"""Immutable contiguous typed columns and detached query snapshots."""
from __future__ import annotations
from array import array
from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping
MAX_CHUNK_ROWS = 1024


class ColumnType(str, Enum):
    INT64 = "Int64"
    FLOAT64 = "Float64"
    UTF8 = "Utf8String"
    NULL = "Null"


def normalize_type(value):
    if value is int:
        return ColumnType.INT64
    if value is float:
        return ColumnType.FLOAT64
    if value is str:
        return ColumnType.UTF8
    try:
        return ColumnType(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unsupported column type: {value!r}") from error


def scalar_type(value):
    if value is None:
        return ColumnType.NULL
    if type(value) is int:
        if not -(1 << 63) <= value < (1 << 63):
            raise OverflowError("integer column value exceeds signed Int64 range")
        return ColumnType.INT64
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Float64 values must be finite")
        return ColumnType.FLOAT64
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return ColumnType.UTF8
    raise TypeError(f"unsupported column value type: {type(value).__name__}")


def infer_type(values):
    result = ColumnType.NULL
    for value in values:
        current = scalar_type(value)
        if current is ColumnType.NULL:
            continue
        if result is ColumnType.NULL:
            result = current
        elif result is not current:
            if {result, current} <= {ColumnType.INT64, ColumnType.FLOAT64}:
                result = ColumnType.FLOAT64
            else:
                raise TypeError("column contains incompatible scalar types")
    return result


def _readonly_array(typecode, values):
    return memoryview(array(typecode, values).tobytes()).cast(typecode)


@dataclass(frozen=True)
class ValidityBitmap:
    words: memoryview
    length: int

    @classmethod
    def from_values(cls, values):
        words = [0] * ((len(values) + 63) // 64)
        for index, value in enumerate(values):
            if value is not None:
                words[index // 64] |= 1 << (index % 64)
        return cls(_readonly_array("Q", words), len(values))

    def is_valid(self, index):
        if not 0 <= index < self.length:
            raise IndexError(index)
        return bool(self.words[index // 64] & (1 << (index % 64)))


@dataclass(frozen=True)
class ColumnVector:
    dtype: ColumnType
    validity: ValidityBitmap
    data: memoryview
    offsets: memoryview | None = None

    @classmethod
    def from_values(cls, values, dtype=None):
        if len(values) > MAX_CHUNK_ROWS:
            raise ValueError("a column chunk holds at most 1024 rows")
        selected = infer_type(values) if dtype is None else normalize_type(dtype)
        for value in values:
            actual = scalar_type(value)
            if actual not in (ColumnType.NULL, selected):
                if not (selected is ColumnType.FLOAT64 and actual is ColumnType.INT64):
                    raise TypeError(f"value does not match {selected.value} schema")
        validity = ValidityBitmap.from_values(values)
        if selected is ColumnType.UTF8:
            offsets, payload = [0], bytearray()
            for value in values:
                if value is not None:
                    payload.extend(value.encode("utf-8"))
                offsets.append(len(payload))
            return cls(selected, validity, memoryview(bytes(payload)), _readonly_array("Q", offsets))
        if selected is ColumnType.FLOAT64:
            data = _readonly_array("d", (0.0 if value is None else float(value) for value in values))
        else:
            data = _readonly_array("q", (0 if value is None else value for value in values))
        return cls(selected, validity, data)

    def __len__(self):
        return self.validity.length

    def value_at(self, index):
        if not self.validity.is_valid(index):
            return None
        if self.dtype is ColumnType.UTF8:
            if self.offsets is None:
                raise RuntimeError("Utf8 column has no offsets")
            start, end = self.offsets[index], self.offsets[index + 1]
            return bytes(self.data[start:end]).decode("utf-8", errors="strict")
        return self.data[index]


@dataclass(frozen=True)
class ColumnarChunk:
    columns: Mapping[str, ColumnVector]
    length: int

    @classmethod
    def from_rows(cls, rows, schema=None):
        if len(rows) > MAX_CHUNK_ROWS:
            raise ValueError("a chunk holds at most 1024 rows")
        names = tuple(schema) if schema is not None else tuple(rows[0]) if rows else ()
        if any(type(name) is not str or not name for name in names):
            raise ValueError("column names must be nonempty strings")
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != set(names):
                raise ValueError("every row must contain exactly the schema columns")
        columns = {name: ColumnVector.from_values([row[name] for row in rows],
                   None if schema is None else schema[name]) for name in names}
        return cls(MappingProxyType(columns), len(rows))

    def row_at(self, index):
        if not 0 <= index < self.length:
            raise IndexError(index)
        return {name: vector.value_at(index) for name, vector in self.columns.items()}


@dataclass(frozen=True)
class QuerySnapshot:
    chunks: tuple[ColumnarChunk, ...]
    column_types: tuple[tuple[str, ColumnType], ...]
    row_count: int
    explicit_schema: bool = False

    @property
    def schema(self):
        return MappingProxyType(dict(self.column_types))

    @classmethod
    def from_rows(cls, rows, schema=None):
        copied = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("rows must be mappings")
            copied.append(dict(row))
        names = tuple(schema) if schema is not None else tuple(copied[0]) if copied else ()
        if any(type(name) is not str or not name for name in names):
            raise ValueError("column names must be nonempty strings")
        for row in copied:
            if set(row) != set(names):
                raise ValueError("every row must contain exactly the schema columns")
        resolved = {name: normalize_type(schema[name]) if schema is not None
                    else infer_type(row[name] for row in copied) for name in names}
        chunks = tuple(ColumnarChunk.from_rows(copied[start:start + MAX_CHUNK_ROWS], resolved)
                       for start in range(0, len(copied), MAX_CHUNK_ROWS))
        return cls(chunks, tuple(resolved.items()), len(copied), schema is not None)

    def iter_rows(self):
        for chunk in self.chunks:
            for index in range(chunk.length):
                yield chunk.row_at(index)
