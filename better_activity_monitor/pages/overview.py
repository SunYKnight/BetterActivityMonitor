"""Overview page: the complete hardware inventory with live load, temperatures and pinned sensors."""

import math

from gi.repository import Adw, Gdk, GLib, Gtk

from better_activity_monitor.pages.base import Page, chip_of
from better_activity_monitor.pages.network import link_speed
from better_activity_monitor.util import (
    DASH, fmt_bytes, fmt_duration, fmt_mhz, fmt_percent, fmt_rate, fmt_rpm, fmt_sensor, fmt_temp, fmt_watts,
    is_num,
)
from better_activity_monitor.widgets import EvenGrid, StatTile, group, info_row


def gib(value) -> str:
    return fmt_bytes(value, binary=True)


def _expander(title: str, rows: list[tuple[str, str]]) -> Adw.ExpanderRow:
    expander = Adw.ExpanderRow(title=GLib.markup_escape_text(title), subtitle=f"{len(rows)} devices")
    for name, detail in rows:
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=GLib.markup_escape_text(detail))
        row.set_title_lines(2)
        expander.add_row(row)
    return expander


class OverviewPage(Page):
    page_id = "overview"
    title = "Overview"
    icon = "computer-symbolic"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        inv = ctx.inventory
        self.live: list[tuple] = []
        self.pinned_rows: list[Adw.ActionRow] = []
        self.pinned_live: list[tuple] = []
        self.display_rows: list[Adw.ActionRow] = []
        board = " ".join(part for part in (inv.board.vendor, inv.board.name) if part) or inv.board.product
        self.board_name = board or inv.system.hostname

        hero = Gtk.Box(spacing=18)
        hero.append(Gtk.Image(icon_name="computer-symbolic", pixel_size=64, css_classes=["dim-label"]))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        text.append(Gtk.Label(label=self.board_name, xalign=0, wrap=True, css_classes=["title-1"]))
        text.append(Gtk.Label(
            label=f"{inv.system.hostname} · {inv.system.os_name} · Linux {inv.system.kernel}", xalign=0, wrap=True,
            css_classes=["dim-label"],
        ))
        hero.append(text)
        self.content.append(hero)

        tiles = EvenGrid()
        self.t_cpu = StatTile("Processor")
        self.t_mem = StatTile("Memory")
        self.t_gpu = StatTile("Graphics")
        self.t_disk = StatTile("Storage I/O")
        self.t_net = StatTile("Network")
        self.t_up = StatTile("Uptime")
        for tile in (self.t_cpu, self.t_mem, self.t_gpu, self.t_disk, self.t_net, self.t_up):
            tiles.append(tile)
        self.content.append(tiles)

        container, left, right = self.columns()
        left.append(self._system())
        left.append(self._board())
        left.append(self._processor())
        left.append(self._memory())
        for gpu in inv.gpus:
            left.append(self._graphics(gpu))
        self.sensor_group = group("Temperatures and fans", "Pin more sensors on the Sensors page")
        right.append(self.sensor_group)
        self._build_pinned()
        right.append(self._storage())
        self.display_group = group("Displays")
        right.append(self.display_group)
        right.append(self._network())
        right.append(self._devices())
        self.content.append(container)
        ctx.pins_changed.append(self._build_pinned)
        monitors = Gdk.Display.get_default().get_monitors()
        monitors.connect("items-changed", lambda *_args: self._build_displays())
        self._build_displays()

    def _add_live(self, target: Adw.PreferencesGroup, title: str, text, subtitle: str | None = None) -> None:
        row, label = info_row(title, subtitle=subtitle)
        target.add(row)
        self.live.append((label, text))

    def _system(self) -> Adw.PreferencesGroup:
        system = self.ctx.inventory.system
        result = group("System")
        for title, value in (("Hostname", system.hostname), ("Operating system", system.os_name),
                             ("Kernel", f"{system.kernel} ({system.arch})"), ("Desktop", system.desktop)):
            result.add(info_row(title, value)[0])
        self._add_live(result, "Uptime", lambda: fmt_duration(self.ctx.sample.info["cpu"].get("uptime")))
        return result

    def _board(self) -> Adw.PreferencesGroup:
        board = self.ctx.inventory.board
        result = group("Mainboard")
        rows = (
            ("Model", " ".join(part for part in (board.vendor, board.name) if part)),
            ("Revision", board.version), ("System", board.product),
            ("BIOS", " ".join(part for part in (board.bios_vendor, board.bios_version) if part)),
            ("BIOS date", board.bios_date),
        )
        for title, value in rows:
            if value:
                result.add(info_row(title, value)[0])
        return result

    def _processor(self) -> Adw.PreferencesGroup:
        cpu = self.ctx.inventory.cpu
        value = self.ctx.value
        result = group("Processor")
        result.add(info_row("Model", cpu.model)[0])
        result.add(info_row("Cores", f"{cpu.cores} cores · {cpu.threads} threads")[0])
        if cpu.max_mhz:
            result.add(info_row("Clock range", f"{fmt_mhz(cpu.min_mhz)} – {fmt_mhz(cpu.max_mhz)}")[0])
        caches = [f"{name} {size.split(' (')[0]}" for name, size in cpu.caches if name in ("L2", "L3")]
        if caches:
            result.add(info_row("Cache", " · ".join(caches))[0])
        self._add_live(result, "Load", lambda: f"{fmt_percent(value('cpu.total'))} at {fmt_mhz(value('cpu.freq'))}")
        if cpu.temp_key:
            self._add_live(result, "Temperature", lambda: fmt_temp(value(cpu.temp_key)))
        return result

    def _memory(self) -> Adw.PreferencesGroup:
        memory = self.ctx.inventory.memory
        value = self.ctx.value
        result = group("Memory")
        result.add(info_row("Installed", gib(memory.total) + (
            f" · {len(memory.dimms)} of {memory.slots} slots" if memory.slots else ""))[0])
        for dimm in memory.dimms:
            speed = dimm.configured_speed or dimm.speed
            detail = " ".join(part for part in (dimm.manufacturer, dimm.part) if part)
            result.add(info_row(dimm.locator, f"{gib(dimm.size)} {dimm.kind}{f'-{speed}' if speed else ''}",
                                detail or None)[0])
        self._add_live(result, "In use", lambda: f"{gib(value('mem.used'))} ({fmt_percent(value('mem.pct'))})")
        return result

    def _graphics(self, gpu) -> Adw.PreferencesGroup:
        value = self.ctx.value
        chip = self.ctx.inventory.chip_for(gpu.hwmon)
        result = group("Graphics")
        result.add(info_row("Model", gpu.name)[0])
        if gpu.vram_total:
            result.add(info_row("Video memory", gib(gpu.vram_total))[0])
        result.add(info_row("Driver", " · ".join(part for part in (gpu.driver, gpu.link) if part) or DASH)[0])
        prefix = f"gpu{gpu.index}"
        if gpu.amd:
            self._add_live(result, "Load", lambda: f"{fmt_percent(value(prefix + '.busy'))} · VRAM "
                                                   f"{gib(value(prefix + '.vram'))}")
        temps = [sensor for sensor in chip.sensors if sensor.kind == "temp"] if chip else []
        if temps:
            self._add_live(result, "Temperature", lambda: " · ".join(
                f"{sensor.label.capitalize()} {fmt_temp(value(sensor.key))}" for sensor in temps))
        power = chip.sensor("power") if chip else None
        fan = chip.sensor("fan") if chip else None
        if power or fan:
            self._add_live(result, "Power and fan", lambda: " · ".join(part for part in (
                fmt_watts(value(power.key)) if power else None, fmt_rpm(value(fan.key)) if fan else None) if part))
        return result

    def _storage(self) -> Adw.PreferencesGroup:
        value = self.ctx.value
        result = group("Storage")
        for disk in self.ctx.inventory.disks:
            subtitle = f"{disk.kind} · {disk.name}"
            if disk.temp_key:
                key = disk.temp_key
                self._add_live(result, disk.model, lambda size=disk.size, key=key:
                               f"{fmt_bytes(size)} · {fmt_temp(value(key))}", subtitle)
            else:
                result.add(info_row(disk.model, fmt_bytes(disk.size), subtitle)[0])
        return result

    def _network(self) -> Adw.PreferencesGroup:
        result = group("Network")
        for net in self.ctx.inventory.nets:
            self._add_live(result, f"{net.name} · {net.kind}", lambda name=net.name: self._net_text(name),
                           net.controller)
        return result

    def _net_text(self, name: str) -> str:
        details = self.ctx.sample.info["nets"].get(name, {})
        text = "Disconnected"
        if details.get("state") == "up":
            addresses = [address.split("/")[0] for address in details.get("addresses", []) if ":" not in address]
            text = " · ".join(part for part in (link_speed(details.get("speed")), *addresses[:1]) if part != DASH)
            text = text or "Connected"
        return text

    def _devices(self) -> Adw.PreferencesGroup:
        inv = self.ctx.inventory
        result = group("Other devices")
        for name, driver in inv.audio:
            result.add(info_row(name, driver, "Audio")[0])
        if inv.usb:
            result.add(_expander("USB devices", [(device.name, f"{device.speed} · {device.ident}")
                                                  for device in inv.usb]))
        if inv.pci:
            result.add(_expander("PCI devices", [(name, kind) for kind, name in inv.pci]))
        return result

    def _build_pinned(self) -> None:
        for row in self.pinned_rows:
            self.sensor_group.remove(row)
        self.pinned_rows = []
        self.pinned_live = []
        sensors = {sensor.uid: sensor for sensor in self.ctx.inventory.sensors()}
        for uid in self.ctx.pinned():
            sensor = sensors.get(uid)
            if sensor:
                row, label = info_row(sensor.chip.title, subtitle=sensor.label)
                self.sensor_group.add(row)
                self.pinned_rows.append(row)
                self.pinned_live.append((label, sensor))
        if self.ctx.sample is not None:
            self.update()

    def _build_displays(self) -> None:
        for row in self.display_rows:
            self.display_group.remove(row)
        self.display_rows = []
        monitors = Gdk.Display.get_default().get_monitors()
        for index in range(monitors.get_n_items()):
            monitor = monitors.get_item(index)
            geometry = monitor.get_geometry()
            scale = monitor.get_scale()
            width, height = round(geometry.width * scale), round(geometry.height * scale)
            hertz = monitor.get_refresh_rate() / 1000
            diagonal = math.hypot(monitor.get_width_mm(), monitor.get_height_mm()) / 25.4
            name = monitor.get_description() or " ".join(
                part for part in (monitor.get_manufacturer(), monitor.get_model()) if part) or "Display"
            detail = f"{width} × {height} at {hertz:.0f} Hz"
            if diagonal > 1:
                detail += f" · {diagonal:.0f}″"
            if scale != 1:
                detail += f" · scale {scale:g}"
            row = info_row(name, detail, monitor.get_connector())[0]
            self.display_group.add(row)
            self.display_rows.append(row)

    def update(self) -> None:
        value = self.ctx.value
        inv = self.ctx.inventory
        cpu_temp = value(inv.cpu.temp_key) if inv.cpu.temp_key else float("nan")
        self.t_cpu.set(fmt_percent(value("cpu.total")), " · ".join(
            part for part in (fmt_mhz(value("cpu.freq")), fmt_temp(cpu_temp) if is_num(cpu_temp) else None) if part))
        self.t_mem.set(gib(value("mem.used")), f"of {gib(inv.memory.total)} · {fmt_percent(value('mem.pct'))}")
        if inv.gpus:
            gpu = inv.gpus[0]
            chip = inv.chip_for(gpu.hwmon)
            temp = chip.sensor("temp") if chip else None
            self.t_gpu.set(fmt_percent(value("gpu0.busy")), " · ".join(part for part in (
                f"VRAM {gib(value('gpu0.vram'))}" if gpu.amd else None,
                fmt_temp(value(temp.key)) if temp else None) if part))
        else:
            self.t_gpu.set(DASH, "No GPU found")
        self.t_disk.set(fmt_rate(value("disk.total")),
                        f"R {fmt_rate(value('disk.r'))} · W {fmt_rate(value('disk.w'))}")
        self.t_net.set(f"↓ {fmt_rate(value('net.rx'))}", f"↑ {fmt_rate(value('net.tx'))}")
        self.t_up.set(fmt_duration(self.ctx.sample.info["cpu"].get("uptime")),
                      f"{self.ctx.sample.info['cpu'].get('procs', DASH)} processes")
        for label, text in self.live:
            label.update(text())
        for label, sensor in self.pinned_live:
            label.update(fmt_sensor(sensor.kind, value(sensor.key)))

    def demand(self) -> set[str]:
        inv = self.ctx.inventory
        sensors = {sensor.uid: sensor for sensor in inv.sensors()}
        chips = {sensors[uid].chip.hwmon for uid in self.ctx.pinned() if uid in sensors}
        chips |= {chip_of(disk.temp_key) for disk in inv.disks if disk.temp_key}
        chips |= {gpu.hwmon for gpu in inv.gpus if gpu.hwmon}
        if inv.cpu.temp_key:
            chips.add(chip_of(inv.cpu.temp_key))
        return chips

    def summary(self) -> str:
        return self.ctx.inventory.system.hostname
