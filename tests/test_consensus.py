import unittest
from src.testing.vopr_harness import Cluster
from src.consensus.types import Entry, ZERO_HASH, validate_request, parse_log


class ConsensusTests(unittest.TestCase):
    def request(self, client, seq, **fields):
        return {"client": client, "seq": seq, **fields}

    def test_transaction_atomicity_and_conditional_abort(self):
        cluster = Cluster(11, faults=False)
        cluster.submit(self.request(0, 1, writes={"a": 5, "b": 8}))
        cluster.run(150)
        cluster.submit(self.request(0, 2, reads=["a", "b"], writes={"a": 99, "b": 0}, expect={"a": 4}))
        cluster.run(300)
        result = cluster.clients[0]["responses"][-1]["result"]
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reads"], {"a": 5, "b": 8})
        cluster.submit(self.request(0, 3, reads=["a", "b"], writes={"a": 6}, expect={"a": 5}))
        cluster.run(450)
        self.assertEqual(cluster.replicas[0].machine.storage.snapshot(), {"a": 6, "b": 8})

    def test_read_query_goes_through_log(self):
        cluster = Cluster(12, faults=False)
        cluster.submit(self.request(0, 1, writes={"a": 5, "b": 7}))
        cluster.run(100)
        cluster.submit(self.request(0, 2, query={"group_by": [],
                      "aggregates": {"s": ["sum", "value"], "n": ["count", None]}}))
        cluster.run(250)
        reply = cluster.clients[0]["responses"][-1]
        self.assertEqual(reply["index"], 2)
        self.assertEqual(reply["result"]["query"], [{"s": 12, "n": 2}])

    def test_minority_cannot_ack_new_read(self):
        cluster = Cluster(13, faults=False)
        cluster.submit(self.request(0, 1, writes={"a": 1}))
        cluster.run(100)
        cluster.network.blocked = {(a, b) for a in range(5) for b in range(5)
                                   if (a < 2) != (b < 2)}
        request = self.request(0, 2, reads=["a"])
        cluster.checker.invoke(request)
        cluster.clients[0]["pending"] = request
        cluster.network.send(1000, 0, {"type": "client", "request": request})
        cluster.run(200)
        self.assertNotIn((0, 2), cluster.checker.completed)
        cluster.network.blocked.clear()
        cluster._send_client(request)
        cluster.run(700)
        if cluster.clients[0]["pending"]:
            cluster._send_client(request)
            cluster.run(1200)
        self.assertIn((0, 2), cluster.checker.completed)

    def test_primary_crash_view_change_and_durable_reboot(self):
        cluster = Cluster(14, faults=False)
        cluster.start_clients(2)
        cluster.run(200)
        before = len(cluster.checker.completed)
        cluster.replicas[0].crash()
        cluster.run(1000)
        self.assertGreater(len(cluster.checker.completed), before)
        self.assertTrue(any(r.view > 0 for r in cluster.replicas[1:]))
        cluster.replicas[0].reboot()
        cluster.run(1600)
        self.assertEqual(cluster.replicas[0].status, "normal")
        cluster.client_enabled = False
        cluster.run(2200)
        self.assertEqual(len({r.commit for r in cluster.replicas}), 1)

    def test_total_cluster_power_loss_with_intact_disks(self):
        cluster = Cluster(15, faults=False)
        cluster.submit(self.request(0, 1, writes={"survives": 42}))
        cluster.run(150)
        for replica in cluster.replicas:
            replica.crash()
        for replica in cluster.replicas:
            replica.reboot()
        cluster.run(400)
        cluster.submit(self.request(0, 2, reads=["survives"]))
        cluster.run(700)
        self.assertEqual(cluster.clients[0]["responses"][-1]["result"]["reads"], {"survives": 42})

    def test_corrupt_wal_recovery_and_chunk_state_transfer(self):
        cluster = Cluster(16, faults=False)
        cluster.start_clients(2)
        cluster.run(700)
        replica = cluster.replicas[3]
        replica.crash()
        replica.disk.corrupt(0, 12, 1)
        replica.reboot()
        cluster.run(1500)
        self.assertGreater(replica.stats["recoveries"], 0)
        cluster.client_enabled = False
        cluster.run(2200)
        self.assertEqual(len({r.machine.storage.digest() for r in cluster.replicas}), 1)

    def test_replay_same_seed_same_report(self):
        first = Cluster(17, faults=True).verify(1000, 500)
        second = Cluster(17, faults=True).verify(1000, 500)
        self.assertEqual(first, second)

    def test_checker_rejects_forged_reply(self):
        cluster = Cluster(18, faults=False)
        cluster.submit(self.request(0, 1, writes={"a": 1}, reads=["a"]))
        cluster.run(120)
        response = dict(cluster.clients[0]["responses"][-1])
        response["result"] = {"accepted": True, "reads": {"a": 999}}
        with self.assertRaisesRegex(AssertionError, "incorrect transaction result"):
            cluster.checker.reply(response)

    def test_stale_view_messages_and_duplicate_client(self):
        cluster = Cluster(19, faults=False)
        request = self.request(0, 1, writes={"x": 7})
        cluster.submit(request)
        cluster.run(150)
        for _ in range(10):
            cluster._send_client(request)
        cluster.run(250)
        self.assertEqual(cluster.replicas[0].commit, 1)
        follower = cluster.replicas[2]
        follower._begin_view(3)
        cluster.run(265)
        old = cluster.replicas[0].log[0]
        follower.receive(0, {"type": "prepare", "view": 0, "entry": old.wire(), "commit": 1})
        self.assertGreaterEqual(follower.view, 3)

    def test_hash_chain_and_input_bounds(self):
        entry = Entry.create(1, 0, self.request(0, 1, writes={"a": 1}), ZERO_HASH)
        self.assertEqual(parse_log([entry.wire()]), [entry])
        raw = entry.wire()
        raw["hash"] = ZERO_HASH
        with self.assertRaises(ValueError):
            Entry.parse(raw)
        with self.assertRaises(ValueError):
            validate_request(self.request(0, 1, writes={"a": float("nan")}))
        with self.assertRaises(ValueError):
            validate_request(self.request(0, 1, writes={"a": 1 << 70}))

    def test_recovery_propagates_durable_view_floor(self):
        cluster = Cluster(20, faults=False)
        cluster.run(20)
        replica = cluster.replicas[4]
        cluster.network.blocked = {(a, b) for a in range(5) for b in range(5) if (a == 4) != (b == 4)}
        replica._begin_view(1)
        cluster.run(30)
        replica.disk.torn_write_rate = 1000
        replica._begin_view(2)
        cluster.run(40)
        # A tear can leave all meaningful bytes intact if it hits padding.
        # Damage the final record header to guarantee quarantine with view 1.
        replica.disk.corrupt(max(replica.disk.blocks), 0, 0)
        replica.disk.torn_write_rate = 0
        cluster.network.blocked.clear()
        cluster.run(1000)
        self.assertEqual(replica.status, "normal")
        self.assertGreater(replica.stats["recoveries"], 0)
        self.assertTrue(all(r.view >= 1 for r in cluster.replicas))


if __name__ == "__main__":
    unittest.main()
