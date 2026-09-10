"""Immutable-wire asynchronous network with independently faulty directions."""
from src.common import canonical_decode, canonical_encode, sha256_digest


class VirtualNetwork:
    def __init__(self, env):
        self.env = env
        self.blocked = set()
        self.drop_rate = 0
        self.duplicate_rate = 0
        self.corrupt_rate = 0
        self.min_latency = 1
        self.max_latency = 5
        self.stats = {"sent": 0, "delivered": 0, "dropped": 0, "partitioned": 0,
                      "duplicated": 0, "corrupted": 0, "rejected": 0, "unavailable": 0}
        self._handlers = {}

    def register(self, node_id, handler):
        if not callable(handler):
            raise TypeError("network handler must be callable")
        self._handlers[node_id] = handler

    def unregister(self, node_id):
        self._handlers.pop(node_id, None)

    def send(self, sender, recipient, payload):
        if not isinstance(payload, dict):
            raise TypeError("network payload must be a dictionary")
        if not 0 <= self.min_latency <= self.max_latency:
            raise ValueError("invalid network latency range")
        for rate in (self.drop_rate, self.duplicate_rate, self.corrupt_rate):
            if not isinstance(rate, int) or not 0 <= rate <= 1000:
                raise ValueError("network fault rates must be integers in [0, 1000]")
        self.stats["sent"] += 1
        if (sender, recipient) in self.blocked:
            self.stats["partitioned"] += 1
            return
        if self.env.prng.chance(self.drop_rate):
            self.stats["dropped"] += 1
            return
        body = canonical_encode({"sender": sender, "recipient": recipient, "payload": payload})
        wire = sha256_digest(body) + body
        copies = 2 if self.env.prng.chance(self.duplicate_rate) else 1
        self.stats["duplicated"] += copies - 1
        for _ in range(copies):
            packet = wire
            if self.env.prng.chance(self.corrupt_rate):
                altered = bytearray(packet)
                byte_index = self.env.prng.randbelow(len(altered))
                altered[byte_index] ^= 1 << self.env.prng.randbelow(8)
                packet = bytes(altered)
                self.stats["corrupted"] += 1
            delay = self.min_latency + self.env.prng.randbelow(self.max_latency - self.min_latency + 1)
            self.env.schedule(delay, lambda packet=packet: self._deliver(sender, recipient, packet),
                              label=f"network:{sender}->{recipient}")

    def _deliver(self, sender, recipient, wire):
        if (sender, recipient) in self.blocked:
            self.stats["partitioned"] += 1
            return
        handler = self._handlers.get(recipient)
        if handler is None:
            self.stats["unavailable"] += 1
            return
        if len(wire) < 32 or sha256_digest(wire[32:]) != wire[:32]:
            self.stats["rejected"] += 1
            self.env.record("network_reject", sender=sender, recipient=recipient)
            return
        try:
            envelope = canonical_decode(wire[32:])
            if (not isinstance(envelope, dict) or envelope.get("sender") != sender
                    or envelope.get("recipient") != recipient or not isinstance(envelope.get("payload"), dict)):
                raise ValueError("invalid network envelope")
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
            self.stats["rejected"] += 1
            return
        self.stats["delivered"] += 1
        handler(sender, envelope["payload"])
