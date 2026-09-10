"""Persistent block storage with async faults and crash-generation cancellation."""
BLOCK_SIZE = 4096


class VirtualDisk:
    def __init__(self, env):
        self.env = env
        self.blocks = {}
        self.generation = 0
        self.version = 0
        self.read_fail_rate = 0
        self.torn_write_rate = 0
        self.stall_rate = 0
        self.min_latency = 1
        self.max_latency = 3
        self.stats = {"reads": 0, "writes": 0, "read_failures": 0, "torn_writes": 0,
                      "stalls": 0, "crashes": 0, "cancelled": 0, "corruptions": 0,
                      "completed_reads": 0, "completed_writes": 0, "erases": 0}

    @property
    def revision(self):
        return self.version

    def _validate(self, block):
        if not isinstance(block, int) or block < 0:
            raise ValueError("block address must be a nonnegative integer")
        if not 0 <= self.min_latency <= self.max_latency:
            raise ValueError("invalid disk latency range")
        for rate in (self.read_fail_rate, self.torn_write_rate, self.stall_rate):
            if not isinstance(rate, int) or not 0 <= rate <= 1000:
                raise ValueError("disk fault rates must be integers in [0, 1000]")

    def _latency(self):
        delay = self.min_latency + self.env.prng.randbelow(self.max_latency - self.min_latency + 1)
        if self.env.prng.chance(self.stall_rate):
            delay += max(1, self.max_latency) * (5 + self.env.prng.randbelow(16))
            self.stats["stalls"] += 1
        return delay

    def write(self, block, data, callback):
        self._validate(block)
        if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) != BLOCK_SIZE:
            raise ValueError(f"writes require exactly {BLOCK_SIZE} bytes")
        if not callable(callback):
            raise TypeError("write completion callback must be callable")
        wire = bytes(data)
        generation = self.generation
        self.stats["writes"] += 1
        torn = self.env.prng.chance(self.torn_write_rate)
        length = 1 + self.env.prng.randbelow(BLOCK_SIZE - 1) if torn else BLOCK_SIZE

        def complete():
            if generation != self.generation:
                self.stats["cancelled"] += 1
                return
            if torn:
                previous = self.blocks.get(block, bytes(BLOCK_SIZE))
                self.blocks[block] = wire[:length] + previous[length:]
                self.version += 1
                self.stats["torn_writes"] += 1
                self.env.record("disk_torn_write", block=block, bytes_written=length)
                callback(False)
            else:
                self.blocks[block] = wire
                self.version += 1
                self.stats["completed_writes"] += 1
                callback(True)

        self.env.schedule(self._latency(), complete, label=f"disk:write:{block}")

    def read(self, block, callback):
        self._validate(block)
        if not callable(callback):
            raise TypeError("read completion callback must be callable")
        generation = self.generation
        self.stats["reads"] += 1
        failure = self.env.prng.chance(self.read_fail_rate)

        def complete():
            if generation != self.generation:
                self.stats["cancelled"] += 1
                return
            if failure:
                self.stats["read_failures"] += 1
                callback(None)
            else:
                self.stats["completed_reads"] += 1
                callback(self.blocks.get(block))

        self.env.schedule(self._latency(), complete, label=f"disk:read:{block}")

    def erase(self, callback):
        """Atomic virtual-device erase; not an ordinary filesystem guarantee."""
        self._validate(0)
        if not callable(callback):
            raise TypeError("erase completion callback must be callable")
        generation = self.generation

        def complete():
            if generation != self.generation:
                self.stats["cancelled"] += 1
                return
            self.blocks.clear()
            self.version += 1
            self.stats["erases"] += 1
            self.env.record("disk_erase", generation=generation)
            callback(True)

        self.env.schedule(self._latency(), complete, label="disk:erase")

    def crash(self):
        self.generation += 1
        self.stats["crashes"] += 1
        self.env.record("disk_crash", generation=self.generation)

    def corrupt(self, block, offset=0, bit=0):
        self._validate(block)
        if not isinstance(offset, int) or not 0 <= offset < BLOCK_SIZE:
            raise ValueError("corruption offset outside block")
        if not isinstance(bit, int) or not 0 <= bit < 8:
            raise ValueError("corruption bit must be in [0, 8)")
        if block not in self.blocks:
            return False
        data = bytearray(self.blocks[block])
        data[offset] ^= 1 << bit
        self.blocks[block] = bytes(data)
        self.version += 1
        self.stats["corruptions"] += 1
        self.env.record("disk_corrupt", block=block, offset=offset, bit=bit)
        return True
