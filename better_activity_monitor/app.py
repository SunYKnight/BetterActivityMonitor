"""Adw.Application: loads style and icons, discovers hardware, opens the single main window."""

import os

from gi.repository import Adw, Gdk, Gio, Gtk

from better_activity_monitor import APP_ID
from better_activity_monitor.context import Context
from better_activity_monitor.history import History
from better_activity_monitor.hwinfo import discover
from better_activity_monitor.settings import Settings
from better_activity_monitor.window import MainWindow

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# One hour at 1 s; the chart window never asks for more than this.
HISTORY_SAMPLES = 3600


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(DATA_DIR, "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(DATA_DIR, "icons"))
        Gtk.Window.set_default_icon_name(APP_ID)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_args: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("window.close", ["<Control>w"])

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            ctx = Context(discover(), History(HISTORY_SAMPLES), Settings.load())
            window = MainWindow(self, ctx)
        window.present()
