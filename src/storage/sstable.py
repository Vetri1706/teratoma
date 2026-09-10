"""Canonical sorted tables with per-record CRC, sparse indexes, and Bloom filters."""
from bisect import bisect_right
import hashlib
import struct
import zlib
from .bloom_filter import BloomFilter
from .codec import MAX_FIELD, encode_key, encode_value, decode_value
MAGIC = b"AEGSST01"
HEADER = struct.Struct(">8sBIIII")
RECORD = struct.Struct(">IQI")
INDEX_STRIDE = 32


class SSTable:
    def __init__(self, entries, level=0):
        if type(level) is not int or level not in (0, 1, 2):
            raise ValueError("SSTable level must be 0, 1, or 2")
        self.level = level
        self.entries = tuple(entries)
        self.bloom = BloomFilter(len(self.entries))
        data, index = bytearray(), bytearray()
        self.sparse_keys, self.sparse_offsets = [], []
        previous = None
        for position, (key, version, value) in enumerate(self.entries):
            key_bytes, value_bytes = encode_key(key), encode_value(value)
            if previous is not None and key <= previous:
                raise ValueError("SSTable keys must be strictly increasing")
            if type(version) is not int or not 1 <= version < (1 << 64):
                raise ValueError("SSTable version must be positive UInt64")
            previous = key
            if position % INDEX_STRIDE == 0:
                self.sparse_keys.append(key)
                self.sparse_offsets.append(len(data))
                index += struct.pack(">IQ", len(key_bytes), len(data)) + key_bytes
            body = RECORD.pack(len(key_bytes), version, len(value_bytes)) + key_bytes + value_bytes
            data += body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            self.bloom.add(key_bytes)
        bloom = self.bloom.to_bytes()
        header = HEADER.pack(MAGIC, level, len(self.entries), len(data), len(index), len(bloom))
        body = header + bytes(data) + bytes(index) + bloom
        self.data = body + hashlib.sha256(body).digest()

    def lookup(self, key):
        encode_key(key)
        if not self.bloom.might_contain(key):
            return None
        block = bisect_right(self.sparse_keys, key) - 1
        if block < 0:
            return None
        start = block * INDEX_STRIDE
        for candidate, version, value in self.entries[start:start + INDEX_STRIDE]:
            if candidate == key:
                return version, value
            if candidate > key:
                break
        return None

    def get(self, key, default=None):
        found = self.lookup(key)
        return default if found is None else found[1]

    def to_bytes(self):
        return self.data

    @classmethod
    def from_bytes(cls, raw):
        if not isinstance(raw, bytes) or len(raw) < HEADER.size + 32:
            raise ValueError("truncated SSTable")
        body, checksum = raw[:-32], raw[-32:]
        if hashlib.sha256(body).digest() != checksum:
            raise ValueError("SSTable digest mismatch")
        magic, level, count, data_len, index_len, bloom_len = HEADER.unpack(body[:HEADER.size])
        if magic != MAGIC or level not in (0, 1, 2) or len(body) != HEADER.size + data_len + index_len + bloom_len:
            raise ValueError("invalid SSTable header")
        data = body[HEADER.size:HEADER.size + data_len]
        entries, offset = [], 0
        for _ in range(count):
            if len(data) - offset < RECORD.size:
                raise ValueError("truncated SSTable record")
            key_len, version, value_len = RECORD.unpack(data[offset:offset + RECORD.size])
            end = offset + RECORD.size + key_len + value_len
            if key_len > MAX_FIELD or value_len > MAX_FIELD + 5 or end + 4 > len(data):
                raise ValueError("invalid SSTable record bounds")
            expected_crc = struct.unpack(">I", data[end:end + 4])[0]
            if zlib.crc32(data[offset:end]) & 0xFFFFFFFF != expected_crc:
                raise ValueError("SSTable record checksum mismatch")
            begin = offset + RECORD.size
            key = data[begin:begin + key_len].decode("utf-8")
            value = decode_value(data[begin + key_len:end])
            entries.append((key, version, value))
            offset = end + 4
        if offset != len(data):
            raise ValueError("trailing SSTable record bytes")
        result = cls(entries, level)
        if result.data != raw:
            raise ValueError("noncanonical SSTable index or Bloom filter")
        return result
