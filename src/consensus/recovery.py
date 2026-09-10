"""Checksummed chunked state transfer and nonce-bound quorum recovery."""
import hashlib
import json
from .types import encode, digest, parse_log


class Recovery:
    def _snapshot(self):
        return {"view": self.view, "commit": self.commit,
                "log": [entry.wire() for entry in self.log]}

    def _send_snapshot(self, target, kind, nonce=None):
        payload = self._snapshot()
        payload.update({"kind": kind, "nonce": nonce})
        data = encode(payload)
        transfer = hashlib.sha256(data).hexdigest()
        chunk_size = 4096
        count = max(1, (len(data) + chunk_size - 1) // chunk_size)
        for index in range(count):
            self._send(target, {"type": "chunk", "view": self.view, "transfer": transfer,
                                "part": index, "count": count,
                                "data": data[index * chunk_size:(index + 1) * chunk_size].hex()})

    def _on_chunk(self, sender, message):
        count, part = message["count"], message["part"]
        if type(count) is not int or type(part) is not int or not 0 <= part < count <= 16384:
            raise ValueError("invalid transfer bounds")
        if message["view"] < self.view or sender != message["view"] % self.n:
            return
        key = (sender, message["transfer"])
        if key not in self.transfers:
            if len(self.transfers) >= 8:
                oldest = min(self.transfers, key=lambda k: self.transfers[k]["created"])
                del self.transfers[oldest]
            self.transfers[key] = {"count": count, "chunks": {}, "created": self.env.now}
        transfer = self.transfers[key]
        if transfer["count"] != count:
            raise ValueError("transfer size changed")
        data = bytes.fromhex(message["data"])
        if len(data) > 4096:
            raise ValueError("oversized transfer chunk")
        transfer["chunks"][part] = data
        if len(transfer["chunks"]) != count:
            return
        raw = b"".join(transfer["chunks"][i] for i in range(count))
        del self.transfers[key]
        if hashlib.sha256(raw).hexdigest() != message["transfer"]:
            raise ValueError("snapshot digest mismatch")
        payload = json.loads(raw)
        self._accept_snapshot(sender, payload, message["view"])

    def _accept_snapshot(self, sender, payload, view):
        if type(view) is not int or view < self.view or sender != view % self.n:
            return
        parse_log(payload["log"])
        if payload["view"] != view or type(payload["commit"]) is not int or not 0 <= payload["commit"] <= len(payload["log"]):
            raise ValueError("invalid snapshot metadata")
        if payload["kind"] == "recovery":
            if self.status == "recovering" and payload["nonce"] == self.recovery_nonce:
                self.recovery_primary[sender] = payload
                self._try_recover()
        elif payload["kind"] == "state":
            if self.status == "normal" and payload["view"] == self.view:
                if payload["commit"] < self.commit or len(payload["log"]) < len(self.log):
                    return
                if len(payload["log"]) == len(self.log) and (not self.log or payload["log"][-1]["hash"] == self.log[-1].hash):
                    self._ack()
                    self._follow_commit(payload["commit"])
                    return
                self._install(payload, self._state_installed)
        elif payload["kind"] == "start_view":
            if self.status != "recovering" and (payload["view"] > self.view or self.status == "view-change"):
                self._install(payload, self._state_installed)
        else:
            raise ValueError("unknown snapshot purpose")

    def _request_recovery(self):
        self._broadcast({"type": "recovery", "view": self.view, "nonce": self.recovery_nonce})

    def _on_recovery(self, sender, message):
        if message["view"] > self.view:
            self._begin_view(message["view"])
            return
        if self.status != "normal":
            return
        self._send(sender, {"type": "recovery_response", "view": self.view,
                            "nonce": message["nonce"]})
        if self.primary:
            self._send_snapshot(sender, "recovery", message["nonce"])

    def _on_recovery_response(self, sender, message):
        if self.status != "recovering" or message["nonce"] != self.recovery_nonce:
            return
        self.recovery_responses[sender] = message["view"]
        self._try_recover()

    def _try_recover(self):
        if len(self.recovery_responses) < self.quorum:
            return
        view = max(self.recovery_responses.values())
        primary = view % self.n
        payload = self.recovery_primary.get(primary)
        if payload is None or payload["view"] != view or view < self.view:
            return
        if primary not in self.recovery_responses:
            return
        self._repair(payload)

    def _recovered(self):
        self.stats["recoveries"] += 1
        self.recovery_responses.clear()
        self.recovery_primary.clear()
        self._trace("recovered", commit=self.commit)
        self._state_installed()

    def _state_installed(self):
        self.svc.clear()
        self.dvc.clear()
        self.last_contact = self.env.now
        self.last_progress = self.env.now
        self._send(self.view % self.n, {"type": "prepare_ok", "view": self.view,
                                      "index": len(self.log),
                                      "hash": self.log[-1].hash if self.log else "0" * 64})

    def _on_get_state(self, sender, message):
        if self.status == "normal" and self.primary and message["view"] == self.view:
            self._send_snapshot(sender, "start_view" if message.get("start") else "state")
