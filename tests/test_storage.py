import unittest
from src.storage import LSMTree, WAL, MemTable, SSTable, BloomFilter, murmur3
from src.storage.codec import encode_value, decode_value


class StorageTests(unittest.TestCase):
    def test_wal_multiblock_and_empty_records(self):
        payloads = [b"", b"first", bytes(range(256)) * 90]
        blocks = []
        for seq, payload in enumerate(payloads, 1):
            blocks.extend(WAL.encode_record(seq, payload))
        result = WAL.scan(iter(blocks))
        self.assertIsNone(result.error)
        self.assertEqual(result.records, list(enumerate(payloads, 1)))
        self.assertEqual(result.valid_blocks, len(blocks))

    def test_wal_truncates_whole_incomplete_record(self):
        first = WAL.encode_record(1, b"acknowledged")
        second = WAL.encode_record(2, b"b" * 12000)
        for partial in (second[:-1], [second[0][:100]], [second[0], second[1][:10]]):
            result = WAL.scan(first + partial)
            self.assertEqual(result.records, [(1, b"acknowledged")])
            self.assertEqual(result.valid_blocks, len(first))
            self.assertIsNotNone(result.error)

    def test_wal_corruption_and_sequence_gap(self):
        first = WAL.encode_record(1, b"stable")
        for offset in (0, 20, 100, 4095):
            damaged = bytearray(WAL.encode_record(2, b"payload")[0])
            damaged[offset] ^= 1
            result = WAL.scan(first + [bytes(damaged)])
            self.assertEqual(result.valid_blocks, 1)
            self.assertEqual(result.records, [(1, b"stable")])
            self.assertIsNotNone(result.error)
        self.assertIsNotNone(WAL.scan(first + WAL.encode_record(3, b"gap")).error)

    def test_primitive_codec_and_bounds(self):
        for value in (None, -(1 << 63), (1 << 63) - 1, 1.25, -0.0, "héllo\x00世界"):
            self.assertEqual(decode_value(encode_value(value)), value)
        for value in (True, [], 1 << 63, float("nan"), float("inf")):
            with self.assertRaises((ValueError, TypeError)):
                encode_value(value)
        for raw in (b"", b"\x00extra", b"\x09", b"\x01a", b"\x03\x00\x00\x00\x02x"):
            with self.assertRaises(ValueError):
                decode_value(raw)

    def test_memtable_order_and_overwrites(self):
        table = MemTable()
        for index, key in enumerate(("z", "b", "é", "", "a"), 1):
            table.put(key, index, index)
        table.put("b", None, 6)
        self.assertEqual(table.items(), [("", 4), ("a", 5), ("b", None), ("z", 1), ("é", 3)])
        self.assertEqual(table.lookup("b"), (6, None))
        self.assertIsNone(table.lookup("absent"))
        with self.assertRaises(ValueError):
            table.put("b", 3, 2)

    def test_murmur_and_bloom(self):
        self.assertEqual(murmur3(b""), 0)
        self.assertEqual(murmur3(b"foo"), 0xF6A5C420)
        self.assertEqual(murmur3(b"hello"), 0x248BFA47)
        bloom = BloomFilter(1000)
        for value in range(1000):
            bloom.add(f"present-{value}")
        restored = BloomFilter.from_bytes(bloom.to_bytes())
        self.assertTrue(all(restored.might_contain(f"present-{value}") for value in range(1000)))
        false_positives = sum(restored.might_contain(f"absent-{value}") for value in range(10000))
        self.assertLess(false_positives, 100)

    def test_sstable_roundtrip_sparse_lookup_and_damage(self):
        entries = [(f"k{n:04d}", n + 1, n if n % 2 else str(n)) for n in range(145)]
        table = SSTable(entries, 1)
        restored = SSTable.from_bytes(table.to_bytes())
        self.assertEqual(restored.entries, tuple(entries))
        self.assertEqual(restored.sparse_keys, [entries[n][0] for n in range(0, 145, 32)])
        for key, version, value in entries:
            self.assertEqual(restored.lookup(key), (version, value))
        self.assertIsNone(restored.lookup("k9999"))
        raw = bytearray(table.to_bytes())
        raw[len(raw) // 2] ^= 1
        with self.assertRaises(ValueError):
            SSTable.from_bytes(bytes(raw))
        with self.assertRaises(ValueError):
            SSTable.from_bytes(table.to_bytes()[:-1])

    def test_lsm_physical_determinism_under_different_pacing(self):
        left = LSMTree(flush_every=2, compaction_budget=3)
        right = LSMTree(flush_every=2, compaction_budget=3)
        expected = {}
        for index in range(1, 601):
            writes = {} if index % 7 == 0 else {f"k{index % 17}": index, "shared": index * 2}
            left.apply(index, writes)
            right.apply(index, dict(reversed(list(writes.items()))))
            left.advance(1)
            right.advance(10000)
            expected.update(writes)
            self.assertEqual(left.snapshot(), expected)
            self.assertEqual(left.files(), right.files())
            self.assertEqual(left.digest(), right.digest())
        self.assertTrue(any(name.startswith("L2-") for name in left.files()))
        before = left.files()
        left.drain()
        self.assertEqual(before, left.files())

    def test_lsm_size_bound_and_atomic_rejection(self):
        tree = LSMTree(flush_every=100, memtable_max_bytes=64)
        tree.apply(1, {"a": "x" * 20, "b": "y" * 20})
        self.assertLessEqual(tree.memtable.byte_size, 64)
        self.assertEqual(tree.snapshot(), {"a": "x" * 20, "b": "y" * 20})
        with self.assertRaises(ValueError):
            tree.apply(2, {"bad": "z" * 100})
        self.assertEqual(tree.index, 1)
        with self.assertRaises(ValueError):
            tree.apply(3, {})
        tree.apply(2, {"a": None})
        self.assertEqual(tree.lookup("a"), (2, None))


if __name__ == "__main__":
    unittest.main()
