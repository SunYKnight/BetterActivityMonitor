"""Storage page: combined read/write stacked per drive, plus one card per drive with its filesystems."""

from gi.repository import Gtk, Pango

from better_activity_monitor.hwinfo import DiskInfo
from better_activity_monitor.pages.base import Page, chip_of
from better_activity_monitor.util import DASH, fmt_bytes, fmt_percent, fmt_rate, fmt_temp, is_num
from better_activity_monitor.widgets import (
    EvenGrid, READ_COLOR, WRITE_COLOR, SegmentBar, Series, StatTile, Value, card, color,
)

MIN_SPAN = 1e6


def disk_labels(disks: list[DiskInfo]) -> dict[str, str]:
    models = [disk.model for disk in disks]
    return {disk.name: disk.model if models.count(disk.model) == 1 else f"{disk.model} ({disk.name})"
            for disk in disks}


def disk_icon(disk: DiskInfo) -> str:
    icons = {"HDD": "drive-harddisk-symbolic", "USB drive": "drive-harddisk-usb-symbolic"}
    return icons.get(disk.kind, "drive-harddisk-solidstate-symbolic")


def mini_stat(caption: str) -> tuple[Gtk.Box, Value]:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.append(Gtk.Label(label=caption, xalign=0, css_classes=["caption", "dim-label"],
                         ellipsize=Pango.EllipsizeMode.END))
    value = Value(xalign=0, css_classes=["mini-value"], ellipsize=Pango.EllipsizeMode.END)
    box.append(value)
    return box, value


class _FsRow(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(spacing=8)
        self.mounts = Value("", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.kind = Value("", css_classes=["dim-label", "caption"])
        self.usage = Value("", css_classes=["dim-label"])
        top.append(self.mounts)
        top.append(self.kind)
        top.append(self.usage)
        self.bar = SegmentBar(8)
        self.append(top)
        self.append(self.bar)

    def update(self, usage, rgb: tuple) -> None:
        extra = f"  +{len(usage.mounts) - 1}" if len(usage.mounts) > 1 else ""
        self.mounts.update(usage.mounts[0] + extra)
        self.set_tooltip_text(f"{usage.source}\n" + "\n".join(usage.mounts))
        self.kind.update(usage.fstype)
        percent = 100 * usage.used / usage.total if usage.total else 0
        self.usage.update(f"{fmt_bytes(usage.used)} of {fmt_bytes(usage.total)} · {fmt_percent(percent)}")
        self.bar.set_segments([(usage.used, rgb)], usage.total)


class DiskCard(Gtk.Box):
    def __init__(self, page: Page, disk: DiskInfo, accent: tuple) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["card", "chart-card"])
        self.disk = disk
        self.accent = accent
        header = Gtk.Box(spacing=12)
        header.append(Gtk.Image(icon_name=disk_icon(disk), pixel_size=32, css_classes=["dim-label"]))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titles.append(Gtk.Label(label=disk.model, xalign=0, css_classes=["heading"], ellipsize=Pango.EllipsizeMode.END))
        subtitle = " · ".join(part for part in (disk.kind, fmt_bytes(disk.size), disk.name,
                                                f"FW {disk.firmware}" if disk.firmware else None) if part)
        titles.append(Gtk.Label(label=subtitle, xalign=0, css_classes=["caption", "dim-label"],
                                ellipsize=Pango.EllipsizeMode.END))
        header.append(titles)
        self.temp = Value("", css_classes=["title-4"], tooltip_text="Drive temperature")
        header.append(self.temp)
        self.append(header)

        stats = EvenGrid(100)
        self.values = {}
        for key, caption in (("r", "Read"), ("w", "Write"), ("busy", "Active time"), ("iops", "IOPS read / write"),
                             ("rt", "Read since boot"), ("wt", "Written since boot")):
            box, value = mini_stat(caption)
            stats.append(box)
            self.values[key] = value
        self.append(stats)
        self.append(page.chart([
            Series(f"disk.{disk.name}.r", "Read", READ_COLOR), Series(f"disk.{disk.name}.w", "Write", WRITE_COLOR),
        ], fmt_rate, stackable=False, min_span=MIN_SPAN, height=110))
        self.fs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.append(self.fs_box)
        self.fs_sources: list[str] = []
        self.fs_rows: list[_FsRow] = []

    def update(self, ctx) -> None:
        value = ctx.value
        name = self.disk.name
        details = ctx.sample.info["disks"].get(name, {})
        self.values["r"].update(fmt_rate(value(f"disk.{name}.r")))
        self.values["w"].update(fmt_rate(value(f"disk.{name}.w")))
        self.values["busy"].update(fmt_percent(value(f"disk.{name}.busy")))
        read_iops, write_iops = details.get("read_iops"), details.get("write_iops")
        self.values["iops"].update(
            f"{read_iops:.0f} / {write_iops:.0f}" if is_num(read_iops) and is_num(write_iops) else DASH
        )
        self.values["rt"].update(fmt_bytes(details.get("read_total")))
        self.values["wt"].update(fmt_bytes(details.get("write_total")))
        if self.disk.temp_key:
            self.temp.update(fmt_temp(value(self.disk.temp_key)))
        filesystems = ctx.sample.info["fs"].get(name, [])
        sources = [usage.source for usage in filesystems]
        if sources != self.fs_sources:
            for row in self.fs_rows:
                self.fs_box.remove(row)
            self.fs_rows = [_FsRow() for _usage in filesystems]
            for row in self.fs_rows:
                self.fs_box.append(row)
            self.fs_sources = sources
        for row, usage in zip(self.fs_rows, filesystems):
            row.update(usage, self.accent)


class DisksPage(Page):
    page_id = "disks"
    title = "Storage"
    icon = "drive-harddisk-solidstate-symbolic"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        disks = ctx.inventory.disks
        labels = disk_labels(disks)

        tiles = EvenGrid()
        self.t_read = StatTile("Read")
        self.t_write = StatTile("Write")
        self.t_capacity = StatTile("Capacity")
        self.t_space = StatTile("Space used")
        for tile in (self.t_read, self.t_write, self.t_capacity, self.t_space):
            tiles.append(tile)
        self.content.append(tiles)
        self.t_capacity.set(fmt_bytes(sum(disk.size for disk in disks)),
                            f"{len(disks)} drive{'s' if len(disks) != 1 else ''}")

        grid = EvenGrid(340)
        for kind, title in (("r", "Read — all drives"), ("w", "Write — all drives")):
            chart = self.chart([
                Series(f"disk.{disk.name}.{kind}", labels[disk.name], color(index))
                for index, disk in enumerate(disks)
            ], fmt_rate, min_span=MIN_SPAN, height=180)
            grid.append(card(title, chart, self.legend(chart)))
        self.content.append(grid)

        cards = EvenGrid(340)
        self.cards = [DiskCard(self, disk, color(index)) for index, disk in enumerate(disks)]
        for disk_card in self.cards:
            cards.append(disk_card)
        self.content.append(cards)

    def update(self) -> None:
        value = self.ctx.value
        details = self.ctx.sample.info["disks"]
        read_iops = sum(entry.get("read_iops", 0.0) for entry in details.values())
        write_iops = sum(entry.get("write_iops", 0.0) for entry in details.values())
        self.t_read.set(fmt_rate(value("disk.r")), f"{read_iops:.0f} IOPS" if is_num(read_iops) else "")
        self.t_write.set(fmt_rate(value("disk.w")), f"{write_iops:.0f} IOPS" if is_num(write_iops) else "")
        filesystems = [usage for usages in self.ctx.sample.info["fs"].values() for usage in usages]
        used = sum(usage.used for usage in filesystems)
        total = sum(usage.total for usage in filesystems)
        self.t_space.set(fmt_bytes(used), f"of {fmt_bytes(total)} mounted" if total else "")
        for disk_card in self.cards:
            disk_card.update(self.ctx)

    def demand(self) -> set[str]:
        return {chip_of(disk.temp_key) for disk in self.ctx.inventory.disks if disk.temp_key}

    def summary(self) -> str:
        return f"R {fmt_rate(self.ctx.value('disk.r'))} · W {fmt_rate(self.ctx.value('disk.w'))}"

    def spark(self) -> tuple:
        return ("disk.total", READ_COLOR, None, MIN_SPAN)
