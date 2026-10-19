"""Memory page: composition bar, stacked used/cache history, swap, meminfo details and DIMM modules."""

from gi.repository import Gtk

from better_activity_monitor.pages.base import Page
from better_activity_monitor.util import DASH, fmt_bytes, fmt_percent
from better_activity_monitor.widgets import (
    EvenGrid, SegmentBar, Series, StatTile, Swatch, Value, card, color, group, info_row,
)

USED_COLOR = color(0)
CACHE_COLOR = color(6)
SWAP_COLOR = color(3)

_DETAILS = (
    ("MemTotal", "Total"), ("MemAvailable", "Available"), ("MemFree", "Free"), ("Cached", "Page cache"),
    ("Buffers", "Buffers"), ("Shmem", "Shared (tmpfs, shm)"), ("SReclaimable", "Slab, reclaimable"),
    ("SUnreclaim", "Slab, unreclaimable"), ("KernelStack", "Kernel stacks"), ("PageTables", "Page tables"),
    ("AnonHugePages", "Transparent huge pages"), ("Dirty", "Dirty"), ("Writeback", "Writeback"),
    ("SwapCached", "Swap cache"), ("Zswap", "Zswap pool"),
)


def gib(value) -> str:
    return fmt_bytes(value, binary=True)


class MemoryPage(Page):
    page_id = "memory"
    title = "Memory"
    icon = "bam-memory-symbolic"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        memory = ctx.inventory.memory
        self.total = memory.total

        tiles = EvenGrid()
        self.t_used = StatTile("Used")
        self.t_avail = StatTile("Available")
        self.t_cache = StatTile("Cache & buffers")
        self.t_swap = StatTile("Swap")
        for tile in (self.t_used, self.t_avail, self.t_cache, self.t_swap):
            tiles.append(tile)
        self.content.append(tiles)

        self.bar = SegmentBar(14)
        keys = Gtk.Box(spacing=18)
        self.bar_labels = {}
        for name, rgb in (("Used", USED_COLOR), ("Cache", CACHE_COLOR), ("Free", None)):
            item = Gtk.Box(spacing=6)
            if rgb:
                item.append(Swatch(rgb))
            item.append(Gtk.Label(label=name, css_classes=["dim-label"]))
            self.bar_labels[name] = Value()
            item.append(self.bar_labels[name])
            keys.append(item)
        usage = self.chart([
            Series("mem.used", "Used", USED_COLOR), Series("mem.cache", "Cache (reclaimable)", CACHE_COLOR),
        ], gib, y_max=float(memory.total), height=200)
        self.content.append(card("Memory usage", self.bar, keys, usage, self.legend(usage)))

        self.swap_total = sum(size for _name, _kind, size in memory.swaps)
        if self.swap_total:
            swap = self.chart([Series("mem.swap", "Swap used", SWAP_COLOR)], gib, y_max=float(self.swap_total),
                              stackable=False, height=120)
            self.content.append(card("Swap", swap, self.legend(swap)))

        container, left, right = self.columns()
        details = group("Details", "Values from /proc/meminfo")
        self.details = {}
        for key, title in _DETAILS:
            row, label = info_row(title)
            details.add(row)
            self.details[key] = label
        row, self.committed = info_row("Committed", subtitle="Allocations promised to processes")
        details.add(row)
        left.append(details)

        slots = f"{len(memory.dimms)} of {memory.slots} slots used" if memory.slots else "Module data unavailable"
        if memory.max_capacity:
            slots += f" · up to {gib(memory.max_capacity)}"
        modules = group("Modules", slots)
        for dimm in memory.dimms:
            speed = f"-{dimm.configured_speed or dimm.speed}" if dimm.configured_speed or dimm.speed else ""
            subtitle = " · ".join(part for part in (dimm.manufacturer, dimm.part, dimm.form_factor,
                                                    f"rank {dimm.rank}" if dimm.rank else None) if part)
            modules.add(info_row(dimm.locator, f"{gib(dimm.size)} {dimm.kind}{speed}", subtitle)[0])
        for _index in range(memory.slots - len(memory.dimms)):
            modules.add(info_row("Empty slot", DASH)[0])
        for name, kind, size in memory.swaps:
            modules.add(info_row(f"Swap {kind}", gib(size), name)[0])
        right.append(modules)
        self.content.append(container)

    def update(self) -> None:
        mem = self.ctx.sample.info["mem"]
        total = mem.get("MemTotal", self.total)
        available = mem.get("MemAvailable", 0)
        free = mem.get("MemFree", 0)
        used = total - available
        cache = mem.get("Cached", 0) + mem.get("Buffers", 0) + mem.get("SReclaimable", 0)
        self.t_used.set(gib(used), f"of {gib(total)} · {fmt_percent(100 * used / total if total else 0)}")
        self.t_avail.set(gib(available), f"Free {gib(free)}")
        self.t_cache.set(gib(cache), f"Dirty {gib(mem.get('Dirty', 0))}")
        swap_total = mem.get("SwapTotal", 0)
        swap_used = swap_total - mem.get("SwapFree", 0)
        self.t_swap.set(gib(swap_used) if swap_total else "None", f"of {gib(swap_total)}" if swap_total else "")
        self.bar.set_segments([(used, USED_COLOR), (max(0, available - free), CACHE_COLOR)], total)
        self.bar_labels["Used"].update(gib(used))
        self.bar_labels["Cache"].update(gib(max(0, available - free)))
        self.bar_labels["Free"].update(gib(free))
        for key, label in self.details.items():
            label.update(gib(mem[key]) if key in mem else DASH)
        self.committed.update(f"{gib(mem.get('Committed_AS'))} · commit limit {gib(mem.get('CommitLimit'))}")

    def summary(self) -> str:
        return f"{gib(self.ctx.value('mem.used'))} of {gib(self.total)}"

    def spark(self) -> tuple:
        return ("mem.pct", USED_COLOR, 100.0, 1.0)
