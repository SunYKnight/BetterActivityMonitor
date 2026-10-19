"""Pages in sidebar order."""

from better_activity_monitor.context import Context
from better_activity_monitor.pages.base import Page
from better_activity_monitor.pages.cpu import CpuPage
from better_activity_monitor.pages.disks import DisksPage
from better_activity_monitor.pages.gpu import GpuPage
from better_activity_monitor.pages.memory import MemoryPage
from better_activity_monitor.pages.network import NetworkPage
from better_activity_monitor.pages.overview import OverviewPage
from better_activity_monitor.pages.sensors import SensorsPage


def build_pages(ctx: Context) -> list[Page]:
    pages: list[Page] = [OverviewPage(ctx), CpuPage(ctx), MemoryPage(ctx)]
    if ctx.inventory.disks:
        pages.append(DisksPage(ctx))
    pages += [GpuPage(ctx, gpu) for gpu in ctx.inventory.gpus]
    if ctx.inventory.nets:
        pages.append(NetworkPage(ctx))
    if ctx.inventory.chips:
        pages.append(SensorsPage(ctx))
    return pages
