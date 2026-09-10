"""Five-node deterministic workload with independent per-tick invariants."""
from src.virtual_os import SimEnvironment, VirtualNetwork, VirtualDisk
from src.consensus.replica import Replica, CLIENT_BASE
from .linearizability_checker import InvariantChecker
from .chaos_generator import ChaosGenerator


class Cluster:
    def __init__(self, seed=1, size=5, faults=True):
        self.seed = seed
        self.env = SimEnvironment(seed)
        self.network = VirtualNetwork(self.env)
        self.network.drop_rate = 25 if faults else 0
        self.network.duplicate_rate = 50 if faults else 0
        self.network.corrupt_rate = 4 if faults else 0
        self.replicas = [Replica(i, size, self.env, self.network, VirtualDisk(self.env)) for i in range(size)]
        self.checker = InvariantChecker(self.env, self.replicas)
        self.clients = {}
        self.client_enabled = True
        self.chaos = ChaosGenerator(self)
        self.faults = faults
        self.liveness_windows = 0

    def submit(self, request):
        client = request["client"]
        if client in self.clients and self.clients[client]["pending"] is not None:
            raise ValueError("one outstanding transaction per client")
        if client not in self.clients:
            self.clients[client] = {"pending": None, "sequence": 0, "responses": [], "automatic": False}
            self.network.register(CLIENT_BASE + client, lambda sender, message: self._reply(client, sender, message))
        self.clients[client]["pending"] = request
        self.clients[client]["sequence"] = request["seq"]
        self.checker.invoke(request)
        self._send_client(request)

    def _send_client(self, request):
        for target in range(len(self.replicas)):
            self.network.send(CLIENT_BASE + request["client"], target,
                              {"type": "client", "request": request})

    def _reply(self, client, sender, message):
        if message.get("type") != "reply":
            return
        if not 0 <= sender < len(self.replicas) or sender != message["view"] % len(self.replicas):
            raise AssertionError("reply from non-primary")
        self.checker.reply(message)
        state = self.clients[client]
        request = state["pending"]
        if request is not None and request["seq"] == message["seq"]:
            state["responses"].append(message)
            state["pending"] = None
            if state["automatic"] and self.client_enabled:
                self.env.schedule(10 + self.env.prng.randbelow(16),
                                  lambda: self._generate(client), "client-next")

    def _generate(self, client):
        if not self.client_enabled or self.clients[client]["pending"] is not None:
            return
        seq = self.clients[client]["sequence"] + 1
        key = f"k{self.env.prng.randbelow(24):02d}"
        other = f"k{self.env.prng.randbelow(24):02d}"
        request = {"client": client, "seq": seq, "reads": [key, other]}
        mode = self.env.prng.randbelow(10)
        if mode < 7:
            request["writes"] = {key: self.env.prng.randbelow(2001) - 1000,
                                 other: self.env.prng.randbelow(2001) - 1000}
            if mode == 0:
                request["expect"] = {key: self.env.prng.randbelow(5)}
        elif mode >= 8:
            request["query"] = {"group_by": [],
                                "aggregates": {"total": ["sum", "value"],
                                               "rows": ["count", None],
                                               "mean": ["avg", "value"],
                                               "lo": ["min", "value"],
                                               "hi": ["max", "value"]},
                                "filters": [["value", ">=", -500]]}
        self.submit(request)

    def start_clients(self, count=4):
        for client in range(count):
            self.clients[client] = {"pending": None, "sequence": 0,
                                    "responses": [], "automatic": True}
            self.network.register(CLIENT_BASE + client,
                                  lambda sender, message, client=client: self._reply(client, sender, message))
            self.env.schedule(20 + client * 3, lambda client=client: self._generate(client), "client-start")

        def retry():
            for state in self.clients.values():
                if state["pending"] is not None:
                    self._send_client(state["pending"])
            if self.client_enabled or any(c["pending"] is not None for c in self.clients.values()):
                self.env.schedule(55, retry, "client-retry")

        self.env.schedule(55, retry, "client-retry")

    def run(self, until):
        self.env.run(until, check=self.checker.check)

    def verify(self, ticks=10000, recovery_ticks=2500):
        if ticks < 1 or recovery_ticks < 1:
            raise ValueError("tick budgets must be positive")
        self.start_clients()
        if self.faults:
            self.chaos.schedule(ticks)
        # Fixed stable windows provide a non-vacuous, bounded liveness test.
        checkpoints = list(range(1000, ticks + 1, 1000))
        if not checkpoints or checkpoints[-1] != ticks:
            checkpoints.append(ticks)
        before = 0
        for checkpoint in checkpoints:
            self.run(checkpoint)
            completed = len(self.checker.completed)
            if checkpoint >= 1000 and completed <= before:
                raise AssertionError(f"no client progress in window ending at {checkpoint}")
            self.liveness_windows += 1
            before = completed
        self.chaos.heal_all()
        before = len(self.checker.completed)
        self.run(ticks + recovery_ticks)
        if len(self.checker.completed) <= before:
            raise AssertionError("no progress after faults healed")
        self.client_enabled = False
        self.run(ticks + recovery_ticks + 1000)
        if any(client["pending"] is not None for client in self.clients.values()):
            raise AssertionError("clients failed to complete in final stable window")
        commits = {replica.commit for replica in self.replicas}
        if len(commits) != 1 or any(not r.alive or r.status != "normal" for r in self.replicas):
            raise AssertionError("replicas failed to converge after fault recovery")
        digests = {r.machine.storage.digest() for r in self.replicas}
        if len(digests) != 1:
            raise AssertionError("final SSTable digests differ")
        return {"seed": self.seed, "chaos_ticks": ticks if self.faults else 0,
                "total_ticks": self.env.now, "transactions": len(self.checker.completed),
                "commit_index": self.replicas[0].commit, "invariant_checks": self.checker.checks,
                "storage_comparisons": self.checker.storage_comparisons,
                "durability_checks": self.checker.durability_checks,
                "liveness_windows": self.liveness_windows + 1,
                "sstable_digest": next(iter(digests)),
                "views": [r.view for r in self.replicas],
                "replicas": [r.stats for r in self.replicas],
                "chaos": self.chaos.counts,
                "network": dict(self.network.stats), "disks": [dict(r.disk.stats) for r in self.replicas]}
