"""Stable discrete events and invariant checks at every integer virtual tick."""
import heapq
from src.common import Xorshift128Plus


class SimEnvironment:
    def __init__(self, seed, trace_limit=4096, max_events_per_tick=100_000):
        if trace_limit < 0 or max_events_per_tick <= 0:
            raise ValueError("invalid trace or event limit")
        self.seed = seed
        self.now = 0
        self.prng = Xorshift128Plus(seed)
        self.trace = []
        self.trace_limit = trace_limit
        self.max_events_per_tick = max_events_per_tick
        self.counters = {"scheduled": 0, "executed": 0, "ticks": 0, "max_pending": 0}
        self._events = []
        self._sequence = 0
        self._running = False
        self._last_checked_tick = -1

    @property
    def pending(self):
        return len(self._events)

    def record(self, kind, **fields):
        if self.trace_limit == 0:
            return
        self.trace.append({"tick": self.now, "kind": kind, **fields})
        overflow = len(self.trace) - self.trace_limit
        if overflow > 0:
            del self.trace[:overflow]

    def schedule(self, delay, callback, label=""):
        if not isinstance(delay, int) or delay < 0:
            raise ValueError("event delay must be a nonnegative integer")
        if not callable(callback):
            raise TypeError("event callback must be callable")
        self._sequence += 1
        heapq.heappush(self._events, (self.now + delay, self._sequence, str(label), callback))
        self.counters["scheduled"] += 1
        self.counters["max_pending"] = max(self.counters["max_pending"], len(self._events))
        return self._sequence

    def run(self, until, check=None):
        """Run through inclusive absolute tick, checking idle ticks as well."""
        if not isinstance(until, int) or until < self.now:
            raise ValueError("run horizon must be an integer at or after now")
        if self._running:
            raise RuntimeError("nested simulator runs are not supported")
        self._running = True
        try:
            while self.now <= until:
                count = 0
                while self._events and self._events[0][0] <= self.now:
                    if count >= self.max_events_per_tick:
                        raise RuntimeError(f"event loop exceeded per-tick limit at tick {self.now}")
                    _, sequence, label, callback = heapq.heappop(self._events)
                    self.record("event", sequence=sequence, label=label)
                    self.counters["executed"] += 1
                    count += 1
                    callback()
                if self._last_checked_tick < self.now or count:
                    if check is not None:
                        check()
                    if self._last_checked_tick < self.now:
                        self.counters["ticks"] += 1
                    self._last_checked_tick = self.now
                if self.now == until:
                    break
                self.now += 1
        finally:
            self._running = False
