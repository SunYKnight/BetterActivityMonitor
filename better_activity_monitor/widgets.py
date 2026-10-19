"""Custom-drawn widgets: history charts, sparklines, segment bars, stat tiles and legends."""

import colorsys
import math

from gi.repository import Adw, GLib, Graphene, Gsk, Gtk, Pango, PangoCairo

from better_activity_monitor.context import Context
from better_activity_monitor.util import DASH, NAN, fmt_window

# GNOME HIG palette (blue 3, green 3, yellow 3, orange 3, red 3, purple 3) plus teal, pink, brown and slate.
PALETTE = (
    (0.208, 0.518, 0.894), (0.200, 0.820, 0.478), (0.965, 0.827, 0.176), (1.000, 0.471, 0.000),
    (0.878, 0.106, 0.141), (0.569, 0.255, 0.675), (0.129, 0.565, 0.643), (0.835, 0.380, 0.600),
    (0.596, 0.416, 0.267), (0.435, 0.514, 0.588),
)
READ_COLOR = PALETTE[0]
WRITE_COLOR = PALETTE[3]


def color(index: int) -> tuple[float, float, float]:
    return PALETTE[index % len(PALETTE)]


def core_colors(count: int) -> list[tuple[float, float, float]]:
    """Hue sweep; alternating saturation/value keeps neighbouring layers of a stacked chart apart."""
    return [
        colorsys.hsv_to_rgb((0.58 + index / max(count, 1)) % 1.0, 0.55 if index % 2 else 0.70,
                            0.95 if index % 2 else 0.82)
        for index in range(count)
    ]


def hex_color(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)


def nice_ceil(value: float) -> float:
    """Smallest of 1, 2, 2.5, 5 × 10^n that is ≥ value."""
    result = 1.0
    if value > 0:
        magnitude = 10 ** math.floor(math.log10(value))
        result = 10 * magnitude
        for factor in (5, 2.5, 2, 1):
            if factor * magnitude >= value:
                result = factor * magnitude
    return result


def _bucket(values, step: int) -> list[float]:
    """Mean per group of `step` samples, ignoring gaps, so long windows draw about one point per two pixels."""
    result = []
    for start in range(0, len(values), step):
        chunk = [value for value in values[start:start + step] if value == value]
        result.append(sum(chunk) / len(chunk) if chunk else NAN)
    return result


def _rounded(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _runs(xs: list[float], values: list[float]) -> list[list[tuple[float, float]]]:
    """Splits a polyline at gaps (NaN) into continuous runs."""
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for x, value in zip(xs, values):
        if value == value:
            current.append((x, value))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _age(seconds: float) -> str:
    text = "now"
    if seconds >= 90:
        text = f"{seconds / 60:.0f} min ago"
    elif seconds >= 1:
        text = f"{seconds:.0f} s ago"
    return text


class Value(Gtk.Label):
    """Label that skips relayout when the text did not change."""

    def __init__(self, text: str = DASH, **kwargs) -> None:
        super().__init__(label=text, **kwargs)
        self._text = text
        self.add_css_class("numeric")

    def update(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.set_label(text)


class Series:
    __slots__ = ("key", "label", "color", "stack_scale", "visible")

    def __init__(self, key: str, label: str, rgb: tuple, stack_scale: float = 1.0, visible: bool = True) -> None:
        self.key = key
        self.label = label
        self.color = rgb
        self.stack_scale = stack_scale
        self.visible = visible


class _Plot:
    """Chart data prepared for one draw."""

    __slots__ = ("xs", "times", "columns", "layers", "ymin", "ymax", "stacked", "series", "now")


class Chart(Gtk.DrawingArea):
    """History chart over the global time window, stacked areas or lines, with a hover read-out.

    Stacked mode multiplies each series by its stack_scale, so per-core CPU layers (scale 1/n) add up to the total
    and per-disk layers add up to the combined throughput.
    """

    def __init__(self, ctx: Context, series: list[Series], fmt, *, y_max: float | None = None,
                 min_span: float = 1.0, stackable: bool = True, auto_min: bool = False, height: int = 160) -> None:
        super().__init__()
        self.ctx = ctx
        self.series = list(series)
        self.fmt = fmt
        self.y_max = y_max
        self.min_span = min_span
        self.stackable = stackable
        self.auto_min = auto_min
        self._hover_x: float | None = None
        self._hover_index: int | None = None
        self._xs: list[float] = []
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def stacked(self) -> bool:
        return self.stackable and self.ctx.settings.stacked and len(self.series) > 1

    def _on_motion(self, _controller, x: float, _y: float) -> None:
        # Pointer motion arrives at display refresh rate; redraw only when the read-out moves to another sample.
        self._hover_x = x
        index = self._nearest(x)
        if index != self._hover_index:
            self._hover_index = index
            self.queue_draw()

    def _on_leave(self, _controller) -> None:
        self._hover_x = None
        self._hover_index = None
        self.queue_draw()

    def _nearest(self, x: float | None) -> int | None:
        xs = self._xs
        result = None
        if x is not None and xs and x >= xs[0] - 8:
            result = min(range(len(xs)), key=lambda position: abs(xs[position] - x))
        return result

    def _plot(self, width: int) -> _Plot | None:
        history = self.ctx.history
        now = history.latest_time()
        window = self.ctx.settings.window
        visible = [series for series in self.series if series.visible]
        times, columns = history.window([series.key for series in visible], now - window)
        plot = None
        if len(times) >= 2 and visible:
            step = max(1, math.ceil(len(times) / max(2.0, width / 2)))
            times = _bucket(times, step) if step > 1 else list(times)
            columns = [_bucket(column, step) if step > 1 else list(column) for column in columns]
            plot = _Plot()
            plot.now = now
            plot.times = times
            plot.columns = columns
            plot.series = visible
            plot.stacked = self.stacked()
            plot.xs = [width - (now - t) / window * width for t in times]
            plot.layers = []
            low = 0.0
            if plot.stacked:
                base = [0.0] * len(times)
                for series, column in zip(visible, columns):
                    top = [below + (value * series.stack_scale if value == value else 0.0)
                           for below, value in zip(base, column)]
                    plot.layers.append((series, base, top))
                    base = top
                peak = max(base)
            else:
                finite = [value for column in columns for value in column if value == value]
                peak = max(finite) if finite else 0.0
                low = min(finite) if finite else 0.0
                plot.layers = [(series, None, column) for series, column in zip(visible, columns)]
            plot.ymin = max(0.0, math.floor((low - 5) / 10) * 10) if self.auto_min and not plot.stacked else 0.0
            plot.ymax = self.y_max if self.y_max else plot.ymin + nice_ceil(max((peak - plot.ymin) * 1.05,
                                                                                  self.min_span))
        return plot

    def _draw(self, _area, cr, width: int, height: int) -> None:
        fg = self.get_color()
        ink = (fg.red, fg.green, fg.blue)
        dark = sum(ink) / 3 > 0.5
        _rounded(cr, 0.5, 0.5, width - 1, height - 1, 8)
        cr.set_source_rgba(*ink, 0.04)
        cr.fill_preserve()
        cr.set_source_rgba(*ink, 0.10)
        cr.set_line_width(1)
        cr.stroke()
        _rounded(cr, 1, 1, width - 2, height - 2, 7)
        cr.clip()
        cr.set_source_rgba(*ink, 0.07)
        for index in (1, 2, 3):
            cr.move_to(0, round(height * index / 4) + 0.5)
            cr.line_to(width, round(height * index / 4) + 0.5)
            cr.move_to(round(width * index / 4) + 0.5, 0)
            cr.line_to(round(width * index / 4) + 0.5, height)
        cr.stroke()
        layout = PangoCairo.create_layout(cr)
        font = self.get_pango_context().get_font_description().copy()
        font.set_size(int((font.get_size() or 11 * Pango.SCALE) * 0.8))
        layout.set_font_description(font)
        plot = self._plot(width)
        self._xs = plot.xs if plot else []
        if plot is None:
            layout.set_text("Collecting data…", -1)
            text_width, text_height = layout.get_pixel_size()
            cr.move_to((width - text_width) / 2, (height - text_height) / 2)
            cr.set_source_rgba(*ink, 0.45)
            PangoCairo.show_layout(cr, layout)
        else:
            self._draw_series(cr, plot, height, dark)
            self._draw_labels(cr, layout, plot, width, height, ink)
            index = self._nearest(self._hover_x)
            if index is not None:
                self._draw_hover(cr, layout, plot, index, width, height, ink, dark)

    @staticmethod
    def _y(plot: _Plot, value: float, height: int) -> float:
        return height - 2 - (value - plot.ymin) / (plot.ymax - plot.ymin) * (height - 4)

    def _draw_series(self, cr, plot: _Plot, height: int, dark: bool) -> None:
        cr.set_line_join(1)  # cairo.LINE_JOIN_ROUND
        fill_alpha = 0.55 if dark else 0.45
        if plot.stacked:
            # Layers that contribute nothing would only paint their outline over the layer below.
            drawn = [layer for layer in plot.layers if any(top > below for below, top in zip(layer[1], layer[2]))]
            for series, base, top in drawn:
                cr.move_to(plot.xs[0], self._y(plot, top[0], height))
                for x, value in zip(plot.xs, top):
                    cr.line_to(x, self._y(plot, value, height))
                for x, value in zip(reversed(plot.xs), reversed(base)):
                    cr.line_to(x, self._y(plot, value, height))
                cr.close_path()
                cr.set_source_rgba(*series.color, fill_alpha)
                cr.fill()
                cr.move_to(plot.xs[0], self._y(plot, top[0], height))
                for x, value in zip(plot.xs, top):
                    cr.line_to(x, self._y(plot, value, height))
                cr.set_source_rgba(*series.color, 0.95)
                cr.set_line_width(1.0)
                cr.stroke()
        else:
            fill = len(plot.layers) <= 3
            for series, _base, values in plot.layers:
                runs = _runs(plot.xs, values)
                if fill:
                    for run in runs:
                        cr.move_to(run[0][0], self._y(plot, plot.ymin, height))
                        for x, value in run:
                            cr.line_to(x, self._y(plot, value, height))
                        cr.line_to(run[-1][0], self._y(plot, plot.ymin, height))
                        cr.close_path()
                    cr.set_source_rgba(*series.color, 0.14)
                    cr.fill()
                for run in runs:
                    cr.move_to(run[0][0], self._y(plot, run[0][1], height))
                    for x, value in run[1:]:
                        cr.line_to(x, self._y(plot, value, height))
                cr.set_source_rgba(*series.color, 1.0)
                cr.set_line_width(1.5)
                cr.stroke()

    def _draw_labels(self, cr, layout, plot: _Plot, width: int, height: int, ink: tuple) -> None:
        cr.set_source_rgba(*ink, 0.55)
        layout.set_text(self.fmt(plot.ymax), -1)
        cr.move_to(6, 3)
        PangoCairo.show_layout(cr, layout)
        if plot.ymin > 0:
            layout.set_text(self.fmt(plot.ymin), -1)
            cr.move_to(6, height - 3 - layout.get_pixel_size()[1])
            PangoCairo.show_layout(cr, layout)
        window = self.ctx.settings.window
        layout.set_text(fmt_window(window), -1)
        text_width, text_height = layout.get_pixel_size()
        cr.move_to(width - text_width - 6, height - text_height - 3)
        PangoCairo.show_layout(cr, layout)

    def _draw_hover(self, cr, layout, plot: _Plot, index: int, width: int, height: int, ink: tuple,
                    dark: bool) -> None:
        x = round(plot.xs[index]) + 0.5
        cr.set_source_rgba(*ink, 0.35)
        cr.set_line_width(1)
        cr.move_to(x, 0)
        cr.line_to(x, height)
        cr.stroke()
        rows = []
        for (series, _base, top), column in zip(plot.layers, plot.columns):
            value = column[index]
            dot = top[index] if value == value else NAN
            if dot == dot:
                cr.arc(x, self._y(plot, dot, height), 3, 0, 2 * math.pi)
                cr.set_source_rgba(*series.color, 1)
                cr.fill()
            rows.append((value if value == value else -math.inf, series, value))
        if len(rows) > 8:
            rows = sorted(rows, key=lambda row: row[0], reverse=True)[:6]
        lines = [f"<small>{_age(plot.now - plot.times[index])}</small>"]
        lines += [
            f"<span foreground='{hex_color(series.color)}'>●</span> {GLib.markup_escape_text(series.label)}  "
            f"<b>{GLib.markup_escape_text(self.fmt(value))}</b>"
            for _rank, series, value in rows
        ]
        if plot.stacked and len(plot.layers) > 1:
            lines.append(f"Total  <b>{GLib.markup_escape_text(self.fmt(plot.layers[-1][2][index]))}</b>")
        layout.set_markup("\n".join(lines), -1)
        text_width, text_height = layout.get_pixel_size()
        box_x = x + 10 if x + 10 + text_width + 16 < width else x - 10 - text_width - 16
        box_y = 6
        _rounded(cr, box_x, box_y, text_width + 16, text_height + 12, 6)
        cr.set_source_rgba(*((0.14, 0.14, 0.16, 0.94) if dark else (1, 1, 1, 0.96)))
        cr.fill_preserve()
        cr.set_source_rgba(*ink, 0.15)
        cr.stroke()
        cr.set_source_rgba(*ink, 0.95)
        cr.move_to(box_x + 8, box_y + 6)
        PangoCairo.show_layout(cr, layout)


class Sparkline(Gtk.DrawingArea):
    """Small single-series history for the sidebar."""

    def __init__(self, ctx: Context, key: str, rgb: tuple, y_max: float | None = None, min_span: float = 1.0) -> None:
        super().__init__()
        self.ctx = ctx
        self.key = key
        self.rgb = rgb
        self.y_max = y_max
        self.min_span = min_span
        self.set_content_width(56)
        self.set_content_height(24)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width: int, height: int) -> None:
        history = self.ctx.history
        now = history.latest_time()
        window = self.ctx.settings.window
        times, (column,) = history.window([self.key], now - window)
        if len(times) >= 2:
            step = max(1, math.ceil(len(times) / width))
            times = _bucket(times, step) if step > 1 else list(times)
            values = _bucket(column, step) if step > 1 else list(column)
            finite = [value for value in values if value == value]
            top = self.y_max or nice_ceil(max(max(finite, default=0.0), self.min_span))
            xs = [width - (now - t) / window * width for t in times]
            for run in _runs(xs, values):
                cr.move_to(run[0][0], height)
                for x, value in run:
                    cr.line_to(x, height - 1 - min(value / top, 1.0) * (height - 2))
                cr.line_to(run[-1][0], height)
                cr.close_path()
            cr.set_source_rgba(*self.rgb, 0.30)
            cr.fill_preserve()
            cr.set_source_rgba(*self.rgb, 0.9)
            cr.set_line_width(1.2)
            cr.stroke()


class SegmentBar(Gtk.DrawingArea):
    """Horizontal proportion bar, e.g. used / cache / free memory or filesystem usage."""

    def __init__(self, height: int = 10) -> None:
        super().__init__()
        self._segments: list[tuple[float, tuple]] = []
        self._total = 1.0
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_segments(self, segments: list[tuple[float, tuple]], total: float) -> None:
        if segments != self._segments or total != self._total:
            self._segments = segments
            self._total = total or 1.0
            self.queue_draw()

    def _draw(self, _area, cr, width: int, height: int) -> None:
        fg = self.get_color()
        _rounded(cr, 0, 0, width, height, height / 2)
        cr.clip()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
        cr.paint()
        x = 0.0
        for value, rgb in self._segments:
            part = max(0.0, value) / self._total * width
            cr.rectangle(x, 0, part, height)
            cr.set_source_rgb(*rgb)
            cr.fill()
            x += part


class Swatch(Gtk.DrawingArea):
    def __init__(self, rgb: tuple, size: int = 10) -> None:
        super().__init__()
        self.rgb = rgb
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width: int, height: int) -> None:
        _rounded(cr, 0, 0, width, height, 3)
        cr.set_source_rgb(*self.rgb)
        cr.fill()


class EvenGrid(Gtk.Widget):
    """Rows of equal-width children whose column count depends only on the available width.

    Content-driven wrapping (FlowBox, WrapBox) resized and re-wrapped tiles and cards whenever a live value grew or
    shrank. Here widths never depend on content; columns are balanced, so six children become 6, 3 + 3 or 2 + 2 + 2,
    never 4 + 2. Children of one row share the row's height.
    """

    def __init__(self, min_width: int = 160, spacing: int = 12) -> None:
        super().__init__()
        self.min_width = min_width
        self.spacing = spacing
        self._children: list[Gtk.Widget] = []

    def append(self, child: Gtk.Widget) -> None:
        child.set_parent(self)
        self._children.append(child)

    def do_dispose(self) -> None:
        for child in self._children:
            child.unparent()
        self._children.clear()
        super().do_dispose()

    def _layout(self, width: float) -> tuple[int, float, list[int]]:
        """(columns, column width, row heights) for the given width."""
        count = max(1, len(self._children))
        fit = max(1, min(count, int((width + self.spacing) // (self.min_width + self.spacing))))
        columns = math.ceil(count / math.ceil(count / fit))
        column_width = max(1.0, (width - (columns - 1) * self.spacing) / columns)
        heights: list[int] = []
        for index, child in enumerate(self._children):
            height = child.measure(Gtk.Orientation.VERTICAL, int(column_width))[1]
            if index % columns == 0:
                heights.append(height)
            else:
                heights[-1] = max(heights[-1], height)
        return columns, column_width, heights

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        count = len(self._children)
        natural_width = count * self.min_width + max(0, count - 1) * self.spacing
        if orientation == Gtk.Orientation.HORIZONTAL:
            result = (self.min_width, natural_width, -1, -1)
        else:
            _columns, _width, heights = self._layout(for_size if for_size >= 0 else natural_width)
            height = sum(heights) + max(0, len(heights) - 1) * self.spacing
            result = (height, height, -1, -1)
        return result

    def do_size_allocate(self, width: int, _height: int, _baseline: int) -> None:
        columns, column_width, heights = self._layout(width)
        top = 0
        for index, child in enumerate(self._children):
            row, column = divmod(index, columns)
            if column == 0 and row > 0:
                top += heights[row - 1] + self.spacing
            left = round(column * (column_width + self.spacing))
            right = round(column * (column_width + self.spacing) + column_width)
            point = Graphene.Point()
            point.init(left, top)
            child.allocate(right - left, heights[row], -1, Gsk.Transform.new().translate(point))


class StatTile(Gtk.Box):
    """Headline number with caption and detail line."""

    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("card")
        self.add_css_class("tile")
        # Ellipsizing keeps the tile's minimum width independent of the live text.
        self.append(Gtk.Label(label=title, xalign=0, css_classes=["tile-caption"], ellipsize=Pango.EllipsizeMode.END))
        self.value = Value(xalign=0, css_classes=["tile-value"], ellipsize=Pango.EllipsizeMode.END)
        self.sub = Value(" ", xalign=0, css_classes=["tile-sub"], ellipsize=Pango.EllipsizeMode.END)
        self.append(self.value)
        self.append(self.sub)

    def set(self, value: str, sub: str = "") -> None:
        self.value.update(value)
        # A blank keeps the line height, so tiles without detail stay as tall as the others.
        self.sub.update(sub or " ")


class LegendItem(Gtk.Button):
    """Legend entry showing the current value; clicking hides or shows the series."""

    def __init__(self, series: Series, chart: Chart, chars: int) -> None:
        super().__init__(css_classes=["flat", "legend-item"], tooltip_text="Show or hide in the chart")
        self.series = series
        self.chart = chart
        box = Gtk.Box(spacing=6)
        box.append(Swatch(series.color))
        box.append(Gtk.Label(label=series.label))
        # Fixed value width: legend lines must not re-wrap as values change.
        self.value = Value("", css_classes=["dim-label"], width_chars=chars, xalign=0)
        box.append(self.value)
        self.set_child(box)
        self._sync()
        self.connect("clicked", self._on_clicked)

    def _on_clicked(self, _button) -> None:
        self.series.visible = not self.series.visible
        self._sync()
        self.chart.queue_draw()

    def _sync(self) -> None:
        if self.series.visible:
            self.remove_css_class("off")
        else:
            self.add_css_class("off")


class Legend(Adw.WrapBox):
    def __init__(self, chart: Chart, text=None, chars: int = 9) -> None:
        super().__init__(child_spacing=4, line_spacing=2)
        self.chart = chart
        self.text = text or (lambda series, value: chart.fmt(value))
        self.items = [LegendItem(series, chart, chars) for series in chart.series]
        for item in self.items:
            self.append(item)

    def refresh(self, ctx: Context) -> None:
        for item in self.items:
            item.value.update(self.text(item.series, ctx.value(item.series.key)))


def card(title: str, *children: Gtk.Widget, suffix: Gtk.Widget | None = None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card", "chart-card"])
    header = Gtk.Box(spacing=8)
    header.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=["heading"]))
    if suffix is not None:
        header.append(suffix)
    box.append(header)
    for child in children:
        box.append(child)
    return box


def info_row(title: str, value: str = DASH, subtitle: str | None = None) -> tuple[Adw.ActionRow, Value]:
    row = Adw.ActionRow(title=GLib.markup_escape_text(title))
    if subtitle:
        row.set_subtitle(GLib.markup_escape_text(subtitle))
    # NaturalWrapMode.NONE: natural width is the unwrapped text, so short values never wrap early.
    label = Value(value, xalign=1, wrap=True, selectable=True, css_classes=["dim-label"],
                  justify=Gtk.Justification.RIGHT, natural_wrap_mode=Gtk.NaturalWrapMode.NONE)
    label.set_max_width_chars(48)
    row.add_suffix(label)
    return row, label


def group(title: str, description: str | None = None) -> Adw.PreferencesGroup:
    return Adw.PreferencesGroup(title=title, description=description)
