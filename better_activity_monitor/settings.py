"""User preferences persisted as JSON under $XDG_CONFIG_HOME."""

import json
import os
import sys
from dataclasses import asdict, dataclass, fields

INTERVALS = (0.5, 1.0, 2.0, 5.0)
WINDOWS = (60, 300, 900, 3600)


def _config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "better-activity-monitor", "settings.json")


@dataclass
class Settings:
    interval: float = 1.0
    window: int = 60
    stacked: bool = True
    width: int = 1280
    height: int = 880
    maximized: bool = False
    page: str = "overview"
    # None selects the default pin set computed from the detected sensors.
    pinned: list[str] | None = None

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        path = _config_path()
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                known = {field.name for field in fields(cls)}
                settings = cls(**{key: value for key, value in data.items() if key in known})
            except (OSError, ValueError, TypeError) as error:
                print(f"better-activity-monitor: ignoring unreadable settings {path}: {error}", file=sys.stderr)
        if settings.interval not in INTERVALS:
            settings.interval = 1.0
        if settings.window not in WINDOWS:
            settings.window = 60
        return settings

    def save(self) -> None:
        path = _config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2)
