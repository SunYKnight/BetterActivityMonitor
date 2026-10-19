"""Main window: sidebar with live summaries and sparklines, content stack, header controls."""

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from better_activity_monitor import APP_ID, APP_NAME, VERSION
from better_activity_monitor.context import Context
from better_activity_monitor.pages import Page, build_pages
from better_activity_monitor.sampler import Collector, Sample, Sampler
from better_activity_monitor.settings import INTERVALS, WINDOWS
from better_activity_monitor.util import fmt_window
from better_activity_monitor.widgets import Sparkline, Value


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, ctx: Context, page: Page) -> None:
        super().__init__()
        self.page = page
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(Gtk.Image(icon_name=page.icon))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=page.title, xalign=0, ellipsize=Pango.EllipsizeMode.END))
        self.subtitle = Value("", xalign=0, css_classes=["caption", "dim-label"], ellipsize=Pango.EllipsizeMode.END)
        texts.append(self.subtitle)
        box.append(texts)
        spark = page.spark()
        self.spark = Sparkline(ctx, *spark) if spark else None
        if self.spark:
            box.append(self.spark)
        self.set_child(box)

    def refresh(self) -> None:
        self.subtitle.update(self.page.summary())
        if self.spark:
            self.spark.queue_draw()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, ctx: Context) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.ctx = ctx
        settings = ctx.settings
        self.set_default_size(settings.width, settings.height)
        self.set_size_request(360, 480)
        if settings.maximized:
            self.maximize()

        self.pages = build_pages(ctx)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        for page in self.pages:
            self.stack.add_named(page, page.page_id)

        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.rows = [SidebarRow(ctx, page) for page in self.pages]
        for row in self.rows:
            self.listbox.append(row)
        self.listbox.connect("row-selected", self._on_row_selected)
        sidebar_header = Adw.HeaderBar()
        sidebar_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._menu(),
                                               primary=True, tooltip_text="Main menu"))
        sidebar_view = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER))
        sidebar_view.add_top_bar(sidebar_header)

        self.window_title = Adw.WindowTitle(title=APP_NAME)
        content_header = Adw.HeaderBar(title_widget=self.window_title)
        content_header.pack_end(self._range_toggles())
        content_header.pack_end(self._style_toggles())
        content_view = Adw.ToolbarView(content=self.stack)
        content_view.add_top_bar(content_header)
        self.content_page = Adw.NavigationPage(title=APP_NAME, child=content_view, tag="content")
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title=APP_NAME, child=sidebar_view, tag="sidebar"),
            content=self.content_page, min_sidebar_width=230, max_sidebar_width=300,
        )
        self.set_content(self.split)
        self._breakpoints()

        self._install_actions()
        self.collector = Collector(ctx.inventory)
        self.sampler = Sampler(self.collector, settings.interval, self._on_sample)
        self.current = self.pages[0]
        start = next((row for row in self.rows if row.page.page_id == settings.page), self.rows[0])
        self.listbox.select_row(start)
        self.connect("close-request", self._on_close)
        self.connect("notify::suspended", self._on_suspended)
        self.sampler.start()

    def _menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        intervals = Gio.Menu()
        for interval in INTERVALS:
            intervals.append(f"Every {interval:g} s", f"win.interval::{interval:g}")
        menu.append_section("Update interval", intervals)
        about = Gio.Menu()
        about.append(f"About {APP_NAME}", "win.about")
        about.append("Quit", "app.quit")
        menu.append_section(None, about)
        return menu

    def _style_toggles(self) -> Adw.ToggleGroup:
        toggles = Adw.ToggleGroup(tooltip_text="Chart style", css_classes=["round"])
        toggles.add(Adw.Toggle(name="stacked", label="Stacked"))
        toggles.add(Adw.Toggle(name="lines", label="Lines"))
        toggles.set_active_name("stacked" if self.ctx.settings.stacked else "lines")
        toggles.connect("notify::active-name", self._on_style)
        return toggles

    def _range_toggles(self) -> Adw.ToggleGroup:
        toggles = Adw.ToggleGroup(tooltip_text="History shown in charts", css_classes=["round"])
        for seconds in WINDOWS:
            toggles.add(Adw.Toggle(name=str(seconds), label=fmt_window(seconds).replace(" ", "")))
        toggles.set_active_name(str(self.ctx.settings.window))
        toggles.connect("notify::active-name", self._on_range)
        return toggles

    def _breakpoints(self) -> None:
        # Only the last matching breakpoint applies, so the narrow one repeats the single-column setters.
        boxes = [box for page in self.pages for box in page.responsive]
        single_column = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1100sp"))
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700sp"))
        narrow.add_setter(self.split, "collapsed", True)
        for breakpoint in (single_column, narrow):
            for box in boxes:
                breakpoint.add_setter(box, "orientation", Gtk.Orientation.VERTICAL)
            self.add_breakpoint(breakpoint)

    def _install_actions(self) -> None:
        interval = Gio.SimpleAction.new_stateful(
            "interval", GLib.VariantType.new("s"), GLib.Variant("s", f"{self.ctx.settings.interval:g}")
        )
        interval.connect("change-state", self._on_interval)
        self.add_action(interval)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)

    def _on_interval(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.ctx.settings.interval = float(value.get_string())
        self.sampler.set_interval(self.ctx.settings.interval)

    def _on_about(self, *_args) -> None:
        Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
            comments="Lists the PC's hardware with live temperatures and fans, and charts processor, memory, storage, "
                     "graphics, network and sensors with up to an hour of history.",
        ).present(self)

    def _on_style(self, toggles: Adw.ToggleGroup, _param) -> None:
        self.ctx.settings.stacked = toggles.get_active_name() == "stacked"
        self.current.refresh()

    def _on_range(self, toggles: Adw.ToggleGroup, _param) -> None:
        self.ctx.settings.window = int(toggles.get_active_name())
        self._refresh_visible()

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: SidebarRow | None) -> None:
        if row is not None:
            self.current = row.page
            self.stack.set_visible_child(row.page)
            self.window_title.set_title(row.page.title)
            self.content_page.set_title(row.page.title)
            self.split.set_show_content(True)
            self.ctx.settings.page = row.page.page_id
            self._update_demand()
            row.page.refresh()

    def _update_demand(self) -> None:
        demand = set() if self.is_suspended() else self.current.demand()
        self.collector.demand = frozenset(demand)

    def _refresh_visible(self) -> None:
        for row in self.rows:
            row.refresh()
        self.current.refresh()

    def _on_sample(self, sample: Sample) -> None:
        self.ctx.history.push(sample.t, sample.values)
        self.ctx.sample = sample
        # A minimized or otherwise hidden window only records history; nothing is laid out or drawn.
        if self.get_mapped() and not self.is_suspended():
            self._refresh_visible()

    def _on_suspended(self, *_args) -> None:
        self._update_demand()
        if not self.is_suspended() and self.ctx.sample is not None:
            self._refresh_visible()

    def _on_close(self, *_args) -> bool:
        settings = self.ctx.settings
        settings.maximized = self.is_maximized()
        if not settings.maximized:
            settings.width, settings.height = self.get_default_size()
        settings.save()
        self.sampler.stop()
        return False
