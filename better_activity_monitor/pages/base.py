"""Page base class: scrollable, width-clamped content with charts refreshed only while visible."""

from gi.repository import Adw, Gtk

from better_activity_monitor.context import Context
from better_activity_monitor.widgets import Chart, Legend


def chip_of(key: str | None) -> str | None:
    """hwmon dir of a sensor history key such as "hw.hwmon4.temp1"."""
    return key.split(".")[1] if key and key.startswith("hw.") else None


class Page(Gtk.ScrolledWindow):
    page_id = ""
    title = ""
    icon = ""

    def __init__(self, ctx: Context) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.ctx = ctx
        self.charts: list[Chart] = []
        self.legends: list[Legend] = []
        # Horizontal boxes that the window's narrow breakpoint turns vertical.
        self.responsive: list[Gtk.Box] = []
        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=24, margin_start=18,
            margin_end=18,
        )
        self.set_child(Adw.Clamp(maximum_size=1600, tightening_threshold=1400, child=self.content))
        # Selectable value labels take focus; following it would make the page jump while reading.
        self.get_child().set_scroll_to_focus(False)

    def chart(self, *args, **kwargs) -> Chart:
        chart = Chart(self.ctx, *args, **kwargs)
        self.charts.append(chart)
        return chart

    def legend(self, chart: Chart, text=None, chars: int = 9) -> Legend:
        legend = Legend(chart, text, chars)
        self.legends.append(legend)
        return legend

    def columns(self) -> tuple[Gtk.Box, Gtk.Box, Gtk.Box]:
        container = Gtk.Box(spacing=18, homogeneous=True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        container.append(left)
        container.append(right)
        self.responsive.append(container)
        return container, left, right

    def refresh(self) -> None:
        """Called per sample while the page is on screen."""
        if self.ctx.sample is not None:
            self.update()
            for legend in self.legends:
                legend.refresh(self.ctx)
        for chart in self.charts:
            chart.queue_draw()

    def update(self) -> None:
        """Updates labels from ctx.sample; charts redraw from the history on their own."""

    def demand(self) -> set[str]:
        """hwmon dirs whose sensors this page shows; slow chips are polled faster while on screen."""
        return set()

    def summary(self) -> str:
        return ""

    def spark(self) -> tuple | None:
        """(history key, colour, y_max, min_span) for the sidebar sparkline."""
        return None
