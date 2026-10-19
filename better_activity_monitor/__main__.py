"""Entry point: `python3 -m better_activity_monitor`."""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")


def main() -> int:
    from better_activity_monitor.app import Application

    return Application().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
