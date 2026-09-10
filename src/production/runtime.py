"""Wall-clock reactor and OS-thread durable-write worker using native channels."""
from collections import deque
import heapq
import threading
import time
from src.common.prng import Xorshift128Plus
from .channels import NativeChannel


class RealEnvironment:
    TICK_SECONDS = 0.01

    def __init__(self, seed):
        self.prng = Xorshift128Plus(seed)
        self.trace = deque(maxlen=2048)
        self.events = []
        self.serial = 0

    @property
    def now(self):
        return int(time.monotonic() / self.TICK_SECONDS)

    def schedule(self, delay, callback, label=""):
        self.serial += 1
        heapq.heappush(self.events, (time.monotonic() + delay * self.TICK_SECONDS,
                                     self.serial, callback, label))

    def poll(self):
        count = 0
        while self.events and self.events[0][0] <= time.monotonic() and count < 1000:
            _, _, callback, _ = heapq.heappop(self.events)
            callback()
            count += 1


class DiskWorker:
    def __init__(self):
        self.commands = NativeChannel()
        self.completions = NativeChannel()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="aegis-fsync", daemon=True)
        self.thread.start()

    def submit(self, function, callback):
        if not self.commands.put((function, callback)):
            raise RuntimeError("durability queue capacity exceeded")

    def _run(self):
        while not self.stopping.is_set():
            job = self.commands.get()
            if job is None:
                self.stopping.wait(0.001)
                continue
            function, callback = job
            try:
                value, error = function(), None
            except Exception as failure:
                value, error = None, failure
            while not self.completions.put((callback, value, error)):
                if self.stopping.wait(0.001):
                    return

    def poll(self):
        for _ in range(1024):
            completion = self.completions.get()
            if completion is None:
                return
            callback, value, error = completion
            callback(value, error)

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("durability worker failed to stop")
        self.commands.close()
        self.completions.close()
