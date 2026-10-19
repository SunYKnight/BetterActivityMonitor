#!/bin/sh
# Installs Better Activity Monitor for the current user: the `better-activity-monitor` command, a desktop entry
# and the app icon. `./install.sh --uninstall` removes them again. PREFIX defaults to ~/.local.
set -eu

APP_ID=ch.homejj.BetterActivityMonitor
PREFIX="${PREFIX:-$HOME/.local}"
SRC="$(cd "$(dirname "$0")" && pwd)"
LIB="$PREFIX/lib/better-activity-monitor"
BIN="$PREFIX/bin/better-activity-monitor"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"
ICON="$PREFIX/share/icons/hicolor/512x512/apps/$APP_ID.png"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$LIB"
    rm -f "$BIN" "$DESKTOP" "$ICON"
    echo "Removed Better Activity Monitor from $PREFIX"
    exit 0
fi

# PyGObject, GTK 4 and libadwaita come from the distribution, so the launcher must use this interpreter.
PYTHON="$(command -v python3)"
if ! "$PYTHON" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null; then
    echo "error: $PYTHON lacks PyGObject with GTK 4 and libadwaita (openSUSE: python3-gobject-Gdk," \
         "typelib-1_0-Gtk-4_0, typelib-1_0-Adw-1)" >&2
    exit 1
fi

rm -rf "$LIB"
mkdir -p "$LIB" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp -r "$SRC/better_activity_monitor" "$LIB/"
find "$LIB" -name __pycache__ -prune -exec rm -rf {} +
# Running a directory executes its __main__.py with the directory first on sys.path.
printf 'import sys\n\nfrom better_activity_monitor.__main__ import main\n\nsys.exit(main())\n' > "$LIB/__main__.py"
"$PYTHON" -m compileall -q "$LIB"

printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$PYTHON" "$LIB" > "$BIN"
chmod 755 "$BIN"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Better Activity Monitor
GenericName=System Monitor
Comment=Hardware overview with live statistics and history charts
Exec=$BIN
Icon=$APP_ID
Terminal=false
Categories=System;Monitor;GTK;
Keywords=cpu;memory;gpu;disk;network;temperature;sensors;hardware;fan;
StartupNotify=true
StartupWMClass=$APP_ID
EOF
cp "$SRC/better_activity_monitor/data/icons/$APP_ID.png" "$ICON"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$PREFIX/share/applications" || true
fi
echo "Installed: $BIN"
echo "Desktop entry: $DESKTOP"
