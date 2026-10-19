"""Sensors page: every hwmon channel with session min/max, temperature and fan history, overview pins."""

from gi.repository import Adw, GLib, Gtk

from better_activity_monitor.hwinfo import Sensor
from better_activity_monitor.pages.base import Page
from better_activity_monitor.util import fmt_rpm, fmt_sensor, fmt_temp, is_num
from better_activity_monitor.widgets import Series, Value, card, color, group

NAN = float("nan")


class SensorsPage(Page):
    page_id = "sensors"
    title = "Sensors"
    icon = "temperature-symbolic"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        chips = ctx.inventory.chips
        pinned = set(ctx.pinned())
        self.rows: list[tuple[Sensor, Adw.ActionRow, Value]] = []
        self.subtitles: dict[str, str] = {}
        self.cpu_key = ctx.inventory.cpu.temp_key
        gpu_chip = ctx.inventory.chip_for(ctx.inventory.gpus[0].hwmon) if ctx.inventory.gpus else None
        gpu_temp = gpu_chip.sensor("temp") if gpu_chip else None
        self.gpu_key = gpu_temp.key if gpu_temp else None

        temps = [sensor for chip in chips for sensor in chip.sensors if sensor.kind == "temp"]
        fans = [sensor for chip in chips for sensor in chip.sensors if sensor.kind == "fan"]
        if temps:
            chart = self.chart([
                Series(sensor.key, f"{sensor.chip.title} · {sensor.label}", color(index),
                       visible=sensor.uid in pinned)
                for index, sensor in enumerate(temps)
            ], fmt_temp, stackable=False, auto_min=True, min_span=20, height=220)
            self.content.append(card("Temperatures", chart, self.legend(chart)))
        if fans:
            chart = self.chart([
                Series(sensor.key, f"{sensor.chip.title} · {sensor.label}", color(index + 2))
                for index, sensor in enumerate(fans)
            ], fmt_rpm, stackable=False, min_span=1000, height=160)
            self.content.append(card("Fans", chart, self.legend(chart)))

        container, left, right = self.columns()
        heights = [0, 0]
        for chip in chips:
            chip_group = group(chip.title, f"{chip.name} · {chip.hwmon}")
            main = [sensor for sensor in chip.sensors if sensor.kind in ("temp", "fan", "power")]
            other = [sensor for sensor in chip.sensors if sensor.kind in ("in", "curr")]
            for sensor in main:
                chip_group.add(self._row(sensor, pinned))
            if other:
                expander = Adw.ExpanderRow(title="Voltages and currents", subtitle=f"{len(other)} channels")
                for sensor in other:
                    expander.add_row(self._row(sensor, pinned))
                chip_group.add(expander)
            column = 0 if heights[0] <= heights[1] else 1
            (left, right)[column].append(chip_group)
            heights[column] += len(main) + (1 if other else 0) + 2
        self.content.append(container)

    def _row(self, sensor: Sensor, pinned: set[str]) -> Adw.ActionRow:
        row = Adw.ActionRow(title=GLib.markup_escape_text(sensor.label))
        value = Value()
        row.add_suffix(value)
        pin = Gtk.ToggleButton(icon_name="view-pin-symbolic", valign=Gtk.Align.CENTER, active=sensor.uid in pinned,
                               css_classes=["flat", "pin"], tooltip_text="Pin to the overview page")
        pin.connect("toggled", self._on_pin, sensor)
        row.add_suffix(pin)
        self.rows.append((sensor, row, value))
        return row

    def _on_pin(self, button: Gtk.ToggleButton, sensor: Sensor) -> None:
        pins = [uid for uid in self.ctx.pinned() if uid != sensor.uid]
        if button.get_active():
            pins.append(sensor.uid)
        self.ctx.settings.pinned = pins
        self.ctx.settings.save()
        for callback in self.ctx.pins_changed:
            callback()

    def update(self) -> None:
        ranges = self.ctx.sample.info["sensor_range"]
        for sensor, row, value in self.rows:
            value.update(fmt_sensor(sensor.kind, self.ctx.value(sensor.key)))
            low, high = ranges.get(sensor.key, (NAN, NAN))
            subtitle = f"min {fmt_sensor(sensor.kind, low)} · max {fmt_sensor(sensor.kind, high)}"
            if self.subtitles.get(sensor.key) != subtitle:
                self.subtitles[sensor.key] = subtitle
                row.set_subtitle(subtitle)

    def demand(self) -> set[str]:
        return {chip.hwmon for chip in self.ctx.inventory.chips}

    def summary(self) -> str:
        parts = []
        for name, key in (("CPU", self.cpu_key), ("GPU", self.gpu_key)):
            value = self.ctx.value(key) if key else NAN
            if is_num(value):
                parts.append(f"{name} {fmt_temp(value)}")
        return " · ".join(parts)

    def spark(self) -> tuple | None:
        return (self.cpu_key, color(4), None, 20.0) if self.cpu_key else None
