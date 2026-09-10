"""VSR state machine shared by deterministic and native transport/storage adapters."""
from collections import deque
from .durability import Durability
from .recovery import Recovery
from .view_change import ViewChange
from .state_machine import StateMachine
from .types import Entry, ZERO_HASH, validate_request, digest

CLIENT_BASE = 1000


class Replica(Durability, Recovery, ViewChange):
    def __init__(self, node_id, size, env, network, disk):
        if size < 3 or size % 2 == 0 or not 0 <= node_id < size:
            raise ValueError("membership must be an odd cluster of at least three")
        self.id, self.n = node_id, size
        self.quorum = size // 2 + 1
        self.env, self.network, self.disk = env, network, disk
        self.generation = 0
        self.alive = False
        self.stats = {"crashes": 0, "recoveries": 0, "view_changes": 0,
                      "commits": 0, "invalid_messages": 0, "queue_drops": 0}
        self._reset()
        self.alive = True
        self.network.register(self.id, self.receive)
        self._install({"view": 0, "commit": 0, "log": []}, lambda: None)
        self._arm_timer()

    @property
    def primary(self):
        return self.id == self.view % self.n

    def _reset(self):
        self.view = 0
        self.last_normal = 0
        self.status = "normal"
        self.log = []
        self.commit = 0
        self.machine = StateMachine()
        self.next_block = 0
        self.wal_seq = 0
        self.busy = False
        self.inbox = deque()
        self.acks = {}
        self.svc = set()
        self.dvc = {}
        self.transfers = {}
        self.recovery_responses = {}
        self.recovery_primary = {}
        self.recovery_nonce = f"{self.id}:{self.generation}:{self.env.prng.next_u64()}"
        self.last_contact = self.env.now
        self.last_progress = self.env.now
        self.last_heartbeat = self.env.now
        self.last_retry = self.env.now
        self.timeout = 160 + self.id * 9 + self.env.prng.randbelow(60)

    def _trace(self, kind, **fields):
        self.env.trace.append({"tick": self.env.now, "kind": kind, "node": self.id,
                               "view": self.view, **fields})

    def crash(self):
        if not self.alive:
            return
        self._trace("crash", commit=self.commit)
        self.alive = False
        self.generation += 1
        self.disk.crash()
        self.inbox.clear()
        self.stats["crashes"] += 1

    def reboot(self):
        if self.alive:
            return
        self._reset()
        self.alive = True
        self.status = "booting"
        self._boot_read()
        self._arm_timer()

    def _arm_timer(self):
        generation = self.generation

        def timer():
            if self.alive and self.generation == generation:
                self.receive(self.id, {"type": "tick"})
                self.env.schedule(10, timer, f"timer-{self.id}")

        self.env.schedule(10, timer, f"timer-{self.id}")

    def receive(self, sender, message):
        if not self.alive:
            return
        if self.busy:
            if message.get("type") == "tick" and any(m.get("type") == "tick" for _, m in self.inbox):
                return
            if len(self.inbox) >= 4096:
                self.stats["queue_drops"] += 1
                return
            self.inbox.append((sender, message))
            return
        self._dispatch(sender, message)
        self._drain()

    def _drain(self):
        # The bounded inbox prevents an unbounded backlog while disks stall.
        while self.alive and not self.busy and self.inbox:
            sender, message = self.inbox.popleft()
            self._dispatch(sender, message)

    def _dispatch(self, sender, message):
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            self.stats["invalid_messages"] += 1
            return
        kind = message["type"]
        if self.status == "booting":
            return
        if kind != "client" and not 0 <= sender < self.n:
            return
        if self.status == "recovering" and kind not in {"tick", "chunk", "recovery_response"}:
            return
        handler = {"tick": self._on_tick, "client": self._on_client,
                   "prepare": self._on_prepare, "prepare_ok": self._on_prepare_ok,
                   "commit": self._on_commit, "start_view_change": self._on_start_view_change,
                   "do_view_change": self._on_do_view_change, "get_state": self._on_get_state,
                   "chunk": self._on_chunk, "recovery": self._on_recovery,
                   "recovery_response": self._on_recovery_response}.get(kind)
        if handler is None:
            self.stats["invalid_messages"] += 1
            return
        try:
            if "view" in message and (type(message["view"]) is not int or message["view"] < 0):
                raise ValueError("invalid view")
            handler(sender, message)
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            self.stats["invalid_messages"] += 1
            self._trace("invalid_message", type=kind, error=str(error))

    def _send(self, target, message):
        self.network.send(self.id, target, message)

    def _broadcast(self, message):
        for target in range(self.n):
            if target != self.id:
                self._send(target, message)

    def _on_tick(self, sender, message):
        now = self.env.now
        if self.status == "booting":
            return
        self.machine.storage.advance(8)
        for key in list(self.transfers):
            if now - self.transfers[key]["created"] > 500:
                del self.transfers[key]
        if self.status == "recovering":
            if now - self.last_retry >= 40:
                self.last_retry = now
                self._request_recovery()
            return
        timeout = self.timeout + min(self.view, 8) * 20
        if self.status == "view-change":
            if now - self.last_contact >= timeout:
                self._begin_view(self.view + 1)
            elif now - self.last_retry >= 30:
                self.last_retry = now
                self._broadcast({"type": "start_view_change", "view": self.view})
                if len(self.svc) >= self.quorum:
                    self._send_do_view_change()
            return
        if self.primary:
            if len(self.log) > self.commit and now - self.last_progress > timeout:
                self._begin_view(self.view + 1)
                return
            if now - self.last_heartbeat >= 20:
                self.last_heartbeat = now
                self._broadcast({"type": "commit", "view": self.view, "commit": self.commit})
                if self.log:
                    self._broadcast_prepare(self.log[-1])
                self._reply_committed()
        elif now - self.last_contact >= timeout:
            self._begin_view(self.view + 1)

    def _on_client(self, sender, message):
        if self.status != "normal" or not self.primary:
            return
        request = validate_request(message["request"])
        if sender != CLIENT_BASE + request["client"]:
            raise ValueError("client sender mismatch")
        identity = (request["client"], request["seq"])
        for entry in self.log:
            if (entry.request["client"], entry.request["seq"]) == identity:
                if digest(entry.request) != digest(request):
                    self._send(sender, {"type": "error", "seq": request["seq"],
                                        "error": "request identity reused"})
                elif entry.index <= self.commit:
                    self._reply(entry)
                else:
                    self._broadcast_prepare(entry)
                return
        if len(self.log) - self.commit >= 32:
            return
        entry = Entry.create(len(self.log) + 1, self.view, request,
                             self.log[-1].hash if self.log else ZERO_HASH)

        def appended():
            self.acks[self.id] = len(self.log)
            self._broadcast_prepare(entry)

        self._persist({"kind": "append", "entries": [entry.wire()]}, appended)

    def _broadcast_prepare(self, entry):
        self._broadcast({"type": "prepare", "view": self.view, "entry": entry.wire(),
                         "commit": self.commit})

    def _observe_primary(self, sender, message):
        view = message["view"]
        if sender != view % self.n or view < self.view:
            return False
        if view > self.view:
            self._begin_view(view)
            # Requeue the request until the view promise is durable.
            self.env.schedule(1, lambda: self.receive(sender, message), "new-view-observation")
            return False
        if self.status == "view-change":
            self._send(sender, {"type": "get_state", "view": view, "start": True})
            return False
        if self.status != "normal":
            return False
        self.last_contact = self.env.now
        return True

    def _on_prepare(self, sender, message):
        if not self._observe_primary(sender, message) or self.primary:
            return
        entry = Entry.parse(message["entry"])
        if entry.view > self.view:
            raise ValueError("entry from future view")
        if entry.index > len(self.log) + 1:
            self._send(sender, {"type": "get_state", "view": self.view})
            return
        if entry.index <= len(self.log):
            if self.log[entry.index - 1].hash != entry.hash:
                self._send(sender, {"type": "get_state", "view": self.view})
                return
            self._ack()
            self._follow_commit(message["commit"])
            return
        previous = self.log[-1].hash if self.log else ZERO_HASH
        if entry.previous != previous:
            self._send(sender, {"type": "get_state", "view": self.view})
            return

        def appended():
            self._ack()
            self._follow_commit(message["commit"])

        self._persist({"kind": "append", "entries": [entry.wire()]}, appended)

    def _ack(self):
        self._send(self.view % self.n, {"type": "prepare_ok", "view": self.view,
                                      "index": len(self.log),
                                      "hash": self.log[-1].hash if self.log else ZERO_HASH})

    def _on_prepare_ok(self, sender, message):
        if not self.primary or self.status != "normal" or message["view"] != self.view:
            return
        index = message["index"]
        if type(index) is not int or not 0 <= index <= len(self.log):
            raise ValueError("invalid acknowledgment index")
        if message["hash"] != (self.log[index - 1].hash if index else ZERO_HASH):
            return
        self.acks[self.id] = len(self.log)
        self.acks[sender] = max(index, self.acks.get(sender, 0))
        lengths = sorted(self.acks.values(), reverse=True)
        if len(lengths) < self.quorum or lengths[self.quorum - 1] <= self.commit:
            return
        commit = lengths[self.quorum - 1]

        def committed():
            self.stats["commits"] += 1
            self.last_progress = self.env.now
            self._broadcast({"type": "commit", "view": self.view, "commit": self.commit})
            self._reply_committed()

        self._persist({"kind": "commit", "commit": commit}, committed)

    def _on_commit(self, sender, message):
        if self._observe_primary(sender, message) and not self.primary:
            self._follow_commit(message["commit"])

    def _follow_commit(self, commit):
        if type(commit) is not int or commit < 0:
            raise ValueError("invalid commit")
        if commit > len(self.log):
            self._send(self.view % self.n, {"type": "get_state", "view": self.view})
        elif commit > self.commit:
            self._persist({"kind": "commit", "commit": commit}, lambda: None)

    def _reply(self, entry):
        identity = (entry.request["client"], entry.request["seq"])
        self._send(CLIENT_BASE + identity[0],
                   {"type": "reply", "client": identity[0], "seq": identity[1],
                    "index": entry.index, "view": self.view, "hash": entry.hash,
                    "request_hash": digest(entry.request), "result": self.machine.results[identity]})

    def _reply_committed(self):
        # One outstanding request per client; only latest reply needs retransmission.
        latest = {}
        for entry in self.log[:self.commit]:
            latest[entry.request["client"]] = entry
        for entry in latest.values():
            self._reply(entry)
