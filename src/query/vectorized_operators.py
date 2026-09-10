"""Selection-vector filtering and ordered null-aware aggregation."""
from dataclasses import dataclass
import math
import operator
from typing import Mapping
from .columnar_chunk import ColumnarChunk, ColumnType, scalar_type
COMPARISONS = {"=": operator.eq, "==": operator.eq, "!=": operator.ne, "<>": operator.ne,
               "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


@dataclass(frozen=True)
class SelectionVector:
    chunk: ColumnarChunk
    indices: tuple[int, ...]

    @classmethod
    def scan(cls, chunk, start=0, end=None):
        end = chunk.length if end is None else end
        if not 0 <= start <= end <= chunk.length:
            raise ValueError("selection bounds are outside the chunk")
        return cls(chunk, tuple(range(start, end)))

    def filter(self, column, comparison, operand):
        vector = self.chunk.columns[column]
        if comparison == "is_null":
            indices = tuple(index for index in self.indices if not vector.validity.is_valid(index))
        elif comparison == "is_not_null":
            indices = tuple(index for index in self.indices if vector.validity.is_valid(index))
        else:
            predicate = COMPARISONS[comparison]
            indices = tuple(index for index in self.indices if operand is not None
                            and vector.validity.is_valid(index) and predicate(vector.value_at(index), operand))
        return SelectionVector(self.chunk, indices)


@dataclass(frozen=True)
class Aggregate:
    alias: str
    operation: str
    column: str | None


def bind_query(schema, group_by, aggregates, filters, unknown_empty_schema=False):
    if isinstance(group_by, (str, bytes)):
        raise TypeError("group_by must be a sequence of column names")
    groups = tuple(group_by)
    if any(type(name) is not str or not name for name in groups):
        raise ValueError("grouping columns must be nonempty strings")
    if len(set(groups)) != len(groups):
        raise ValueError("duplicate grouping column")
    if not isinstance(aggregates, Mapping):
        raise TypeError("aggregates must map output names to operation/column pairs")

    def column_type(column):
        if type(column) is not str or not column:
            raise ValueError("column references must be nonempty strings")
        if column not in schema:
            if unknown_empty_schema:
                return ColumnType.NULL
            raise ValueError(f"unknown column: {column}")
        return schema[column]

    for group in groups:
        column_type(group)
    bound_aggregates = []
    for alias, specification in aggregates.items():
        if type(alias) is not str or not alias:
            raise ValueError("aggregate output names must be nonempty strings")
        if alias in groups:
            raise ValueError("aggregate output name collides with grouping column")
        if not isinstance(specification, (tuple, list)) or len(specification) != 2:
            raise ValueError("aggregate specification must contain an operation and column")
        operation, column = specification
        if type(operation) is not str or operation.lower() not in {"sum", "count", "min", "max", "avg"}:
            raise ValueError(f"unsupported aggregate: {operation!r}")
        operation = operation.lower()
        dtype = ColumnType.NULL if column is None and operation == "count" else column_type(column)
        if operation in {"sum", "avg"} and dtype not in {ColumnType.INT64, ColumnType.FLOAT64, ColumnType.NULL}:
            raise TypeError(f"{operation} requires a numeric column")
        bound_aggregates.append(Aggregate(alias, operation, column))
    if not groups and not bound_aggregates:
        raise ValueError("a query must project groups or aggregates")
    bound_filters = []
    for specification in filters:
        if not isinstance(specification, (tuple, list)) or len(specification) != 3:
            raise ValueError("filter specification must contain column, operator, and operand")
        column, comparison, operand = specification
        dtype = column_type(column)
        if type(comparison) is not str:
            raise ValueError("filter operator must be a string")
        comparison = comparison.lower()
        if comparison not in COMPARISONS and comparison not in {"is_null", "is_not_null"}:
            raise ValueError(f"unsupported filter operator: {comparison}")
        if comparison in {"is_null", "is_not_null"}:
            if operand is not None:
                raise ValueError("null predicates require a None operand")
        else:
            actual = scalar_type(operand)
            numeric = {ColumnType.INT64, ColumnType.FLOAT64}
            if dtype is not ColumnType.NULL and actual is not ColumnType.NULL and dtype is not actual:
                if not {dtype, actual} <= numeric:
                    raise TypeError("filter operand type does not match the column")
        bound_filters.append((column, comparison, operand))
    return groups, tuple(bound_aggregates), tuple(bound_filters)


class AggregateState:
    def __init__(self, aggregate):
        self.aggregate = aggregate
        self.count, self.value = 0, None

    def update(self, value):
        operation = self.aggregate.operation
        if operation == "count":
            if self.aggregate.column is None or value is not None:
                self.count += 1
            return
        if value is None:
            return
        self.count += 1
        if self.value is None:
            self.value = value
        elif operation in {"sum", "avg"}:
            self.value += value
        elif operation == "min":
            self.value = min(self.value, value)
        elif operation == "max":
            self.value = max(self.value, value)
        else:
            raise RuntimeError("unbound aggregate reached execution")

    def finish(self):
        if self.aggregate.operation == "count":
            return self.count
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise OverflowError("aggregate exceeds finite Float64 range")
        if self.aggregate.operation == "avg" and self.count:
            return self.value / self.count
        return self.value


def aggregate_selections(selections, groups, aggregates):
    grouped = {}
    if not groups:
        grouped[()] = [AggregateState(aggregate) for aggregate in aggregates]
    for selection in selections:
        columns = selection.chunk.columns
        for index in selection.indices:
            key = tuple(columns[group].value_at(index) for group in groups)
            if key not in grouped:
                grouped[key] = [AggregateState(aggregate) for aggregate in aggregates]
            for state in grouped[key]:
                column = state.aggregate.column
                state.update(None if column is None else columns[column].value_at(index))
    output = []
    for key, states in grouped.items():
        row = dict(zip(groups, key))
        row.update((state.aggregate.alias, state.finish()) for state in states)
        output.append(row)
    return output
