"""State shared by the window, pages and widgets on the GTK main thread."""

from dataclasses import dataclass, field

from better_activity_monitor.history import History
from better_activity_monitor.hwinfo import Inventory
from better_activity_monitor.sampler import Sample
from better_activity_monitor.settings import Settings


@dataclass
class Context:
    inventory: Inventory
    history: History
    settings: Settings
    sample: Sample | None = None
    # Called with no arguments after pinned sensors change, so the overview can rebuild its sensor group.
    pins_changed: list = field(default_factory=list)

    def value(self, key: str) -> float:
        return self.sample.values.get(key, float("nan")) if self.sample else float("nan")

    def pinned(self) -> list[str]:
        pins = self.settings.pinned
        return self.inventory.default_pins() if pins is None else pins
