"""Bounded fault episodes with explicit quiescent intervals for liveness."""


class ChaosGenerator:
    def __init__(self, cluster):
        self.cluster = cluster
        self.env = cluster.env
        self.network = cluster.network
        self.replicas = cluster.replicas
        self.counts = {"partitions": 0, "asymmetric_partitions": 0,
                       "crashes": 0, "corruptions": 0, "disk_fault_windows": 0}

    def schedule(self, ticks):
        for base in range(0, ticks, 1000):
            for offset, callback, name in (
                    (180, self.partition, "chaos-partition"),
                    (360, self.heal_network, "chaos-heal"),
                    (440, self.crash_one, "chaos-crash"),
                    (560, self.corrupt_one, "chaos-corrupt"),
                    (680, self.disk_window, "chaos-disk-faults"),
                    (770, self.heal_disks, "chaos-disks-heal")):
                if base + offset < ticks:
                    self.env.schedule(base + offset, callback, name)

    def _healthy(self):
        return [r for r in self.replicas if r.alive and r.status == "normal"]

    def partition(self):
        ids = list(range(len(self.replicas)))
        first = ids.pop(self.env.prng.randbelow(len(ids)))
        second = ids.pop(self.env.prng.randbelow(len(ids)))
        minority = {first, second}
        asymmetric = self.env.prng.randbelow(2) == 0
        self.network.blocked = {(a, b) for a in range(5) for b in range(5)
                                if (a in minority) != (b in minority)
                                and (not asymmetric or a in minority)}
        self.counts["partitions"] += 1
        self.counts["asymmetric_partitions"] += int(asymmetric)
        self.env.trace.append({"tick": self.env.now, "kind": "partition",
                               "minority": sorted(minority), "asymmetric": asymmetric})

    def heal_network(self):
        self.network.blocked.clear()

    def crash_one(self):
        healthy = self._healthy()
        if len(healthy) < 4:
            return
        replica = self.env.prng.choice(healthy)
        replica.crash()
        self.counts["crashes"] += 1
        self.env.schedule(35, replica.reboot, "chaos-reboot")

    def corrupt_one(self):
        healthy = [r for r in self._healthy() if r.disk.blocks]
        if len(healthy) < 4:
            return
        replica = self.env.prng.choice(healthy)
        replica.crash()
        address = self.env.prng.choice(sorted(replica.disk.blocks))
        # Target checked header bytes rather than harmless padding.
        replica.disk.corrupt(address, self.env.prng.randbelow(24), self.env.prng.randbelow(8))
        self.counts["corruptions"] += 1
        self.env.trace.append({"tick": self.env.now, "kind": "disk_corruption",
                               "node": replica.id, "block": address})
        self.env.schedule(10, replica.reboot, "corruption-reboot")

    def disk_window(self):
        healthy = self._healthy()
        if len(healthy) < 4:
            return
        replica = self.env.prng.choice(healthy)
        replica.disk.torn_write_rate = 120
        replica.disk.stall_rate = 150
        replica.disk.read_fail_rate = 80
        self.counts["disk_fault_windows"] += 1

    def heal_disks(self):
        for replica in self.replicas:
            replica.disk.torn_write_rate = 0
            replica.disk.stall_rate = 0
            replica.disk.read_fail_rate = 0

    def heal_all(self):
        self.heal_network()
        self.heal_disks()
        self.network.drop_rate = 0
        self.network.corrupt_rate = 0
        self.network.duplicate_rate = 0
        for replica in self.replicas:
            replica.reboot()
