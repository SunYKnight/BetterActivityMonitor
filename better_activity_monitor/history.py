"""Fixed-size ring buffers for all sampled series, sharing one time axis."""

from array import array
from bisect import bisect_left

from better_activity_monitor.util import NAN


class History:
    """Every series has one slot per sample; series missing from a sample hold NaN.

    array('d') keeps the footprint at 8 bytes per value, so an hour of samples for a few hundred series stays in the
    single-digit megabytes. Only the GTK main thread touches instances.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.times = array("d", bytes(8 * capacity))
        self.series: dict[str, array] = {}
        self.head = 0
        self.count = 0

    def push(self, t: float, values: dict[str, float]) -> None:
        slot = self.head
        self.times[slot] = t
        for key in self.series.keys() - values.keys():
            self.series[key][slot] = NAN
        for key, value in values.items():
            ring = self.series.get(key)
            if ring is None:
                ring = array("d", [NAN]) * self.capacity
                self.series[key] = ring
            ring[slot] = value
        self.head = (slot + 1) % self.capacity
        self.count = min(self.count + 1, self.capacity)

    def latest_time(self) -> float:
        return self.times[(self.head - 1) % self.capacity] if self.count else 0.0

    def latest(self, key: str) -> float:
        ring = self.series.get(key)
        return ring[(self.head - 1) % self.capacity] if ring is not None and self.count else NAN

    def _chronological(self, ring: array) -> array:
        result = ring[: self.count]
        if self.count == self.capacity:
            result = ring[self.head :] + ring[: self.head]
        return result

    def window(self, keys: list[str], since: float) -> tuple[array, list[array]]:
        """Samples with t >= since, oldest first; unknown keys yield all-NaN columns."""
        times = self._chronological(self.times)
        first = bisect_left(times, since)
        columns = []
        for key in keys:
            ring = self.series.get(key)
            column = self._chronological(ring)[first:] if ring is not None else array("d", [NAN]) * (len(times) - first)
            columns.append(column)
        return times[first:], columns

    def clear(self) -> None:
        self.series.clear()
        self.head = 0
        self.count = 0
