"""Formatting helpers and cheap procfs/sysfs readers."""

import math
import os

NAN = float("nan")
DASH = "—"

_DECIMAL_UNITS = ("B", "kB", "MB", "GB", "TB", "PB")
_BINARY_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def read_text(path: str) -> str | None:
    """Read and strip a small text file; None when the attribute is absent or unreadable."""
    result = None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            result = handle.read().strip()
    except OSError:
        result = None
    return result


def read_int(path: str, base: int = 10) -> int | None:
    text = read_text(path)
    result = None
    if text:
        try:
            result = int(text.split()[0], base)
        except ValueError:
            result = None
    return result


def link_name(path: str) -> str:
    """Basename of a symlink target, e.g. the driver bound to a device."""
    result = ""
    if os.path.lexists(path):
        result = os.path.basename(os.path.realpath(path))
    return result


class PolledFiles:
    """Keeps descriptors of files that are read every tick open.

    procfs seq_files and sysfs attributes regenerate their content on each read at offset 0, so a pread() on a
    cached descriptor replaces the open/read/close triple per sample.
    """

    def __init__(self) -> None:
        self._fds: dict[str, int] = {}
        self._sizes: dict[str, int] = {}

    def read(self, path: str) -> str | None:
        result = None
        try:
            fd = self._fds.get(path)
            if fd is None:
                fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                self._fds[path] = fd
            size = self._sizes.get(path, 4096)
            data = os.pread(fd, size, 0)
            while len(data) >= size:
                size *= 2
                data = os.pread(fd, size, 0)
            self._sizes[path] = size
            result = data.decode("utf-8", "replace")
        except OSError:
            self.forget(path)
        return result

    def read_float(self, path: str, scale: float = 1.0) -> float:
        text = self.read(path)
        result = NAN
        if text:
            try:
                result = float(text.split()[0]) * scale
            except ValueError:
                result = NAN
        return result

    def forget(self, path: str) -> None:
        fd = self._fds.pop(path, None)
        if fd is not None:
            os.close(fd)

    def close(self) -> None:
        for fd in self._fds.values():
            os.close(fd)
        self._fds.clear()


def is_num(value) -> bool:
    return value is not None and math.isfinite(value)


def fmt_bytes(value, binary: bool = False) -> str:
    text = DASH
    if is_num(value):
        base = 1024.0 if binary else 1000.0
        units = _BINARY_UNITS if binary else _DECIMAL_UNITS
        index = 0
        scaled = float(value)
        while abs(scaled) >= base * 0.995 and index < len(units) - 1:
            scaled /= base
            index += 1
        if index == 0:
            text = f"{scaled:.0f} {units[0]}"
        else:
            digits = 2 if abs(scaled) < 10 else 1 if abs(scaled) < 100 else 0
            text = f"{scaled:.{digits}f} {units[index]}"
    return text


def fmt_rate(value) -> str:
    return fmt_bytes(value) + "/s" if is_num(value) else DASH


def fmt_percent(value) -> str:
    text = DASH
    if is_num(value):
        text = f"{value:.1f}%" if abs(value) < 10 else f"{value:.0f}%"
    return text


def fmt_temp(value) -> str:
    return f"{value:.0f} °C" if is_num(value) else DASH


def fmt_mhz(value) -> str:
    text = DASH
    if is_num(value):
        text = f"{value / 1000:.2f} GHz" if value >= 1000 else f"{value:.0f} MHz"
    return text


def fmt_watts(value) -> str:
    text = DASH
    if is_num(value):
        text = f"{value:.1f} W" if abs(value) < 100 else f"{value:.0f} W"
    return text


def fmt_volts(value) -> str:
    return f"{value:.3f} V" if is_num(value) else DASH


def fmt_amps(value) -> str:
    return f"{value:.2f} A" if is_num(value) else DASH


def fmt_rpm(value) -> str:
    return f"{value:.0f} rpm" if is_num(value) else DASH


def fmt_count_rate(value) -> str:
    text = DASH
    if is_num(value):
        text = f"{value / 1000:.1f} k/s" if value >= 10000 else f"{value:.0f} /s"
    return text


def fmt_duration(seconds) -> str:
    text = DASH
    if is_num(seconds):
        minutes = int(seconds // 60)
        days, rest = divmod(minutes, 1440)
        hours, mins = divmod(rest, 60)
        if days:
            text = f"{days} d {hours} h {mins} min"
        elif hours:
            text = f"{hours} h {mins} min"
        else:
            text = f"{mins} min"
    return text


def fmt_window(seconds: float) -> str:
    return f"{seconds / 3600:g} h" if seconds >= 3600 else f"{seconds / 60:g} min"


SENSOR_FORMATS = {
    "temp": fmt_temp,
    "fan": fmt_rpm,
    "in": fmt_volts,
    "power": fmt_watts,
    "curr": fmt_amps,
}


def fmt_sensor(kind: str, value) -> str:
    return SENSOR_FORMATS[kind](value)
