"""One-shot hardware inventory from sysfs, procfs, the udev database and lspci; needs no root."""

import glob
import os
import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field

from better_activity_monitor.util import link_name, read_int, read_text

SYS_BLOCK = "/sys/block"
SYS_NET = "/sys/class/net"
SYS_HWMON = "/sys/class/hwmon"
SYS_DRM = "/sys/class/drm"
SYS_CPU = "/sys/devices/system/cpu"
SYS_DMI = "/sys/class/dmi/id"
UDEV_DMI = "/run/udev/data/+dmi:id"
USB_IDS = "/usr/share/hwdata/usb.ids"
AMDGPU_IDS = ("/usr/share/libdrm/amdgpu.ids", "/usr/local/share/libdrm/amdgpu.ids")

_PLACEHOLDERS = {
    "", "System manufacturer", "System Product Name", "To Be Filled By O.E.M.", "Default string",
    "Not Specified", "O.E.M.", "Unknown", "System Version", "Type1ProductConfigId",
}
_SENSOR_FILE = re.compile(r"^(temp|fan|in|power|curr)(\d+)_(input|average)$")
_KIND_ORDER = {"temp": 0, "fan": 1, "power": 2, "in": 3, "curr": 4}
_KIND_SCALE = {"temp": 0.001, "fan": 1.0, "in": 0.001, "power": 1e-6, "curr": 0.001}
_CPU_CHIPS = {"k10temp", "coretemp", "zenpower", "cpu_thermal", "x86_pkg_temp", "fam15h_power"}
_GPU_CHIPS = {"amdgpu", "radeon", "nouveau", "i915", "xe"}
_DISK_CHIPS = {"nvme", "drivetemp"}
_CHIP_TITLES = {
    "k10temp": "CPU (k10temp)", "coretemp": "CPU (coretemp)", "zenpower": "CPU (zenpower)",
    "asusec": "ASUS EC", "asus_ec_sensors": "ASUS EC", "asus_wmi_sensors": "ASUS WMI",
    "nzxtsmart2": "NZXT Smart Device", "acpitz": "ACPI thermal zone", "iwlwifi_1": "Wi‑Fi adapter",
}
_NOTABLE_FLAGS = (
    ("sse4_2", "SSE4.2"), ("avx", "AVX"), ("avx2", "AVX2"), ("fma", "FMA3"), ("avx512f", "AVX-512"),
    ("aes", "AES-NI"), ("sha_ni", "SHA"), ("svm", "AMD-V"), ("vmx", "VT-x"),
)
_PCIE_GEN = {"2.5": "1.0", "5.0": "2.0", "8.0": "3.0", "16.0": "4.0", "32.0": "5.0", "64.0": "6.0"}
_VENDOR_NAMES = {
    "Advanced Micro Devices, Inc.": "AMD", "Intel Corporation": "Intel", "Intel Corp.": "Intel",
    "NVIDIA Corporation": "NVIDIA", "Samsung Electronics Co Ltd": "Samsung",
    "Realtek Semiconductor Co., Ltd.": "Realtek", "ASMedia Technology Inc.": "ASMedia",
}


def natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def _clean(text: str | None) -> str | None:
    value = (text or "").strip()
    return None if value in _PLACEHOLDERS else value


@dataclass
class Sensor:
    key: str
    uid: str
    path: str
    kind: str
    label: str
    scale: float
    chip: "Chip" = field(repr=False)


@dataclass
class Chip:
    hwmon: str
    name: str
    title: str
    device: str
    role: str
    sensors: list[Sensor] = field(default_factory=list)

    def sensor(self, kind: str, label: str | None = None) -> Sensor | None:
        matches = [s for s in self.sensors if s.kind == kind and (label is None or s.label.lower() == label.lower())]
        return matches[0] if matches else None


@dataclass
class SystemInfo:
    hostname: str
    os_name: str
    kernel: str
    arch: str
    desktop: str


@dataclass
class BoardInfo:
    vendor: str | None
    name: str | None
    version: str | None
    bios_vendor: str | None
    bios_version: str | None
    bios_date: str | None
    product: str | None


@dataclass
class CpuInfo:
    model: str
    vendor: str
    sockets: int
    cores: int
    threads: int
    cpus: list[int]
    min_mhz: float | None
    max_mhz: float | None
    driver: str | None
    governor: str | None
    epp: str | None
    boost: bool | None
    caches: list[tuple[str, str]]
    features: list[str]
    microcode: str | None
    temp_key: str | None = None
    extra_temp_keys: list[str] = field(default_factory=list)


@dataclass
class Dimm:
    locator: str
    size: int
    kind: str
    speed: int | None
    configured_speed: int | None
    manufacturer: str | None
    part: str | None
    rank: str | None
    form_factor: str | None


@dataclass
class MemoryInfo:
    total: int
    slots: int
    max_capacity: int | None
    dimms: list[Dimm]
    swaps: list[tuple[str, str, int]]


@dataclass
class GpuInfo:
    index: int
    card: str
    path: str
    pci_slot: str
    name: str
    vendor: str
    driver: str
    vram_total: int | None
    vram_vendor: str | None
    vbios: str | None
    link: str | None
    hwmon: str | None
    amd: bool


@dataclass
class DiskInfo:
    name: str
    model: str
    size: int
    kind: str
    firmware: str | None
    removable: bool
    device: str
    temp_key: str | None = None


@dataclass
class NetInfo:
    name: str
    kind: str
    controller: str
    driver: str
    mac: str | None
    wireless: bool


@dataclass
class UsbDevice:
    name: str
    speed: str
    ident: str


@dataclass
class Inventory:
    system: SystemInfo
    board: BoardInfo
    cpu: CpuInfo
    memory: MemoryInfo
    gpus: list[GpuInfo]
    disks: list[DiskInfo]
    nets: list[NetInfo]
    chips: list[Chip]
    audio: list[tuple[str, str]]
    usb: list[UsbDevice]
    pci: list[tuple[str, str]]

    def sensors(self) -> list[Sensor]:
        return [sensor for chip in self.chips for sensor in chip.sensors]

    def chip_for(self, hwmon: str | None) -> Chip | None:
        matches = [chip for chip in self.chips if chip.hwmon == hwmon]
        return matches[0] if matches else None

    def default_pins(self) -> list[str]:
        """CPU package, GPU edge/junction, each drive, the first board sensor per chip and all spinning fans."""
        pins = []
        for chip in self.chips:
            temps = [s for s in chip.sensors if s.kind == "temp"]
            if chip.role == "cpu" and self.cpu.temp_key:
                pins += [s.uid for s in temps if s.key == self.cpu.temp_key]
            elif chip.role == "gpu":
                pins += [s.uid for s in temps if s.label.lower() in ("edge", "junction")] or [s.uid for s in temps[:1]]
            elif chip.role in ("disk", "board"):
                pins += [s.uid for s in temps[:1]]
            pins += [s.uid for s in chip.sensors if s.kind == "fan"]
        return pins


def _lspci() -> dict[str, dict[str, str]]:
    """PCI slot → lspci -vmm fields; empty when pciutils is not installed."""
    devices: dict[str, dict[str, str]] = {}
    if shutil.which("lspci"):
        output = subprocess.run(["lspci", "-vmm", "-D"], capture_output=True, text=True, check=False).stdout
        for block in output.strip().split("\n\n"):
            entry = dict(line.split(":\t", 1) for line in block.splitlines() if ":\t" in line)
            if "Slot" in entry:
                devices[entry["Slot"]] = entry
    return devices


def _short_vendor(vendor: str) -> str:
    vendor = re.sub(r"\s*\[.*]", "", vendor).strip()
    vendor = _VENDOR_NAMES.get(vendor, vendor)
    return re.sub(r",? (Inc|Corporation|Corp|Co\.?,? Ltd|Semiconductor Co\., Ltd|Technology Inc)\.?$", "", vendor)


def _pci_name(pci: dict[str, dict[str, str]], slot: str) -> str:
    entry = pci.get(slot, {})
    device = entry.get("Device", "")
    # GPU entries read "Navi 21 [Radeon RX 6800/6800 XT / 6900 XT]": the bracket holds the product name.
    bracket = re.search(r"\[(.+)]$", device)
    if bracket and re.search(r"VGA|3D|Display", entry.get("Class", "")):
        device = bracket.group(1)
    name = f"{_short_vendor(entry.get('Vendor', ''))} {device}".strip()
    return name or slot


def _system_info() -> SystemInfo:
    os_name = platform.system()
    release = read_text("/etc/os-release") or ""
    match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', release, re.MULTILINE)
    if match:
        os_name = match.group(1)
    desktop = " / ".join(
        part for part in (os.environ.get("XDG_CURRENT_DESKTOP"), os.environ.get("XDG_SESSION_TYPE")) if part
    )
    return SystemInfo(socket.gethostname(), os_name, platform.release(), platform.machine(), desktop or "—")


def _board_info() -> BoardInfo:
    def dmi(name: str) -> str | None:
        return _clean(read_text(f"{SYS_DMI}/{name}"))

    return BoardInfo(
        dmi("board_vendor"), dmi("board_name"), dmi("board_version"), dmi("bios_vendor"), dmi("bios_version"),
        dmi("bios_date"), " ".join(part for part in (dmi("sys_vendor"), dmi("product_name")) if part) or None,
    )


def _parse_cpu_list(text: str | None) -> list[int]:
    cpus = []
    for part in (text or "").split(","):
        if "-" in part:
            low, high = part.split("-")
            cpus += list(range(int(low), int(high) + 1))
        elif part.strip():
            cpus.append(int(part))
    return cpus


def _cpu_caches(cpus: list[int]) -> list[tuple[str, str]]:
    totals: dict[str, list[int]] = {}
    seen = set()
    for cpu in cpus:
        for index in sorted(glob.glob(f"{SYS_CPU}/cpu{cpu}/cache/index[0-9]*")):
            level = read_text(f"{index}/level")
            kind = read_text(f"{index}/type") or ""
            shared = read_text(f"{index}/shared_cpu_list")
            size_text = read_text(f"{index}/size") or "0K"
            ident = (level, kind, shared)
            if ident not in seen:
                seen.add(ident)
                name = f"L{level}" + {"Data": "d", "Instruction": "i"}.get(kind, "")
                size = int(re.sub(r"\D", "", size_text) or 0) * (1024 if size_text.endswith("K") else 1024 * 1024)
                totals.setdefault(name, []).append(size)
    caches = []
    for name in sorted(totals):
        sizes = totals[name]
        total = sum(sizes)
        unit = f"{total // 1048576} MiB" if total >= 1048576 else f"{total // 1024} KiB"
        detail = f" ({len(sizes)} × {sizes[0] // 1024} KiB)" if len(sizes) > 1 and sizes[0] < 1048576 else ""
        detail = f" ({len(sizes)} × {sizes[0] // 1048576} MiB)" if len(sizes) > 1 and sizes[0] >= 1048576 else detail
        caches.append((name, unit + detail))
    return caches


def _cpu_info() -> CpuInfo:
    cpuinfo = read_text("/proc/cpuinfo") or ""
    first = cpuinfo.split("\n\n", 1)[0]
    fields_ = dict(
        (key.strip(), value.strip()) for key, _, value in (line.partition(":") for line in first.splitlines())
    )
    cpus = _parse_cpu_list(read_text(f"{SYS_CPU}/online")) or list(range(os.cpu_count() or 1))
    cores = set()
    packages = set()
    for cpu in cpus:
        package = read_text(f"{SYS_CPU}/cpu{cpu}/topology/physical_package_id")
        packages.add(package)
        cores.add((package, read_text(f"{SYS_CPU}/cpu{cpu}/topology/core_id")))
    freq = f"{SYS_CPU}/cpu{cpus[0]}/cpufreq"
    min_khz = read_int(f"{freq}/cpuinfo_min_freq")
    max_khz = read_int(f"{freq}/cpuinfo_max_freq")
    boost = read_int(f"{SYS_CPU}/cpufreq/boost")
    boost = read_int(f"{freq}/boost") if boost is None else boost
    flags = set(fields_.get("flags", "").split())
    return CpuInfo(
        model=re.sub(r"\s+", " ", fields_.get("model name", platform.processor() or "Unknown CPU")),
        vendor=fields_.get("vendor_id", ""),
        sockets=len(packages),
        cores=len(cores),
        threads=len(cpus),
        cpus=cpus,
        min_mhz=min_khz / 1000 if min_khz else None,
        max_mhz=max_khz / 1000 if max_khz else None,
        driver=read_text(f"{freq}/scaling_driver"),
        governor=read_text(f"{freq}/scaling_governor"),
        epp=read_text(f"{freq}/energy_performance_preference"),
        boost=None if boost is None else bool(boost),
        caches=_cpu_caches(cpus),
        features=[label for flag, label in _NOTABLE_FLAGS if flag in flags],
        microcode=fields_.get("microcode"),
    )


def _memory_info() -> MemoryInfo:
    meminfo = read_text("/proc/meminfo") or ""
    match = re.search(r"^MemTotal:\s+(\d+)", meminfo, re.MULTILINE)
    total = int(match.group(1)) * 1024 if match else 0
    props = {}
    for line in (read_text(UDEV_DMI) or "").splitlines():
        if line.startswith("E:MEMORY_"):
            key, _, value = line[2:].partition("=")
            props[key] = value
    slots = int(props.get("MEMORY_ARRAY_NUM_DEVICES", "0") or 0)
    dimms = []
    for index in range(slots):
        prefix = f"MEMORY_DEVICE_{index}_"
        size = int(props.get(prefix + "SIZE", "0") or 0)
        if size:
            speed = props.get(prefix + "SPEED_MTS")
            configured = props.get(prefix + "CONFIGURED_SPEED_MTS")
            dimms.append(Dimm(
                locator=props.get(prefix + "LOCATOR", f"Slot {index + 1}"),
                size=size,
                kind=props.get(prefix + "TYPE", "RAM"),
                speed=int(speed) if speed else None,
                configured_speed=int(configured) if configured else None,
                manufacturer=_clean(props.get(prefix + "MANUFACTURER")),
                part=_clean(props.get(prefix + "PART_NUMBER")),
                rank=props.get(prefix + "RANK"),
                form_factor=_clean(props.get(prefix + "FORM_FACTOR")),
            ))
    max_capacity = props.get("MEMORY_ARRAY_MAX_CAPACITY")
    swaps = []
    for line in (read_text("/proc/swaps") or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            swaps.append((parts[0], parts[1], int(parts[2]) * 1024))
    return MemoryInfo(total, slots, int(max_capacity) if max_capacity else None, dimms, swaps)


def _amdgpu_name(device_id: int, revision: int) -> str | None:
    wanted = f"{device_id:04X},\t{revision:02X},"
    name = None
    for path in AMDGPU_IDS:
        for line in (read_text(path) or "").splitlines():
            if name is None and line.upper().startswith(wanted):
                name = line.split(",", 2)[2].strip()
    return name


def _pcie_link(device: str) -> str | None:
    def describe(speed: str | None, width: str | None) -> str | None:
        gen = _PCIE_GEN.get((speed or "").split(" ")[0])
        return f"PCIe {gen} ×{width}" if gen and width else None

    current = describe(read_text(f"{device}/current_link_speed"), read_text(f"{device}/current_link_width"))
    maximum = describe(read_text(f"{device}/max_link_speed"), read_text(f"{device}/max_link_width"))
    return f"{current} (max {maximum.removeprefix('PCIe ')})" if current and maximum and current != maximum else current


def _gpus(pci: dict[str, dict[str, str]]) -> list[GpuInfo]:
    gpus = []
    cards = [name for name in os.listdir(SYS_DRM) if re.fullmatch(r"card\d+", name)] if os.path.isdir(SYS_DRM) else []
    for card in sorted(cards, key=natural_key):
        device = os.path.realpath(f"{SYS_DRM}/{card}/device")
        vendor_id = read_int(f"{device}/vendor", 16)
        if vendor_id is not None:
            slot = os.path.basename(device)
            driver = link_name(f"{device}/driver")
            amd = driver == "amdgpu"
            name = None
            if amd:
                name = _amdgpu_name(read_int(f"{device}/device", 16) or 0, read_int(f"{device}/revision", 16) or 0)
            hwmons = sorted(glob.glob(f"{device}/hwmon/hwmon*"), key=natural_key)
            vram = read_int(f"{device}/mem_info_vram_total")
            gpus.append(GpuInfo(
                index=len(gpus),
                card=card,
                path=device,
                pci_slot=slot,
                name=name or _pci_name(pci, slot),
                vendor=pci.get(slot, {}).get("Vendor", f"0x{vendor_id:04x}"),
                driver=driver,
                vram_total=vram,
                vram_vendor=read_text(f"{device}/mem_info_vram_vendor"),
                vbios=read_text(f"{device}/vbios_version"),
                link=_pcie_link(device),
                hwmon=os.path.basename(hwmons[0]) if hwmons else None,
                amd=amd,
            ))
    return gpus


def _disks() -> list[DiskInfo]:
    disks = []
    names = sorted(os.listdir(SYS_BLOCK), key=natural_key) if os.path.isdir(SYS_BLOCK) else []
    for name in names:
        base = f"{SYS_BLOCK}/{name}"
        virtual = name.startswith(("loop", "ram", "zram", "fd", "sr", "nbd"))
        if not virtual and os.path.exists(f"{base}/device"):
            device = os.path.realpath(f"{base}/device")
            rotational = read_int(f"{base}/queue/rotational") == 1
            if name.startswith("nvme"):
                kind = "NVMe SSD"
            elif "/usb" in device:
                kind = "USB drive"
            elif name.startswith("mmcblk"):
                kind = "SD / eMMC"
            elif name.startswith("vd"):
                kind = "Virtual disk"
            else:
                kind = "HDD" if rotational else "SATA SSD"
            model = " ".join(
                part for part in (read_text(f"{base}/device/vendor"), read_text(f"{base}/device/model"))
                if part and part not in ("ATA", "")
            )
            disks.append(DiskInfo(
                name=name,
                model=re.sub(r"\s+", " ", model) or name,
                size=(read_int(f"{base}/size") or 0) * 512,
                kind=kind,
                firmware=read_text(f"{base}/device/firmware_rev") or read_text(f"{base}/device/rev"),
                removable=read_int(f"{base}/removable") == 1,
                device=device,
            ))
    return disks


def _nets(pci: dict[str, dict[str, str]]) -> list[NetInfo]:
    nets = []
    names = sorted(os.listdir(SYS_NET), key=natural_key) if os.path.isdir(SYS_NET) else []
    for name in names:
        base = f"{SYS_NET}/{name}"
        if os.path.exists(f"{base}/device"):
            device = os.path.realpath(f"{base}/device")
            wireless = os.path.isdir(f"{base}/wireless") or os.path.exists(f"{base}/phy80211")
            if wireless:
                kind = "Wi‑Fi"
            elif read_int(f"{base}/type") == 1:
                kind = "Ethernet"
            else:
                kind = "Network"
            slot = os.path.basename(device)
            controller = _pci_name(pci, slot) if slot in pci else read_text(f"{device}/../product") or slot
            nets.append(NetInfo(
                name=name,
                kind=kind,
                controller=controller,
                driver=link_name(f"{device}/driver"),
                mac=read_text(f"{base}/address"),
                wireless=wireless,
            ))
    return nets


def _chip_role(name: str, device: str, gpu_paths: set[str]) -> str:
    if name in _CPU_CHIPS:
        role = "cpu"
    elif name in _GPU_CHIPS or device in gpu_paths:
        role = "gpu"
    elif name in _DISK_CHIPS:
        role = "disk"
    elif name.startswith(("iwlwifi", "mt7", "ath", "rtw")) or os.path.isdir(f"{device}/net"):
        role = "net"
    else:
        role = "board"
    return role


def _chip_title(name: str, device: str, role: str, gpus: list[GpuInfo], disks: list[DiskInfo]) -> str:
    title = _CHIP_TITLES.get(name, name)
    if role == "gpu":
        title = next((gpu.name for gpu in gpus if gpu.path == device), title)
    elif role == "disk":
        title = next((disk.model for disk in disks if disk.device == device), title)
    elif re.fullmatch(r"(nct|it|w83|f7)\w+", name):
        title = f"Super I/O ({name})"
    return title


def _chips(gpus: list[GpuInfo], disks: list[DiskInfo]) -> list[Chip]:
    chips = []
    gpu_paths = {gpu.path for gpu in gpus}
    entries = sorted(os.listdir(SYS_HWMON), key=natural_key) if os.path.isdir(SYS_HWMON) else []
    for entry in entries:
        base = f"{SYS_HWMON}/{entry}"
        name = read_text(f"{base}/name") or entry
        device = os.path.realpath(f"{base}/device") if os.path.exists(f"{base}/device") else ""
        role = _chip_role(name, device, gpu_paths)
        chip = Chip(entry, name, _chip_title(name, device, role, gpus, disks), device, role)
        channels: dict[tuple[str, int], str] = {}
        for filename in os.listdir(base):
            match = _SENSOR_FILE.match(filename)
            if match:
                ident = (match.group(1), int(match.group(2)))
                # Instantaneous *_input beats *_average when a driver exposes both.
                if ident not in channels or match.group(3) == "input":
                    channels[ident] = filename
        ordered = sorted(channels.items(), key=lambda item: (_KIND_ORDER[item[0][0]], item[0][1]))
        for (kind, number), filename in ordered:
            raw = read_int(f"{base}/{filename}")
            value = None if raw is None else raw * _KIND_SCALE[kind]
            if kind == "temp":
                usable = value is not None and -30 < value < 150 and value != 0
            elif kind == "fan":
                usable = value is not None and (value > 0 or role == "gpu")
            else:
                usable = value is not None
            if usable:
                attribute = f"{kind}{number}"
                label = read_text(f"{base}/{attribute}_label") or attribute
                chip.sensors.append(Sensor(
                    key=f"hw.{entry}.{attribute}",
                    uid=f"{name}@{os.path.basename(device)}:{attribute}",
                    path=f"{base}/{filename}",
                    kind=kind,
                    label=label,
                    scale=_KIND_SCALE[kind],
                    chip=chip,
                ))
        if chip.sensors:
            chips.append(chip)
    return chips


def _link_disk_temps(disks: list[DiskInfo], chips: list[Chip]) -> None:
    for disk in disks:
        for chip in chips:
            if chip.role == "disk" and chip.device == disk.device and disk.temp_key is None:
                temps = [s for s in chip.sensors if s.kind == "temp"]
                disk.temp_key = temps[0].key if temps else None


def _link_cpu_temps(cpu: CpuInfo, chips: list[Chip]) -> None:
    temps = [s for chip in chips if chip.role == "cpu" for s in chip.sensors if s.kind == "temp"]
    preferred = ("tdie", "tctl", "package id 0", "cpu")
    ranked = sorted(temps, key=lambda s: preferred.index(s.label.lower()) if s.label.lower() in preferred else 99)
    cpu.temp_key = ranked[0].key if ranked else None
    cpu.extra_temp_keys = [s.key for s in temps if s.label.lower().startswith(("tccd", "core "))]


def _audio() -> list[tuple[str, str]]:
    cards = []
    for match in re.finditer(r"^\s*\d+ \[[^]]+]: (\S+) - (.+)$", read_text("/proc/asound/cards") or "", re.MULTILINE):
        cards.append((match.group(2).strip(), match.group(1)))
    return cards


def _usb_ids_name(vendor_id: str, product_id: str) -> str | None:
    """Name from hwdata's usb.ids for devices whose firmware reports no product string."""
    vendor = None
    product = None
    in_vendor = False
    for line in (read_text(USB_IDS) or "").splitlines():
        if line[:1] not in ("\t", "#", ""):
            in_vendor = line.startswith(vendor_id + "  ")
            vendor = line[6:].strip() if in_vendor else vendor
        elif in_vendor and line.startswith(f"\t{product_id}  ") and product is None:
            product = line[7:].strip()
    return f"{_short_vendor(vendor)} {product}" if vendor and product else None


def _usb() -> list[UsbDevice]:
    devices = []
    speeds = {"1.5": "USB 1.1 (1.5 Mbit/s)", "12": "USB 1.1", "480": "USB 2.0", "5000": "USB 3.2 Gen 1",
              "10000": "USB 3.2 Gen 2", "20000": "USB 3.2 Gen 2×2", "40000": "USB4"}
    for base in sorted(glob.glob("/sys/bus/usb/devices/*"), key=natural_key):
        is_device = os.path.exists(f"{base}/idVendor") and read_text(f"{base}/bDeviceClass") != "09"
        if is_device:
            vendor_id = read_text(f"{base}/idVendor") or ""
            product_id = read_text(f"{base}/idProduct") or ""
            product = read_text(f"{base}/product")
            manufacturer = read_text(f"{base}/manufacturer")
            if product and manufacturer and product.lower().startswith(manufacturer.split()[0].lower()):
                manufacturer = None
            name = " ".join(part for part in (manufacturer, product) if part)
            name = name or _usb_ids_name(vendor_id, product_id) or f"{vendor_id}:{product_id}"
            speed = speeds.get(read_text(f"{base}/speed") or "", "USB")
            devices.append(UsbDevice(name, speed, f"{vendor_id}:{product_id}"))
    return devices


def _pci_list(pci: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    hidden = ("bridge", "non-essential instrumentation")
    return [
        (re.sub(r"\s*\[[0-9a-f]{4}]$", "", entry.get("Class", "")), _pci_name(pci, slot))
        for slot, entry in sorted(pci.items())
        if not any(word in entry.get("Class", "").lower() for word in hidden)
    ]


def discover() -> Inventory:
    pci = _lspci()
    cpu = _cpu_info()
    gpus = _gpus(pci)
    disks = _disks()
    chips = _chips(gpus, disks)
    _link_disk_temps(disks, chips)
    _link_cpu_temps(cpu, chips)
    return Inventory(
        system=_system_info(),
        board=_board_info(),
        cpu=cpu,
        memory=_memory_info(),
        gpus=gpus,
        disks=disks,
        nets=_nets(pci),
        chips=chips,
        audio=_audio(),
        usb=_usb(),
        pci=_pci_list(pci),
    )
