"""Background sampling of procfs/sysfs counters into flat per-tick samples."""

import os
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass

from gi.repository import GLib

from better_activity_monitor.hwinfo import SYS_DRM, SYS_HWMON, Inventory
from better_activity_monitor.netaddr import interface_addresses
from better_activity_monitor.util import NAN, PolledFiles, read_int, read_text

# Mount tables, addresses and link speeds rarely change; refreshing them every few seconds keeps ticks cheap.
SLOW_PERIOD = 5.0
DEMAND_PERIOD = 2.0
IDLE_PERIOD = 10.0
SLOW_CHIP_SECONDS = 0.004
CPU_BUDGET = 0.01
SECTOR = 512


@dataclass
class FsUsage:
    source: str
    fstype: str
    mounts: list[str]
    total: int
    used: int
    available: int


@dataclass
class Sample:
    t: float
    values: dict[str, float]
    info: dict


def _unescape(text: str) -> str:
    return re.sub(r"\\(\d{3})", lambda match: chr(int(match.group(1), 8)), text)


class Collector:
    """Reads one sample per call; keeps previous counters to turn them into rates."""

    def __init__(self, inventory: Inventory) -> None:
        self.inv = inventory
        self.files = PolledFiles()
        self.disk_names = {disk.name for disk in inventory.disks}
        self.net_names = [net.name for net in inventory.nets]
        self.freq_paths = [
            (cpu, f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq") for cpu in inventory.cpu.cpus
        ]
        self.prev_t: float | None = None
        self.prev_cpu: dict[str, list[int]] = {}
        self.prev_counters: dict[str, float] = {}
        self.last_slow = float("-inf")
        self.slow: dict = {"fs": {}, "addresses": {}, "route": (None, None), "speeds": {}, "gpus": {}}
        self.sensor_range: dict[str, tuple[float, float]] = {}
        # hwmon dirs currently on screen; replaced wholesale by the GTK thread, so reads need no lock.
        self.demand: frozenset[str] = frozenset()
        self.slow_chips: set[str] = set()
        self.chip_read: dict[str, float] = {}
        self.chip_cost: dict[str, float] = {}
        self.held: dict[str, float] = {}

    def close(self) -> None:
        self.files.close()

    def sample(self) -> Sample:
        now = time.monotonic()
        dt = now - self.prev_t if self.prev_t is not None else 0.0
        slow = now - self.last_slow >= SLOW_PERIOD
        if slow:
            self.last_slow = now
            self._refresh_slow()
        values: dict[str, float] = {}
        info: dict = {}
        self._cpu(values, info, dt)
        self._memory(values, info)
        self._disks(values, info, dt)
        self._nets(values, info, dt)
        suspended = self._gpus(values, info)
        self._sensors(values, suspended, now)
        info["sensor_range"] = dict(self.sensor_range)
        self.prev_t = now
        return Sample(now, values, info)

    def _rate(self, key: str, counter: float, dt: float) -> float:
        previous = self.prev_counters.get(key)
        self.prev_counters[key] = counter
        result = NAN
        if previous is not None and dt > 0 and counter >= previous:
            result = (counter - previous) / dt
        return result

    # CPU -----------------------------------------------------------------------------------------------------

    def _cpu(self, values: dict, info: dict, dt: float) -> None:
        details: dict = {}
        for line in (self.files.read("/proc/stat") or "").splitlines():
            name, _, rest = line.partition(" ")
            if name.startswith("cpu"):
                self._cpu_line(name, [int(part) for part in rest.split()], values)
            elif name == "ctxt":
                details["ctxt"] = self._rate("ctxt", int(rest), dt)
            elif name == "intr":
                details["intr"] = self._rate("intr", int(rest.split(" ", 1)[0]), dt)
            elif name == "processes":
                details["forks"] = self._rate("forks", int(rest), dt)
        freqs = []
        for cpu, path in self.freq_paths:
            mhz = self.files.read_float(path, 0.001)
            values[f"cpu.f{cpu}"] = mhz
            if mhz == mhz:
                freqs.append(mhz)
        values["cpu.freq"] = sum(freqs) / len(freqs) if freqs else NAN
        values["cpu.freq.max"] = max(freqs) if freqs else NAN
        load = (self.files.read("/proc/loadavg") or "").split()
        if len(load) >= 4:
            details["load"] = (float(load[0]), float(load[1]), float(load[2]))
            details["threads"] = int(load[3].partition("/")[2])
        details["procs"] = sum(1 for entry in os.scandir("/proc") if entry.name.isdigit())
        uptime = (self.files.read("/proc/uptime") or "").split()
        details["uptime"] = float(uptime[0]) if uptime else NAN
        info["cpu"] = details

    def _cpu_line(self, name: str, fields: list[int], values: dict) -> None:
        # user nice system idle iowait irq softirq steal; guest time is already contained in user and nice.
        fields = (fields + [0] * 8)[:8]
        previous = self.prev_cpu.get(name)
        self.prev_cpu[name] = fields
        delta = [now - before for now, before in zip(fields, previous)] if previous else []
        total = sum(delta)
        if total > 0:
            busy = 100.0 * (total - delta[3] - delta[4]) / total
            if name == "cpu":
                values["cpu.total"] = busy
                values["cpu.user"] = 100.0 * delta[0] / total
                values["cpu.nice"] = 100.0 * delta[1] / total
                values["cpu.system"] = 100.0 * delta[2] / total
                values["cpu.iowait"] = 100.0 * delta[4] / total
                values["cpu.irq"] = 100.0 * (delta[5] + delta[6]) / total
                values["cpu.steal"] = 100.0 * delta[7] / total
            else:
                values[f"cpu.c{name[3:]}"] = busy

    # Memory --------------------------------------------------------------------------------------------------

    def _memory(self, values: dict, info: dict) -> None:
        mem: dict[str, int] = {}
        for line in (self.files.read("/proc/meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                mem[key] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
        total = mem.get("MemTotal", 0)
        free = mem.get("MemFree", 0)
        available = mem.get("MemAvailable", free)
        values["mem.used"] = float(total - available)
        values["mem.cache"] = float(max(0, available - free))
        values["mem.pct"] = 100.0 * (total - available) / total if total else NAN
        values["mem.swap"] = float(mem.get("SwapTotal", 0) - mem.get("SwapFree", 0))
        info["mem"] = mem

    # Disks ---------------------------------------------------------------------------------------------------

    def _disks(self, values: dict, info: dict, dt: float) -> None:
        details: dict = {}
        for line in (self.files.read("/proc/diskstats") or "").splitlines():
            parts = line.split()
            if len(parts) >= 14 and parts[2] in self.disk_names:
                name = parts[2]
                reads, read_bytes = int(parts[3]), int(parts[5]) * SECTOR
                writes, write_bytes = int(parts[7]), int(parts[9]) * SECTOR
                values[f"disk.{name}.r"] = self._rate(f"disk.{name}.r", read_bytes, dt)
                values[f"disk.{name}.w"] = self._rate(f"disk.{name}.w", write_bytes, dt)
                # io_ticks counts milliseconds with at least one request in flight.
                values[f"disk.{name}.busy"] = min(self._rate(f"disk.{name}.t", int(parts[12]), dt) / 10.0, 100.0)
                details[name] = {
                    "read_total": read_bytes,
                    "write_total": write_bytes,
                    "read_iops": self._rate(f"disk.{name}.ro", reads, dt),
                    "write_iops": self._rate(f"disk.{name}.wo", writes, dt),
                }
        values["disk.r"] = sum(values[f"disk.{name}.r"] for name in details) if details else NAN
        values["disk.w"] = sum(values[f"disk.{name}.w"] for name in details) if details else NAN
        values["disk.total"] = values["disk.r"] + values["disk.w"]
        info["disks"] = details
        info["fs"] = self.slow["fs"]

    def _filesystems(self) -> dict[str, list[FsUsage]]:
        grouped: dict[str, FsUsage] = {}
        for line in (read_text("/proc/self/mountinfo") or "").splitlines():
            left, _, right = line.partition(" - ")
            left_parts, right_parts = left.split(), right.split()
            if len(left_parts) >= 5 and len(right_parts) >= 2 and right_parts[1].startswith("/dev/"):
                mount, fstype, source = _unescape(left_parts[4]), right_parts[0], right_parts[1]
                if source in grouped:
                    grouped[source].mounts.append(mount)
                else:
                    stat = os.statvfs(mount)
                    grouped[source] = FsUsage(
                        source, fstype, [mount], stat.f_blocks * stat.f_frsize,
                        (stat.f_blocks - stat.f_bfree) * stat.f_frsize, stat.f_bavail * stat.f_frsize,
                    )
        result: dict[str, list[FsUsage]] = {}
        for usage in grouped.values():
            usage.mounts.sort(key=len)
            disk = self._parent_disk(os.path.basename(os.path.realpath(usage.source)), 0)
            if disk:
                result.setdefault(disk, []).append(usage)
        return result

    def _parent_disk(self, name: str, depth: int) -> str | None:
        """Walks partition → disk and device-mapper → backing device links in /sys/class/block."""
        block = f"/sys/class/block/{name}"
        result = None
        if name in self.disk_names:
            result = name
        elif depth < 8 and os.path.exists(f"{block}/partition"):
            result = self._parent_disk(os.path.basename(os.path.dirname(os.path.realpath(block))), depth + 1)
        elif depth < 8 and os.path.isdir(f"{block}/slaves"):
            slaves = sorted(os.listdir(f"{block}/slaves"))
            result = self._parent_disk(slaves[0], depth + 1) if slaves else None
        return result

    # Network -------------------------------------------------------------------------------------------------

    def _nets(self, values: dict, info: dict, dt: float) -> None:
        counters: dict[str, tuple[int, int]] = {}
        for line in (self.files.read("/proc/net/dev") or "").splitlines()[2:]:
            name, _, rest = line.partition(":")
            parts = rest.split()
            if name.strip() in self.net_names and len(parts) >= 9:
                counters[name.strip()] = (int(parts[0]), int(parts[8]))
        signals = {}
        for line in (self.files.read("/proc/net/wireless") or "").splitlines()[2:]:
            name, _, rest = line.partition(":")
            parts = rest.split()
            if len(parts) >= 3:
                signals[name.strip()] = float(parts[2].rstrip("."))
        route_iface, gateway = self.slow["route"]
        details: dict = {}
        for name in self.net_names:
            rx_total, tx_total = counters.get(name, (NAN, NAN))
            values[f"net.{name}.rx"] = self._rate(f"net.{name}.rx", rx_total, dt)
            values[f"net.{name}.tx"] = self._rate(f"net.{name}.tx", tx_total, dt)
            details[name] = {
                "rx_total": rx_total,
                "tx_total": tx_total,
                "state": (self.files.read(f"/sys/class/net/{name}/operstate") or "unknown").strip(),
                "speed": self.slow["speeds"].get(name),
                "addresses": self.slow["addresses"].get(name, []),
                "signal": signals.get(name),
                "gateway": gateway if name == route_iface else None,
            }
        values["net.rx"] = sum(values[f"net.{name}.rx"] for name in self.net_names) if self.net_names else NAN
        values["net.tx"] = sum(values[f"net.{name}.tx"] for name in self.net_names) if self.net_names else NAN
        values["net.total"] = values["net.rx"] + values["net.tx"]
        info["nets"] = details

    @staticmethod
    def _default_route() -> tuple[str | None, str | None]:
        result = (None, None)
        for line in (read_text("/proc/net/route") or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "00000000" and result[0] is None:
                result = (parts[0], socket.inet_ntoa(struct.pack("<I", int(parts[2], 16))))
        return result

    # GPU -----------------------------------------------------------------------------------------------------

    def _gpus(self, values: dict, info: dict) -> set[str]:
        """Returns hwmon dirs of runtime-suspended GPUs: reading their sensors would wake them up."""
        suspended = set()
        details: dict = {}
        for gpu in self.inv.gpus:
            prefix = f"gpu{gpu.index}"
            asleep = (self.files.read(f"{gpu.path}/power/runtime_status") or "").strip() == "suspended"
            if asleep and gpu.hwmon:
                suspended.add(gpu.hwmon)
            if gpu.amd and not asleep:
                values[f"{prefix}.busy"] = self.files.read_float(f"{gpu.path}/gpu_busy_percent")
                values[f"{prefix}.membusy"] = self.files.read_float(f"{gpu.path}/mem_busy_percent")
                values[f"{prefix}.vram"] = self.files.read_float(f"{gpu.path}/mem_info_vram_used")
                values[f"{prefix}.gtt"] = self.files.read_float(f"{gpu.path}/mem_info_gtt_used")
            if gpu.hwmon and not asleep:
                values[f"{prefix}.sclk"] = self.files.read_float(f"{SYS_HWMON}/{gpu.hwmon}/freq1_input", 1e-6)
                values[f"{prefix}.mclk"] = self.files.read_float(f"{SYS_HWMON}/{gpu.hwmon}/freq2_input", 1e-6)
            if gpu.driver in ("i915", "xe") and not asleep:
                values[f"{prefix}.sclk"] = self.files.read_float(f"{SYS_DRM}/{gpu.card}/gt_cur_freq_mhz")
            details[gpu.index] = dict(self.slow["gpus"].get(gpu.index, {}), suspended=asleep)
        info["gpus"] = details
        return suspended

    # Sensors -------------------------------------------------------------------------------------------------

    def _sensors(self, values: dict, suspended: set[str], now: float) -> None:
        """Slow chips are read every DEMAND_PERIOD while shown and every IDLE_PERIOD otherwise.

        Both periods stretch with the CPU a chip costs per read, capping it near CPU_BUDGET of one core: the nct6798
        Super I/O chip here spends ~70 ms of kernel CPU per refresh. EC and NVMe sensors mostly block (up to 50 ms).
        Between reads the last value is held so history charts stay continuous.
        """
        demand = self.demand
        for chip in self.inv.chips:
            elapsed = now - self.chip_read.get(chip.hwmon, float("-inf"))
            stretched = self.chip_cost.get(chip.hwmon, 0.0) / CPU_BUDGET
            due = chip.hwmon not in self.slow_chips or elapsed >= max(IDLE_PERIOD, stretched)
            due = due or (chip.hwmon in demand and elapsed >= max(DEMAND_PERIOD, stretched))
            if due and chip.hwmon not in suspended:
                started, started_cpu = time.perf_counter(), time.thread_time()
                for sensor in chip.sensors:
                    self.held[sensor.key] = self._read_sensor(sensor)
                if time.perf_counter() - started > SLOW_CHIP_SECONDS:
                    self.slow_chips.add(chip.hwmon)
                    self.chip_cost[chip.hwmon] = max(self.chip_cost.get(chip.hwmon, 0.0),
                                                     time.thread_time() - started_cpu)
                self.chip_read[chip.hwmon] = now
            for sensor in chip.sensors:
                values[sensor.key] = NAN if chip.hwmon in suspended else self.held.get(sensor.key, NAN)

    def _read_sensor(self, sensor) -> float:
        value = self.files.read_float(sensor.path, sensor.scale)
        if sensor.kind == "temp" and not -30 < value < 150:
            value = NAN
        if value == value:
            low, high = self.sensor_range.get(sensor.key, (value, value))
            self.sensor_range[sensor.key] = (min(low, value), max(high, value))
        return value

    # Slow-changing details -----------------------------------------------------------------------------------

    def _refresh_slow(self) -> None:
        speeds = {}
        for name in self.net_names:
            speed = read_int(f"/sys/class/net/{name}/speed")
            speeds[name] = speed if speed and speed > 0 else None
        gpus = {}
        for gpu in self.inv.gpus:
            cap = read_int(f"{SYS_HWMON}/{gpu.hwmon}/power1_cap") if gpu.hwmon else None
            gpus[gpu.index] = {
                "power_cap": cap / 1e6 if cap else None,
                "perf_level": read_text(f"{gpu.path}/power_dpm_force_performance_level"),
                "gtt_total": read_int(f"{gpu.path}/mem_info_gtt_total"),
            }
        self.slow = {
            "fs": self._filesystems(),
            "addresses": interface_addresses(),
            "route": self._default_route(),
            "speeds": speeds,
            "gpus": gpus,
        }


class Sampler:
    """Runs the collector on a worker thread.

    Embedded-controller sensors (asus-ec-sensors) block for ~50 ms per refresh; on the GTK main loop that would
    stall input and redraws once per tick.
    """

    def __init__(self, collector: Collector, interval: float, deliver) -> None:
        self.collector = collector
        self.interval = interval
        self._deliver = deliver
        self._wake = threading.Event()
        self._running = True
        self._thread = threading.Thread(target=self._run, name="sampler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def set_interval(self, interval: float) -> None:
        self.interval = interval
        self._wake.set()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        due = time.monotonic()
        while self._running:
            sample = self.collector.sample()
            GLib.idle_add(self._post, sample)
            due = max(due + self.interval, time.monotonic())
            self._wake.wait(due - time.monotonic())
            if self._wake.is_set():
                self._wake.clear()
                due = time.monotonic()
        self.collector.close()

    def _post(self, sample: Sample) -> bool:
        if self._running:
            self._deliver(sample)
        return GLib.SOURCE_REMOVE
