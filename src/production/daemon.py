"""One durable VSR replica per native daemon process."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import time
import traceback
from src.consensus.replica import Replica, CLIENT_BASE
from src.consensus.state_machine import StateMachine
from src.consensus.types import parse_log, digest, encode, Entry, validate_request, ZERO_HASH
from .network import TCPNetwork
from .runtime import RealEnvironment, DiskWorker
from .persistence import DurableStore, CorruptStore, freeze_storage
from .framing import FrameError


def emit(event, **fields):
    print(json.dumps({"event": event, "pid": os.getpid(), "time_ns": time.time_ns(), **fields},
                     sort_keys=True), flush=True)


class ProductionReplica(Replica):
    def __init__(self, node_id, size, env, network, store, io, bootstrap=False, checkpoint_every=128):
        if size < 3 or size % 2 == 0 or not 0 <= node_id < size:
            raise ValueError("membership must be odd and contain the node")
        self.id, self.n, self.quorum = node_id, size, size // 2 + 1
        self.env, self.network, self.store, self.io = env, network, store, io
        self.generation, self.alive = 0, False
        self.checkpoint_every = checkpoint_every
        self.last_checkpoint = 0
        self.checkpoints = 0
        self.stats = {"crashes": 0, "recoveries": 0, "view_changes": 0,
                      "commits": 0, "invalid_messages": 0, "queue_drops": 0}
        self._reset()
        self.snapshot_sent = {}
        self.log_bytes = 2
        self.recovery_nonce = secrets.token_hex(24)
        self.alive = True
        self.network.register(self.id, self.receive)
        try:
            records = self.store.recover()
        except CorruptStore as error:
            records = []
            bootstrap = False
            emit("journal_quarantined", node=self.id, error=str(error))
        if records:
            for index, record in enumerate(records):
                if index == 0 and self.store.recovered_storage is not None:
                    self._restore_checkpoint(record)
                else:
                    self._apply_record(record)
            self.wal_seq = self.store.last_sequence
            if self.primary and self.status == "normal":
                self.acks = {self.id: len(self.log)}
            emit("disk_recovered", node=self.id, commit=self.commit, view=self.view,
                 stats=dict(self.store.stats), sstables=len(self.machine.storage.files()))
        elif bootstrap:
            self._persist({"kind": "install", "view": 0, "last_normal": 0,
                           "commit": 0, "log": []}, lambda: None)
        else:
            self.status = "recovering"
            self._request_recovery()
        self.last_contact = self.last_progress = self.env.now
        self._arm_timer()

    def _restore_checkpoint(self, record):
        self.log = parse_log(record["log"])
        self.log_bytes = len(encode(record["log"]))
        self.view, self.last_normal = record["view"], record["last_normal"]
        self.commit = record["commit"]
        if not 0 <= self.commit <= len(self.log):
            raise CorruptStore("checkpoint commit outside log")
        self.status = record.get("status", "normal")
        self.machine = StateMachine()
        self.machine.storage = self.store.recovered_storage
        if self.machine.storage.index != self.commit:
            raise CorruptStore("manifest and checkpoint commit differ")
        self.machine.applied = self.commit
        for client, seq, result, fingerprint in record["client_results"]:
            identity = (client, seq)
            self.machine.results[identity] = result
            self.machine.requests[identity] = fingerprint
        expected = {(entry.request["client"], entry.request["seq"]): digest(entry.request)
                    for entry in self.log[:self.commit]}
        if self.machine.requests != expected:
            raise CorruptStore("checkpoint deduplication metadata does not match log")
        self.last_checkpoint = self.commit

    def _apply_record(self, record):
        super()._apply_record(record)
        if record["kind"] == "install":
            self.log_bytes = len(encode(record["log"]))
        elif record["kind"] == "append":
            self.log_bytes += sum(len(encode(raw)) + 1 for raw in record["entries"])

    def _send_snapshot(self, target, kind, nonce=None):
        identity = (target, kind, nonce, self.view)
        now = self.env.now
        if now - self.snapshot_sent.get(identity, -1000) < 30:
            return
        self.snapshot_sent = {key: tick for key, tick in self.snapshot_sent.items() if now - tick < 1000}
        self.snapshot_sent[identity] = now
        payload = self._snapshot()
        payload.update(kind=kind, nonce=nonce)
        self._send(target, {"type": "snapshot", "view": self.view, "payload": payload})

    def _dispatch(self, sender, message):
        if isinstance(message, dict) and message.get("type") == "snapshot":
            if not 0 <= sender < self.n:
                self.stats["invalid_messages"] += 1
                return
            try:
                self._accept_snapshot(sender, message["payload"], message["view"])
            except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
                self.stats["invalid_messages"] += 1
                self._trace("invalid_snapshot", error=str(error))
            return
        super()._dispatch(sender, message)

    def _checkpoint_record(self):
        return {"kind": "install", "view": self.view, "last_normal": self.last_normal,
                "commit": self.commit, "status": self.status,
                "log": [entry.wire() for entry in self.log],
                "client_results": [[client, seq, result, self.machine.requests[(client, seq)]]
                                   for (client, seq), result in sorted(self.machine.results.items())]}

    def _persist(self, record, after):
        if self.busy:
            raise AssertionError("overlapping durable protocol transitions")
        self.busy = True
        immutable = json.loads(encode(record))

        def durable(sequence, error):
            if error is not None:
                raise RuntimeError(f"durable append failed: {error}") from error
            self.wal_seq = sequence
            self._apply_record(immutable)
            if self.status == "normal" and self.commit - self.last_checkpoint >= self.checkpoint_every:
                self._checkpoint(after)
            else:
                self.busy = False
                after()
                self._drain()

        self.io.submit(lambda: self.store.append(immutable), durable)

    def _checkpoint(self, after):
        snapshot = json.loads(encode(self._checkpoint_record()))
        frozen = freeze_storage(self.machine.storage)
        commit = self.commit

        def saved(value, error):
            if error is not None:
                raise RuntimeError(f"checkpoint failed: {error}") from error
            self.last_checkpoint = commit
            self.checkpoints += 1
            self.busy = False
            emit("checkpoint", node=self.id, commit=commit, files=len(frozen.files),
                 stats=dict(self.store.stats))
            after()
            self._drain()

        sequence = self.wal_seq
        self.io.submit(lambda: self.store.save_checkpoint(snapshot, frozen, expected_sequence=sequence), saved)

    def request_checkpoint(self):
        if self.busy or self.status != "normal":
            return False
        self.busy = True
        self._checkpoint(lambda: None)
        return True

    def _repair(self, payload):
        # Persist a complete replacement generation before becoming eligible to vote.
        self.busy = True
        log = parse_log(payload["log"])
        commit = max(self.commit, payload["commit"])
        if len(log) < commit or any(a.hash != b.hash for a, b in zip(self.log[:self.commit], log[:self.commit])):
            raise AssertionError("recovery would discard committed history")
        machine = StateMachine()
        for entry in log[:commit]:
            machine.apply(entry)
        record = {"kind": "install", "view": payload["view"], "last_normal": payload["view"],
                  "commit": commit, "status": "normal", "log": payload["log"],
                  "client_results": [[client, seq, result, machine.requests[(client, seq)]]
                                     for (client, seq), result in sorted(machine.results.items())]}
        frozen = freeze_storage(machine.storage)

        def repaired(value, error):
            if error is not None:
                raise RuntimeError(f"recovery generation failed: {error}") from error
            self.machine, self.log = machine, log
            self.log_bytes = len(encode(payload["log"]))
            self.commit, self.view, self.last_normal = commit, payload["view"], payload["view"]
            self.status, self.busy = "normal", False
            self.wal_seq = value
            self.last_checkpoint = commit
            self.checkpoints += 1
            emit("state_transfer_recovered", node=self.id, commit=commit, view=self.view)
            self._recovered()
            self._drain()

        self.io.submit(lambda: self.store.replace_from_quorum(record, frozen), repaired)

    def crash(self):
        raise RuntimeError("production replica encountered a fatal storage failure")

    def _reply(self, entry):
        try:
            super()._reply(entry)
        except FrameError as error:
            request = entry.request
            self._send(CLIENT_BASE + request["client"],
                       {"type": "error", "client": request["client"], "seq": request["seq"],
                        "index": entry.index, "view": self.view, "committed": True, "error": str(error)})

    def _on_client(self, sender, message):
        if self.status != "normal" or not self.primary:
            self._send(sender, {"type": "redirect", "primary": self.view % self.n,
                                "view": self.view, "ready": self.status == "normal"})
            return
        request = validate_request(message["request"])
        identity = (request["client"], request["seq"])
        if not any((entry.request["client"], entry.request["seq"]) == identity for entry in self.log):
            candidate = Entry.create(len(self.log) + 1, self.view, request,
                                     self.log[-1].hash if self.log else ZERO_HASH)
            if self.log_bytes + len(encode(candidate.wire())) + 1 > 48 * 1024 * 1024:
                self._send(sender, {"type": "error", "client": identity[0], "seq": identity[1],
                                    "committed": False, "error": "retained consensus log capacity reached"})
                return
            if self.commit == len(self.log):
                self.last_progress = self.env.now
        super()._on_client(sender, message)

    def control(self, payload):
        result = {"type": "status", "node": self.id, "pid": os.getpid(), "view": self.view,
                  "status": self.status, "primary": self.view % self.n, "commit": self.commit,
                  "op": len(self.log), "busy": self.busy, "checkpoints": self.checkpoints,
                  "retained_log_bytes": self.log_bytes,
                  "last_checkpoint": self.last_checkpoint, "storage": dict(self.store.stats),
                  "sstables": len(self.machine.storage.files()), "digest": self.machine.storage.digest(),
                  "protocol": dict(self.stats), "network": self.network.stats()}
        if payload["type"] == "checkpoint":
            result["checkpoint_requested"] = self.request_checkpoint()
        elif payload["type"] == "audit":
            result["log"] = [entry.wire() for entry in self.log[:self.commit]]
            result["state"] = self.machine.storage.snapshot()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="AegisDB durable TCP replica")
    parser.add_argument("--node", type=int, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peers", type=Path, required=True, help="JSON map of node IDs to [host,port]")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=128)
    parser.add_argument("--bootstrap", action="store_true", help="initialize a previously empty replica")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 32 or args.checkpoint_every < 1:
        parser.error("invalid worker or checkpoint configuration")
    peers = {int(key): tuple(value) for key, value in json.loads(args.peers.read_text()).items()}
    if set(peers) != set(range(len(peers))) or args.node not in peers:
        parser.error("peer IDs must be consecutive and include this node")
    directory = args.data_dir or Path("data") / f"node_{args.node}"
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    env = RealEnvironment(secrets.randbits(64))
    store = DurableStore(directory, allow_quarantine=True)
    io = DiskWorker()
    network = None
    exit_code = 0
    try:
        network = TCPNetwork(args.node, (args.bind, args.port), peers, env, args.workers)
        replica = ProductionReplica(args.node, len(peers), env, network, store, io,
                                     args.bootstrap, args.checkpoint_every)
        network.control = replica.control
        emit("ready", node=args.node, bind=args.bind, port=args.port,
             data_dir=str(directory.resolve()), threads=args.workers + 2, status=replica.status)
        while not stopping:
            io.poll()
            network.poll()
            env.poll()
            time.sleep(0.0005)
        deadline = time.monotonic() + 10
        while replica.busy and time.monotonic() < deadline:
            io.poll()
            time.sleep(0.001)
        if replica.busy:
            raise RuntimeError("durability work did not drain before shutdown")
        emit("shutdown", node=args.node, commit=replica.commit, view=replica.view,
             storage=dict(store.stats), exit_code=0)
    except Exception as error:
        emit("fatal", node=args.node, error=repr(error))
        traceback.print_exc()
        exit_code = 1
    finally:
        if network is not None:
            network.close()
        io.close()
        store.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
