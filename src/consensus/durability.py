"""Serialized durable protocol transitions; no message precedes its WAL write."""
import json
from src.storage import WAL
from .types import encode, parse_log, Entry
from .state_machine import StateMachine


class Durability:
    def _load(self, source=None):
        source = self.disk.blocks if source is None else source
        blocks = []
        index = 0
        while index in source:
            blocks.append(source[index])
            index += 1
        scan = WAL.scan(blocks)
        self.next_block = scan.valid_blocks
        self.wal_seq = 0
        try:
            for seq, payload in scan.records:
                self._apply_record(json.loads(payload))
                self.wal_seq = seq
        except (ValueError, KeyError, TypeError, AssertionError) as error:
            self._trace("recovery_decode", error=str(error))
            self.status = "recovering"
            return False
        # Any hole, checksum failure, or empty reboot needs quorum recovery.
        if scan.error or len(source) != scan.valid_blocks or not scan.records:
            self.status = "recovering"
            return False
        return True

    def _boot_read(self):
        """Read recovery blocks asynchronously with at most 32 outstanding I/Os."""
        addresses = sorted(self.disk.blocks)
        generation = self.generation
        recovered = {}
        state = {"next": 0, "pending": 0, "failed": False, "done": False}

        def complete():
            if state["done"]:
                return
            state["done"] = True
            self.status = "normal"
            intact = not state["failed"] and self._load(recovered)
            if not intact:
                self.status = "recovering"
                self._request_recovery()
            elif self.status == "normal" and self.primary:
                self.acks = {self.id: len(self.log)}
            self.last_contact = self.env.now
            self.last_progress = self.env.now
            self._trace("reboot", status=self.status, commit=self.commit)
            self._drain()

        def pump():
            if not self.alive or generation != self.generation:
                return
            if state["next"] == len(addresses) and state["pending"] == 0:
                complete()
                return
            while state["pending"] < 32 and state["next"] < len(addresses):
                address = addresses[state["next"]]
                state["next"] += 1
                state["pending"] += 1

                def read(data, address=address):
                    if not self.alive or generation != self.generation:
                        return
                    state["pending"] -= 1
                    if data is None:
                        state["failed"] = True
                    else:
                        recovered[address] = data
                    pump()

                self.disk.read(address, read)

        pump()

    def _apply_record(self, record):
        kind = record["kind"]
        if kind == "view":
            if record["view"] <= self.view:
                raise ValueError("view must increase")
            self.view = record["view"]
            self.status = "view-change"
        elif kind == "append":
            for raw in record["entries"]:
                entry = Entry.parse(raw)
                previous = self.log[-1].hash if self.log else "0" * 64
                if entry.index != len(self.log) + 1 or entry.previous != previous:
                    raise ValueError("invalid durable append")
                self.log.append(entry)
        elif kind == "install":
            log = parse_log(record["log"])
            commit = record["commit"]
            if not 0 <= commit <= len(log):
                raise ValueError("invalid installed commit")
            if self.commit and (len(log) < self.commit or any(
                    a.hash != b.hash for a, b in zip(self.log[:self.commit], log[:self.commit]))):
                raise AssertionError("install would overwrite a committed prefix")
            if record["view"] < self.view:
                raise ValueError("view regression")
            self.view = record["view"]
            self.last_normal = record["last_normal"]
            self.log = log
            self.status = "normal"
            # Rebuild all deterministic physical state from the authoritative log.
            self.machine = StateMachine()
            self.commit = 0
            self._apply_commit(commit)
        elif kind == "commit":
            self._apply_commit(record["commit"])
        else:
            raise ValueError("unknown WAL event")

    def _apply_commit(self, commit):
        if not self.commit <= commit <= len(self.log):
            raise ValueError("commit number outside durable log")
        for entry in self.log[self.commit:commit]:
            self.machine.apply(entry)
        self.commit = commit

    def _persist(self, record, after):
        if self.busy:
            raise AssertionError("overlapping durable transitions")
        self.busy = True
        generation = self.generation
        sequence = self.wal_seq + 1
        blocks = WAL.encode_record(sequence, encode(record))
        start = self.next_block

        def write_at(offset):
            if not self.alive or generation != self.generation:
                return
            if offset == len(blocks):
                self.wal_seq = sequence
                self.next_block += len(blocks)
                self._apply_record(record)
                self.busy = False
                after()
                self._drain()
                return

            def written(success):
                if not self.alive or generation != self.generation:
                    return
                if not success:
                    self._trace("io_failure", block=start + offset)
                    self.crash()
                    self.env.schedule(30, self.reboot, "disk-failure-reboot")
                    return
                write_at(offset + 1)

            self.disk.write(start + offset, blocks[offset], written)

        write_at(0)

    def _install(self, payload, after):
        self._persist({"kind": "install", "view": payload["view"],
                       "last_normal": payload["view"], "commit": max(self.commit, payload["commit"]),
                       "log": payload["log"]}, after)

    def _repair(self, payload):
        """Erase only while quarantined and after obtaining fresh quorum state."""
        generation = self.generation
        self.busy = True

        def erased(success):
            if not self.alive or generation != self.generation:
                return
            self.busy = False
            if not success:
                self.crash()
                self.env.schedule(30, self.reboot, "erase-failure-reboot")
                return
            self.next_block = 0
            self.wal_seq = 0
            # Keep the recovered committed prefix check; never trust an older snapshot.
            self._install(payload, self._recovered)

        self.disk.erase(erased)
