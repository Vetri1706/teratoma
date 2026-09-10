"""Incremental stable multiway merging; publication belongs to the LSM clock."""
import heapq
from .sstable import SSTable


class Compaction:
    def __init__(self, sources, level, publication_index, output_name):
        self.sources = tuple(sources)
        self.level = level
        self.publication_index = publication_index
        self.output_name = output_name
        self.input_count = sum(len(table.entries) for _, table in sources)
        self._heap, self._output = [], []
        self.result = None
        for number, (_, table) in enumerate(self.sources):
            if table.entries:
                key, version, value = table.entries[0]
                heapq.heappush(self._heap, (key, number, 0, version, value))
        if not self._heap:
            self.result = SSTable([], level)

    def advance(self, budget):
        if type(budget) is not int or budget < 0:
            raise ValueError("compaction budget must be nonnegative")
        spent = 0
        while self._heap and spent < budget:
            key, number, position, version, value = heapq.heappop(self._heap)
            if self._output and self._output[-1][0] == key:
                if version > self._output[-1][1]:
                    self._output[-1] = (key, version, value)
                elif version == self._output[-1][1] and value != self._output[-1][2]:
                    raise ValueError("same-version conflicting storage values")
            else:
                self._output.append((key, version, value))
            position += 1
            entries = self.sources[number][1].entries
            if position < len(entries):
                next_key, next_version, next_value = entries[position]
                heapq.heappush(self._heap, (next_key, number, position, next_version, next_value))
            spent += 1
        if not self._heap and self.result is None:
            self.result = SSTable(self._output, self.level)
        return spent
