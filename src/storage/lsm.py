"""Three-level LSM; file publication follows deterministic commit milestones."""
import hashlib
import struct
from .codec import encode_key, encode_value
from .memtable import MemTable
from .sstable import SSTable
from .compaction import Compaction


class LSMTree:
    def __init__(self, flush_every=16, memtable_max_bytes=1 << 20, compaction_budget=32):
        if type(flush_every) is not int or flush_every < 1:
            raise ValueError("flush_every must be positive")
        if type(memtable_max_bytes) is not int or memtable_max_bytes < 32:
            raise ValueError("memtable bound is too small")
        if type(compaction_budget) is not int or compaction_budget < 1:
            raise ValueError("compaction budget must be positive")
        self.flush_every = flush_every
        self.memtable_max_bytes = memtable_max_bytes
        self.compaction_budget = compaction_budget
        self.index = 0
        self.memtable = MemTable()
        self._tables = {}
        self._serial = 0
        self._job = None

    def _name(self, level):
        self._serial += 1
        return f"L{level}-{self.index:020d}-{self._serial:08d}.sst"

    def _flush(self):
        if len(self.memtable):
            self._tables[self._name(0)] = SSTable(self.memtable.entries(), 0)
            self.memtable = MemTable()

    def apply(self, index, writes):
        if type(index) is not int or index != self.index + 1 or index >= (1 << 64):
            raise ValueError("LSM commits must be consecutive UInt64 starting at one")
        if not isinstance(writes, dict):
            raise TypeError("writes must be a dictionary")
        checked = []
        for key, value in writes.items():
            size = len(encode_key(key)) + len(encode_value(value)) + 16
            if size > self.memtable_max_bytes:
                raise ValueError("entry exceeds the memtable size bound")
            checked.append((key, value, size))
        checked.sort(key=lambda entry: entry[0])
        self.index = index
        for key, value, size in checked:
            if self.memtable.byte_size + size > self.memtable_max_bytes:
                self._flush()
            self.memtable.put(key, value, index)
        if index % self.flush_every == 0:
            self._flush()
        if self._job is not None:
            self._job.advance(self.compaction_budget)
            if index >= self._job.publication_index:
                if self._job.result is None:
                    raise AssertionError("compaction missed deterministic work deadline")
                for name, _ in self._job.sources:
                    del self._tables[name]
                self._tables[self._job.output_name] = self._job.result
                self._job = None
        self._schedule()

    def _schedule(self):
        if self._job is not None:
            return
        levels = {level: [(name, table) for name, table in sorted(self._tables.items()) if table.level == level]
                  for level in (0, 1, 2)}
        if len(levels[1]) >= 4:
            sources, level = levels[1][:4] + levels[2], 2
        elif len(levels[0]) >= 4:
            sources, level = levels[0][:4], 1
        else:
            return
        rows = sum(len(table.entries) for _, table in sources)
        deadline = self.index + max(1, (rows + self.compaction_budget - 1) // self.compaction_budget)
        self._job = Compaction(sources, level, deadline, self._name(level))

    def advance(self, budget=32):
        if type(budget) is not int or budget < 0:
            raise ValueError("compaction budget must be nonnegative")
        return 0 if self._job is None else self._job.advance(budget)

    def drain(self):
        """Prepare current merge; never advance the commit publication clock."""
        return 0 if self._job is None else self._job.advance(self._job.input_count)

    def lookup(self, key):
        found = self.memtable.lookup(key)
        for table in self._tables.values():
            candidate = table.lookup(key)
            if candidate is not None and (found is None or candidate[0] > found[0]):
                found = candidate
        return found

    def get(self, key, default=None):
        entry = self.lookup(key)
        return default if entry is None else entry[1]

    def items(self):
        merged = {}
        for table in self._tables.values():
            for key, version, value in table.entries:
                if key not in merged or version > merged[key][0]:
                    merged[key] = (version, value)
        for key, version, value in self.memtable.entries():
            if key not in merged or version > merged[key][0]:
                merged[key] = (version, value)
        return [(key, merged[key][1]) for key in sorted(merged)]

    def snapshot(self):
        return dict(self.items())

    def files(self):
        return {name: table.to_bytes() for name, table in sorted(self._tables.items())}

    def digest(self):
        digest = hashlib.sha256()
        for name, raw in self.files().items():
            encoded = name.encode("ascii")
            digest.update(struct.pack(">IQ", len(encoded), len(raw)))
            digest.update(encoded)
            digest.update(raw)
        return digest.hexdigest()
