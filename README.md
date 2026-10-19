# Better Activity Monitor

GTK 4 / libadwaita system monitor for Linux: one page lists the PC's hardware with live temperatures and fans; the
others chart processor, memory, storage, graphics, network and sensors with history.

## Install

Needs Python ≥ 3.11 with PyGObject, GTK ≥ 4.14 and libadwaita ≥ 1.7 from the distribution (openSUSE:
`python3-gobject-Gdk`, `typelib-1_0-Gtk-4_0`, `typelib-1_0-Adw-1`); no pip packages.

Download _Source code_ from the [latest release](https://github.com/SunYKnight/BetterActivityMonitor/releases/latest),
unpack it, then in its folder:

```sh
./install.sh                         # ~/.local/bin/better-activity-monitor, desktop entry, icon
./install.sh --uninstall
PREFIX=/some/prefix ./install.sh     # other prefix than ~/.local
python3 -m better_activity_monitor   # run without installing
```

Settings: `~/.config/better-activity-monitor/settings.json`.

## Pages

- **Overview**: board, BIOS, CPU, memory modules, GPU, drives, displays, network adapters, audio, USB and PCI
  devices, with live load, temperatures and pinned sensors.
- **Processor**: per-CPU load stacked to the total, time breakdown (user/system/iowait/…), clocks, temperatures, load
  average, context switches, interrupts.
- **Memory**: used/cache/free history, swap, `/proc/meminfo`, DIMM slots.
- **Storage**: read/write stacked per drive; per drive throughput, active time, IOPS, totals since boot, temperature,
  filesystem usage.
- **Graphics** (one page per GPU): engine and memory-controller load, VRAM, temperatures, power, clocks, fan.
- **Network**: download/upload stacked per physical interface; per interface link state, speed, addresses, gateway,
  Wi-Fi signal.
- **Sensors**: every hwmon channel with session min/max and temperature/fan history; the pin button puts a sensor on
  Overview.

The header switches stacked/line charts and the history window (1 min – 1 h); the main menu sets the update interval
(0.5–5 s). Clicking a legend entry hides its series; hovering a chart shows that moment's values.

## Resource use

- A worker thread samples procfs/sysfs through descriptors kept open (`pread`), ~3 ms per tick; the GTK thread only
  stores samples and redraws what is visible.
- Only the visible page and the sidebar update; a minimized window records history without drawing.
- Slow sensor chips (> 4 ms per read) are read every 2 s while shown, else every 10 s, stretched to cost at most ~1 %
  of a core each. Example: the nct6798 Super I/O costs ~75 ms kernel CPU per read and is read every ~7.5 s; EC and
  NVMe sensors block up to 50 ms.
- Runtime-suspended GPUs (laptop dGPUs) are not polled, so they stay asleep.
- History: 3600 samples per series in `array('d')` ring buffers, a few MB.
- Ryzen 9 5900X at 1 s: ~1 % of a core on Overview, 1.2–1.4 % on Processor (24 stacked cores), ~125 MiB RSS with
  GTK's default Vulkan renderer.

## Limits

- Drives and interfaces are detected at start; hot-plugged ones appear after a restart.
- GPU load, VRAM and clocks need amdgpu; other drivers show hwmon values (temperature, fan, power) and Intel clocks.
- SATA temperatures need the `drivetemp` kernel module; DIMM details come from the udev database
  (`/run/udev/data/+dmi:id`).
- Virtual interfaces (docker, bridges, VPN) are not charted; their traffic shows on the physical adapter.

## Release

1. Set the version in `pyproject.toml` and `VERSION` in `better_activity_monitor/__init__.py`, and head its
   `CHANGELOG.md` section `## <version>`.
2. Commit to `main`, tag `v<version>`, push both (`git push origin main v<version>`). The _Release_ workflow checks
   that tag and versions match and publishes the GitHub release with the changelog section as notes.

## License

[MIT](LICENSE)
