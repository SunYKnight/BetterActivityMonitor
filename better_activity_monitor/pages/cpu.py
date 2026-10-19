"""Processor page: per-core stacked utilization, time breakdown, clocks and temperatures."""

from better_activity_monitor.pages.base import Page, chip_of
from better_activity_monitor.util import (
    DASH, fmt_count_rate, fmt_duration, fmt_mhz, fmt_percent, fmt_temp, is_num,
)
from better_activity_monitor.widgets import (
    EvenGrid, Series, StatTile, Value, card, color, core_colors, group, info_row,
)


class CpuPage(Page):
    page_id = "cpu"
    title = "Processor"
    icon = "bam-cpu-symbolic"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        cpu = ctx.inventory.cpu
        count = len(cpu.cpus)
        self.temp_key = cpu.temp_key
        sensors = {sensor.key: sensor for sensor in ctx.inventory.sensors()}
        self.labels = {key: sensor.label for key, sensor in sensors.items()}

        tiles = EvenGrid()
        self.t_usage = StatTile("Utilization")
        self.t_freq = StatTile("Clock speed")
        self.t_temp = StatTile("Temperature")
        self.t_load = StatTile("Load average")
        self.t_procs = StatTile("Processes")
        self.t_uptime = StatTile("Uptime")
        for tile in (self.t_usage, self.t_freq, self.t_temp, self.t_load, self.t_procs, self.t_uptime):
            tiles.append(tile)
        self.content.append(tiles)

        colors = core_colors(count)
        cores = [
            Series(f"cpu.c{number}", f"CPU {number}", colors[index], stack_scale=1.0 / count)
            for index, number in enumerate(cpu.cpus)
        ]
        self.cores = self.chart(cores, fmt_percent, y_max=100, height=240)
        self.total = Value(css_classes=["title-4"])
        legend = self.legend(
            self.cores, lambda series, value: f"{fmt_percent(value)} · {fmt_mhz(ctx.value('cpu.f' + series.key[5:]))}",
            15,
        )
        self.content.append(card("Utilization per logical CPU", self.cores, legend, suffix=self.total))

        grid = EvenGrid(340)
        breakdown = self.chart([
            Series("cpu.user", "User", color(0)), Series("cpu.system", "System", color(3)),
            Series("cpu.nice", "Nice", color(1)), Series("cpu.iowait", "I/O wait", color(4)),
            Series("cpu.irq", "IRQ", color(5)), Series("cpu.steal", "Steal", color(9)),
        ], fmt_percent, min_span=10)
        grid.append(card("Time breakdown", breakdown, self.legend(breakdown)))
        clocks = self.chart([
            Series("cpu.freq", "Average", color(0)), Series("cpu.freq.max", "Fastest core", color(3)),
        ], fmt_mhz, y_max=cpu.max_mhz, stackable=False)
        grid.append(card("Clock speed", clocks, self.legend(clocks)))
        temp_keys = [key for key in [cpu.temp_key, *cpu.extra_temp_keys] if key]
        if temp_keys:
            temps = self.chart([
                Series(key, sensors[key].label, color(index * 3 + 4)) for index, key in enumerate(temp_keys)
            ], fmt_temp, stackable=False, auto_min=True, min_span=20)
            grid.append(card("Temperature", temps, self.legend(temps)))
        self.content.append(grid)

        details = group("Details")
        topology = f"{cpu.sockets} socket{'s' if cpu.sockets > 1 else ''} · {cpu.cores} cores · {cpu.threads} threads"
        rows = [
            ("Model", cpu.model), ("Topology", topology),
            ("Clock range", f"{fmt_mhz(cpu.min_mhz)} – {fmt_mhz(cpu.max_mhz)}" if cpu.max_mhz else DASH),
            ("Frequency driver", cpu.driver or DASH),
            ("Governor", " · ".join(part for part in (cpu.governor, cpu.epp) if part) or DASH),
            ("Boost", DASH if cpu.boost is None else "Enabled" if cpu.boost else "Disabled"),
            *((f"{name} cache", size) for name, size in cpu.caches),
            ("Instruction sets", ", ".join(cpu.features) or DASH),
            ("Microcode", cpu.microcode or DASH),
        ]
        for title, value in rows:
            details.add(info_row(title, value)[0])
        self.live = {}
        for key, title in (("ctxt", "Context switches"), ("intr", "Interrupts"), ("forks", "New processes")):
            row, label = info_row(title)
            details.add(row)
            self.live[key] = label
        self.content.append(details)

    def update(self) -> None:
        value = self.ctx.value
        info = self.ctx.sample.info["cpu"]
        self.total.update(fmt_percent(value("cpu.total")))
        self.t_usage.set(
            fmt_percent(value("cpu.total")),
            f"User {fmt_percent(value('cpu.user'))} · System {fmt_percent(value('cpu.system'))}",
        )
        self.t_freq.set(fmt_mhz(value("cpu.freq")), f"Fastest core {fmt_mhz(value('cpu.freq.max'))}")
        extras = [
            f"{self.labels[key]} {fmt_temp(value(key))}" for key in self.ctx.inventory.cpu.extra_temp_keys
        ]
        self.t_temp.set(fmt_temp(value(self.temp_key)) if self.temp_key else DASH, " · ".join(extras))
        load = info.get("load")
        if load:
            self.t_load.set(f"{load[0]:.2f}", f"5 min {load[1]:.2f} · 15 min {load[2]:.2f}")
        self.t_procs.set(str(info.get("procs", DASH)), f"{info.get('threads', DASH)} threads")
        self.t_uptime.set(fmt_duration(info.get("uptime")))
        for key, label in self.live.items():
            label.update(fmt_count_rate(info.get(key)))

    def demand(self) -> set[str]:
        return {chip for chip in (chip_of(self.temp_key),) if chip}

    def summary(self) -> str:
        value = self.ctx.value
        temp = value(self.temp_key) if self.temp_key else float("nan")
        parts = [fmt_percent(value("cpu.total")), fmt_mhz(value("cpu.freq"))]
        if is_num(temp):
            parts.append(fmt_temp(temp))
        return " · ".join(parts)

    def spark(self) -> tuple:
        return ("cpu.total", color(0), 100.0, 1.0)

