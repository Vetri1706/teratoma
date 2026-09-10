"""MurmurHash3 and a deterministic Bloom filter with a 0.5% design target."""
import math
import struct
from src.common.hashing import murmur3_32


def murmur3(data, seed=0):
    return murmur3_32(data.encode("utf-8") if isinstance(data, str) else data, seed)


class BloomFilter:
    def __init__(self, capacity, false_positive_rate=0.005):
        if type(capacity) is not int or capacity < 0:
            raise ValueError("capacity must be nonnegative")
        if not 0 < false_positive_rate < 1:
            raise ValueError("false-positive rate must be in (0,1)")
        size = math.ceil(-max(1, capacity) * math.log(false_positive_rate) / math.log(2) ** 2)
        self.bit_count = max(64, ((size + 63) // 64) * 64)
        self.hash_count = max(1, min(32, round(self.bit_count / max(1, capacity) * math.log(2))))
        self.bits = bytearray(self.bit_count // 8)

    def _positions(self, key):
        raw = key.encode("utf-8") if isinstance(key, str) else key
        h1 = murmur3(raw, 0xA3B19535)
        h2 = murmur3(raw, 0x1B873593) | 1
        for index in range(self.hash_count):
            yield (h1 + index * h2) % self.bit_count

    def add(self, key):
        for bit in self._positions(key):
            self.bits[bit // 8] |= 1 << (bit % 8)

    def might_contain(self, key):
        return all(self.bits[bit // 8] & (1 << (bit % 8)) for bit in self._positions(key))

    def to_bytes(self):
        return struct.pack(">II", self.bit_count, self.hash_count) + bytes(self.bits)

    @classmethod
    def from_bytes(cls, raw):
        if len(raw) < 16:
            raise ValueError("truncated Bloom filter")
        bit_count, hash_count = struct.unpack(">II", raw[:8])
        if bit_count < 64 or bit_count % 64 or len(raw) != 8 + bit_count // 8 or not 1 <= hash_count <= 32:
            raise ValueError("invalid Bloom filter bounds")
        result = cls(0)
        result.bit_count, result.hash_count = bit_count, hash_count
        result.bits = bytearray(raw[8:])
        return result
