"""Public query API over immutable committed-row snapshots."""
from dataclasses import dataclass
from .columnar_chunk import ColumnarChunk, QuerySnapshot
from .morsel_scheduler import MorselScheduler
from .vectorized_operators import SelectionVector, aggregate_selections, bind_query


@dataclass(frozen=True)
class Morsel:
    chunk: ColumnarChunk
    start: int
    end: int


class QueryEngine:
    def __init__(self, workers=4, morsel_size=128, ring_capacity=64):
        if type(morsel_size) is not int or morsel_size < 1:
            raise ValueError("morsel size must be a positive integer")
        self.morsel_size = morsel_size
        self.scheduler = MorselScheduler(workers=workers, ring_capacity=ring_capacity)

    @property
    def last_stats(self):
        return self.scheduler.stats

    @staticmethod
    def snapshot(rows, schema=None):
        return QuerySnapshot.from_rows(rows, schema=schema)

    def execute(self, rows, group_by, aggregates, filters=()):
        snapshot = rows if isinstance(rows, QuerySnapshot) else self.snapshot(rows)
        return self.execute_snapshot(snapshot, group_by, aggregates, filters)

    def execute_snapshot(self, snapshot, group_by, aggregates, filters=()):
        if not isinstance(snapshot, QuerySnapshot):
            raise TypeError("execute_snapshot requires a QuerySnapshot")
        groups, bound_aggregates, bound_filters = bind_query(snapshot.schema, group_by, aggregates, filters,
            unknown_empty_schema=snapshot.row_count == 0 and not snapshot.explicit_schema)
        morsels = (Morsel(chunk, start, min(start + self.morsel_size, chunk.length))
                   for chunk in snapshot.chunks for start in range(0, chunk.length, self.morsel_size))

        def scan_filter(morsel):
            selection = SelectionVector.scan(morsel.chunk, morsel.start, morsel.end)
            for column, comparison, operand in bound_filters:
                selection = selection.filter(column, comparison, operand)
            return selection

        selections = self.scheduler.run(morsels, scan_filter)
        return aggregate_selections(selections, groups, bound_aggregates)
