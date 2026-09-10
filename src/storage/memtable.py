"""Deterministic bounded-height skiplist with atomic public operations."""
from threading import RLock
from .bloom_filter import murmur3
from .codec import encode_key, encode_value
MAX_LEVEL = 24


class _Node:
    __slots__ = ("key", "value", "version", "next")

    def __init__(self, key, value, version, height):
        self.key, self.value, self.version = key, value, version
        self.next = [None] * height


class MemTable:
    def __init__(self):
        self._head = _Node("", None, 0, MAX_LEVEL)
        self._size = self._bytes = 0
        self._lock = RLock()

    @staticmethod
    def _height(key):
        bits = murmur3(key, 0x9E3779B9)
        height = 1
        while height < MAX_LEVEL and bits & 1:
            height += 1
            bits >>= 1
        return height

    def put(self, key, value, version):
        key_bytes, value_bytes = encode_key(key), encode_value(value)
        if type(version) is not int or not 1 <= version < (1 << 64):
            raise ValueError("version must be positive UInt64")
        with self._lock:
            update = [self._head] * MAX_LEVEL
            cursor = self._head
            for level in range(MAX_LEVEL - 1, -1, -1):
                while cursor.next[level] is not None and cursor.next[level].key < key:
                    cursor = cursor.next[level]
                update[level] = cursor
            found = cursor.next[0]
            if found is not None and found.key == key:
                if version < found.version:
                    raise ValueError("overwrite has an older version")
                self._bytes += len(value_bytes) - len(encode_value(found.value))
                found.value, found.version = value, version
                return
            node = _Node(key, value, version, self._height(key))
            for level in range(len(node.next)):
                node.next[level] = update[level].next[level]
                update[level].next[level] = node
            self._size += 1
            self._bytes += len(key_bytes) + len(value_bytes) + 16

    def lookup(self, key):
        encode_key(key)
        with self._lock:
            cursor = self._head
            for level in range(MAX_LEVEL - 1, -1, -1):
                while cursor.next[level] is not None and cursor.next[level].key < key:
                    cursor = cursor.next[level]
            cursor = cursor.next[0]
            return None if cursor is None or cursor.key != key else (cursor.version, cursor.value)

    def get(self, key, default=None):
        entry = self.lookup(key)
        return default if entry is None else entry[1]

    def entries(self):
        with self._lock:
            result = []
            cursor = self._head.next[0]
            while cursor is not None:
                result.append((cursor.key, cursor.version, cursor.value))
                cursor = cursor.next[0]
            return result

    def items(self):
        return [(key, value) for key, _, value in self.entries()]

    @property
    def byte_size(self):
        with self._lock:
            return self._bytes

    def __len__(self):
        with self._lock:
            return self._size
