"""Graphics page for one GPU: load, video memory, temperatures, power and clocks."""

from better_activity_monitor.hwinfo import GpuInfo
from better_activity_monitor.pages.base import Page
from better_activity_monitor.util import (
    DASH, fmt_bytes, fmt_mhz, fmt_percent, fmt_rpm, fmt_temp, fmt_watts, is_num,
)
from better_activity_monitor.widgets import EvenGrid, Series, StatTile, card, color, group, info_row

NAN = float("nan")


class GpuPage(Page):
    icon = "bam-gpu-symbolic"

    def __init__(self, ctx, gpu: GpuInfo) -> None:
        super().__init__(ctx)
        self.gpu = gpu
        self.page_id = f"gpu{gpu.index}"
        self.title = "Graphics" if len(ctx.inventory.gpus) == 1 else f"Graphics {gpu.index + 1}"
        self.prefix = f"gpu{gpu.index}"
        chip = ctx.inventory.chip_for(gpu.hwmon)
        self.temps = [sensor for sensor in chip.sensors if sensor.kind == "temp"] if chip else []
        fan = chip.sensor("fan") if chip else None
        power = chip.sensor("power") if chip else None
        self.fan_key = fan.key if fan else None
        self.power_key = power.key if power else None
        prefix = self.prefix

        tiles = EvenGrid()
        self.t_busy = StatTile("Utilization")
        self.t_vram = StatTile("Video memory")
        self.t_temp = StatTile("Temperature")
        self.t_power = StatTile("Power")
        self.t_clock = StatTile("Core clock")
        self.t_fan = StatTile("Fan")
        for tile in (self.t_busy, self.t_vram, self.t_temp, self.t_power, self.t_clock, self.t_fan):
            tiles.append(tile)
        self.content.append(tiles)

        grid = EvenGrid(340)
        if gpu.amd:
            load = self.chart([
                Series(f"{prefix}.busy", "Graphics engine", color(0)),
                Series(f"{prefix}.membusy", "Memory controller", color(5)),
            ], fmt_percent, y_max=100, stackable=False)
            grid.append(card("Utilization", load, self.legend(load)))
            vram = self.chart([
                Series(f"{prefix}.vram", "VRAM used", color(6)),
            ], lambda value: fmt_bytes(value, binary=True), y_max=float(gpu.vram_total or 0) or None, stackable=False)
            grid.append(card("Video memory", vram, self.legend(vram)))
        if self.temps:
            temps = self.chart([
                Series(sensor.key, sensor.label.capitalize(), color(index + 3))
                for index, sensor in enumerate(self.temps)
            ], fmt_temp, stackable=False, auto_min=True, min_span=20)
            grid.append(card("Temperatures", temps, self.legend(temps)))
        if self.power_key:
            self.power_chart = self.chart([Series(self.power_key, "Package power", color(4))], fmt_watts,
                                          stackable=False, min_span=50)
            grid.append(card("Power", self.power_chart, self.legend(self.power_chart)))
        if gpu.hwmon or gpu.driver in ("i915", "xe"):
            clocks = self.chart([
                Series(f"{prefix}.sclk", "Core", color(0)), Series(f"{prefix}.mclk", "Memory", color(1)),
            ], fmt_mhz, stackable=False, min_span=500)
            grid.append(card("Clocks", clocks, self.legend(clocks)))
        self.content.append(grid)

        details = group("Details")
        for title, value in (
            ("Name", gpu.name), ("Vendor", gpu.vendor), ("Driver", gpu.driver or DASH),
            ("PCI slot", gpu.pci_slot), ("PCIe link", gpu.link or DASH), ("VBIOS", gpu.vbios or DASH),
            ("Video memory", " · ".join(part for part in (
                fmt_bytes(gpu.vram_total, binary=True) if gpu.vram_total else None,
                gpu.vram_vendor.capitalize() if gpu.vram_vendor else None) if part) or DASH),
        ):
            details.add(info_row(title, value)[0])
        self.live = {}
        for key, title in (("gtt", "GTT (system memory mapped)"), ("cap", "Power limit"),
                           ("perf", "Performance level")):
            row, label = info_row(title)
            details.add(row)
            self.live[key] = label
        self.content.append(details)

    def update(self) -> None:
        value = self.ctx.value
        prefix = self.prefix
        info = self.ctx.sample.info["gpus"].get(self.gpu.index, {})
        asleep = info.get("suspended")
        self.t_busy.set("Asleep" if asleep else fmt_percent(value(f"{prefix}.busy")),
                        f"Memory controller {fmt_percent(value(f'{prefix}.membusy'))}" if self.gpu.amd else "")
        vram = value(f"{prefix}.vram")
        total = self.gpu.vram_total
        self.t_vram.set(fmt_bytes(vram, binary=True),
                        f"of {fmt_bytes(total, binary=True)} · {fmt_percent(100 * vram / total)}"
                        if total and is_num(vram) else "")
        if self.temps:
            main = self.temps[0]
            self.t_temp.set(fmt_temp(value(main.key)), " · ".join(
                f"{sensor.label.capitalize()} {fmt_temp(value(sensor.key))}" for sensor in self.temps[1:]
            ))
        cap = info.get("power_cap")
        self.t_power.set(fmt_watts(value(self.power_key)) if self.power_key else DASH,
                         f"Limit {fmt_watts(cap)}" if cap else "")
        if self.power_key and cap:
            self.power_chart.y_max = cap
        self.t_clock.set(fmt_mhz(value(f"{prefix}.sclk")), f"Memory {fmt_mhz(value(f'{prefix}.mclk'))}")
        self.t_fan.set(fmt_rpm(value(self.fan_key)) if self.fan_key else DASH)
        gtt_total = info.get("gtt_total")
        self.live["gtt"].update(
            f"{fmt_bytes(value(f'{prefix}.gtt'), binary=True)} of {fmt_bytes(gtt_total, binary=True)}"
            if gtt_total else DASH
        )
        self.live["cap"].update(fmt_watts(cap) if cap else DASH)
        self.live["perf"].update(info.get("perf_level") or DASH)

    def demand(self) -> set[str]:
        return {self.gpu.hwmon} if self.gpu.hwmon else set()

    def summary(self) -> str:
        temp = self.ctx.value(self.temps[0].key) if self.temps else NAN
        parts = [fmt_percent(self.ctx.value(f"{self.prefix}.busy"))]
        if is_num(temp):
            parts.append(fmt_temp(temp))
        return " · ".join(parts)

    def spark(self) -> tuple:
        return (f"{self.prefix}.busy", color(5), 100.0, 1.0)
