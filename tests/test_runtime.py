import unittest
from src.common import Xorshift128Plus, canonical_decode, canonical_encode, crc32, murmur3_32, sha256_hex
from src.virtual_os import BLOCK_SIZE, SimEnvironment, VirtualDisk, VirtualNetwork


class CommonTests(unittest.TestCase):
    def test_seeded_prng_reproducible_and_bounded(self):
        first, second = Xorshift128Plus(0), Xorshift128Plus(0)
        values = [first.next_u64() for _ in range(100)]
        self.assertEqual(values, [second.next_u64() for _ in range(100)])
        self.assertEqual(len(set(values)), 100)
        self.assertTrue(all(0 <= value < 1 << 64 for value in values))
        self.assertNotEqual(values[0], Xorshift128Plus(1).next_u64())
        for bound in (1, 3, 1025, 1 << 64, (1 << 80) + 7):
            self.assertTrue(all(0 <= first.randbelow(bound) < bound for _ in range(100)))
        self.assertFalse(first.chance(0))
        self.assertTrue(first.chance(1000))
        with self.assertRaises(ValueError):
            first.randbelow(0)
        with self.assertRaises(ValueError):
            first.choice([])

    def test_canonical_codec_and_known_hash_vectors(self):
        encoded = canonical_encode({"z": 1, "a": [None, "é"]})
        self.assertEqual(encoded, '{"a":[null,"é"],"z":1}'.encode())
        self.assertEqual(canonical_decode(encoded), {"a": [None, "é"], "z": 1})
        for invalid in (b'{"a":1,"a":2}', b'NaN', b'Infinity'):
            with self.assertRaises(ValueError):
                canonical_decode(invalid)
        with self.assertRaises(ValueError):
            canonical_encode(float("nan"))
        self.assertEqual(crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(murmur3_32(b""), 0)
        self.assertEqual(murmur3_32(b"foo"), 0xF6A5C420)
        self.assertEqual(murmur3_32(b"hello"), 0x248BFA47)


class EnvironmentTests(unittest.TestCase):
    def test_stable_order_and_check_at_every_tick(self):
        env = SimEnvironment(7)
        events, checked = [], []
        env.schedule(2, lambda: events.append("first"), "first")

        def second():
            events.append("second")
            env.schedule(0, lambda: events.append("third"), "third")

        env.schedule(2, second, "second")
        env.run(5, lambda: checked.append((env.now, tuple(events))))
        self.assertEqual(events, ["first", "second", "third"])
        self.assertEqual([tick for tick, _ in checked], list(range(6)))
        self.assertEqual(checked[2][1], ("first", "second", "third"))
        env.run(8, lambda: checked.append((env.now, tuple(events))))
        self.assertEqual([tick for tick, _ in checked], list(range(9)))

    def test_zero_delay_loop_bounded_failure_and_trace(self):
        env = SimEnvironment(9, trace_limit=3, max_events_per_tick=5)

        def again():
            env.schedule(0, again, "cycle")

        env.schedule(0, again, "cycle")
        with self.assertRaisesRegex(RuntimeError, "per-tick"):
            env.run(1)
        self.assertEqual(env.counters["executed"], 5)
        self.assertEqual(len(env.trace), 3)


class NetworkTests(unittest.TestCase):
    def test_wire_snapshot_duplicates_partition_at_delivery(self):
        env = SimEnvironment(42)
        net = VirtualNetwork(env)
        net.min_latency = net.max_latency = 2
        received = []
        net.register(1, lambda sender, payload: received.append((sender, payload)))
        payload = {"nested": [1]}
        net.duplicate_rate = 1000
        net.send(0, 1, payload)
        payload["nested"].append(2)
        env.run(2)
        self.assertEqual(received, [(0, {"nested": [1]}), (0, {"nested": [1]})])
        received[0][1]["nested"].append(3)
        self.assertEqual(received[1][1], {"nested": [1]})
        net.send(0, 1, {"next": 2})
        net.blocked.add((0, 1))
        env.run(4)
        self.assertEqual(len(received), 2)
        self.assertEqual(net.stats["partitioned"], 2)

    def test_asymmetric_partitions_checksum_rejection(self):
        env = SimEnvironment(8)
        net = VirtualNetwork(env)
        received = []
        net.register(0, lambda sender, payload: received.append(sender))
        net.register(1, lambda sender, payload: received.append(sender))
        net.blocked.add((0, 1))
        net.send(0, 1, {"v": 1})
        net.send(1, 0, {"v": 1})
        env.run(10)
        self.assertEqual(received, [1])
        net.corrupt_rate = 1000
        net.send(1, 0, {"v": 2})
        env.run(20)
        self.assertEqual(received, [1])
        self.assertEqual(net.stats["rejected"], 1)

    def test_fault_trace_replays_from_seed(self):
        def replay(seed):
            env = SimEnvironment(seed)
            net = VirtualNetwork(env)
            net.drop_rate, net.duplicate_rate, net.corrupt_rate = 150, 200, 100
            received = []
            net.register(1, lambda sender, payload: received.append((env.now, payload["i"])))
            for i in range(100):
                net.send(0, 1, {"i": i})
            env.run(20)
            return received, env.trace, net.stats, env.prng.state

        self.assertEqual(replay(91), replay(91))
        self.assertNotEqual(replay(91)[0], replay(92)[0])


class DiskTests(unittest.TestCase):
    def test_async_durability_crash_cancellation_reboot_read(self):
        env = SimEnvironment(100)
        disk = VirtualDisk(env)
        disk.min_latency = disk.max_latency = 2
        callbacks = []
        wire = bytearray(b"a" * BLOCK_SIZE)
        disk.write(0, wire, callbacks.append)
        wire[0] = 0
        self.assertEqual(disk.blocks, {})
        env.run(2)
        self.assertEqual(callbacks, [True])
        self.assertEqual(disk.blocks[0], b"a" * BLOCK_SIZE)
        self.assertEqual(disk.revision, 1)
        disk.write(1, b"b" * BLOCK_SIZE, callbacks.append)
        disk.read(0, callbacks.append)
        disk.crash()
        env.run(4)
        self.assertEqual(callbacks, [True])
        self.assertNotIn(1, disk.blocks)
        self.assertEqual(disk.stats["cancelled"], 2)
        disk.read(0, callbacks.append)
        env.run(6)
        self.assertEqual(callbacks[-1], b"a" * BLOCK_SIZE)

    def test_torn_write_corruption_transient_failure(self):
        env = SimEnvironment(101)
        disk = VirtualDisk(env)
        disk.blocks[4] = b"a" * BLOCK_SIZE
        disk.torn_write_rate = 1000
        results = []
        disk.write(4, b"b" * BLOCK_SIZE, results.append)
        env.run(10)
        self.assertEqual(results, [False])
        self.assertTrue(disk.blocks[4].startswith(b"b"))
        self.assertTrue(disk.blocks[4].endswith(b"a"))
        self.assertEqual(len(disk.blocks[4]), BLOCK_SIZE)
        original = disk.blocks[4]
        self.assertTrue(disk.corrupt(4, 10, 2))
        self.assertEqual(disk.blocks[4][10], original[10] ^ 4)
        self.assertEqual(disk.revision, 2)
        disk.read_fail_rate = 1000
        disk.read(4, results.append)
        env.run(20)
        self.assertIsNone(results[-1])
        disk.read_fail_rate = disk.torn_write_rate = 0
        disk.stall_rate = 1000
        disk.write(4, b"c" * BLOCK_SIZE, results.append)
        env.run(100)
        self.assertTrue(results[-1])
        self.assertEqual(disk.blocks[4], b"c" * BLOCK_SIZE)
        self.assertEqual(disk.stats["stalls"], 1)

    def test_atomic_erase_respects_crash_generation(self):
        env = SimEnvironment(12)
        disk = VirtualDisk(env)
        disk.min_latency = disk.max_latency = 2
        disk.blocks[0] = b"a" * BLOCK_SIZE
        callbacks = []
        disk.erase(callbacks.append)
        disk.crash()
        env.run(2)
        self.assertEqual(callbacks, [])
        self.assertIn(0, disk.blocks)
        disk.erase(callbacks.append)
        self.assertIn(0, disk.blocks)
        env.run(4)
        self.assertEqual(callbacks, [True])
        self.assertEqual(disk.blocks, {})
        self.assertEqual(disk.revision, 1)

    def test_invalid_write_does_not_schedule_io(self):
        env = SimEnvironment(11)
        disk = VirtualDisk(env)
        with self.assertRaises(ValueError):
            disk.write(0, b"short", lambda success: self.fail("unexpected completion"))
        self.assertEqual(env.pending, 0)


if __name__ == "__main__":
    unittest.main()
