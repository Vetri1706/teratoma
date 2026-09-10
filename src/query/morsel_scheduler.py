"""Deterministic cooperative work stealing; no cross-thread atomicity claim."""
from dataclasses import dataclass


class WorkStealingRing:
    """Bounded owner-LIFO/thief-FIFO ring for single-thread simulator fibers."""
    def __init__(self, capacity=64):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("ring capacity must be a positive integer")
        self.capacity = capacity
        self._slots = [None] * capacity
        self._top = self._bottom = 0

    def __len__(self):
        return self._bottom - self._top

    def push(self, item):
        if len(self) == self.capacity:
            return False
        self._slots[self._bottom % self.capacity] = item
        self._bottom += 1
        return True

    def pop(self):
        if not len(self):
            raise IndexError("pop from empty work ring")
        self._bottom -= 1
        slot = self._bottom % self.capacity
        item = self._slots[slot]
        self._slots[slot] = None
        return item

    def steal(self):
        if not len(self):
            raise IndexError("steal from empty work ring")
        slot = self._top % self.capacity
        item = self._slots[slot]
        self._slots[slot] = None
        self._top += 1
        return item


@dataclass
class SchedulerStats:
    submitted: int = 0
    completed: int = 0
    steals: int = 0
    backpressure_events: int = 0
    peak_ring_depth: int = 0


class MorselScheduler:
    """Lazy producer with bounded backpressure and ordered result delivery."""
    def __init__(self, workers=4, ring_capacity=64):
        if type(workers) is not int or workers < 1:
            raise ValueError("worker count must be a positive integer")
        self.workers = workers
        self.ring_capacity = WorkStealingRing(ring_capacity).capacity
        self.stats = SchedulerStats()

    def run(self, tasks, execute):
        rings = [WorkStealingRing(self.ring_capacity) for _ in range(self.workers)]
        self.stats = SchedulerStats()
        iterator, exhausted = iter(tasks), False
        results, round_number = {}, 0
        while not exhausted or any(len(ring) for ring in rings):
            while not exhausted and len(rings[0]) < self.ring_capacity:
                try:
                    task = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                ordinal = self.stats.submitted
                if not rings[0].push((ordinal, task)):
                    raise RuntimeError("producer exceeded checked ring capacity")
                self.stats.submitted += 1
                self.stats.peak_ring_depth = max(self.stats.peak_ring_depth, len(rings[0]))
            if not exhausted:
                self.stats.backpressure_events += 1
            for offset in range(self.workers):
                worker = (round_number + offset + 1) % self.workers
                if len(rings[worker]):
                    ordinal, task = rings[worker].pop()
                else:
                    victims = [(worker + distance) % self.workers for distance in range(1, self.workers)]
                    victim = next((candidate for candidate in victims if len(rings[candidate])), None)
                    if victim is None:
                        continue
                    ordinal, task = rings[victim].steal()
                    self.stats.steals += 1
                results[ordinal] = execute(task)
                self.stats.completed += 1
            round_number += 1
        if self.stats.completed != self.stats.submitted:
            raise RuntimeError("scheduler lost submitted work")
        return [results[index] for index in range(self.stats.submitted)]
