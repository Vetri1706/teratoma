"""Two-phase view change; choose latest normal view then longest log."""
from .types import parse_log


class ViewChange:
    def _begin_view(self, view, trigger=None):
        if view <= self.view or self.status == "recovering":
            return

        def changed():
            self.svc = {self.id}
            self.dvc.clear()
            self.acks.clear()
            self.last_contact = self.env.now
            self.stats["view_changes"] += 1
            self._trace("view_change")
            self._broadcast({"type": "start_view_change", "view": self.view})
            if trigger is not None:
                self._dispatch(*trigger)

        self._persist({"kind": "view", "view": view}, changed)

    def _on_start_view_change(self, sender, message):
        view = message["view"]
        if view > self.view:
            self._begin_view(view, (sender, message))
            return
        if self.status != "view-change" or view != self.view:
            return
        self.svc.add(sender)
        if len(self.svc) >= self.quorum:
            self._send_do_view_change()

    def _send_do_view_change(self):
        self._send(self.view % self.n,
                   {"type": "do_view_change", "view": self.view,
                    "last_normal": self.last_normal, "commit": self.commit,
                    "log": [entry.wire() for entry in self.log]})

    def _on_do_view_change(self, sender, message):
        if message["view"] > self.view:
            self._begin_view(message["view"], (sender, message))
            return
        if self.status != "view-change" or not self.primary or message["view"] != self.view:
            return
        log = parse_log(message["log"])
        if not 0 <= message["commit"] <= len(log) or not 0 <= message["last_normal"] < self.view:
            raise ValueError("invalid view-change certificate")
        self.dvc[sender] = message
        if len(self.dvc) < self.quorum:
            return
        selected = max(self.dvc.values(), key=lambda m: (m["last_normal"], len(m["log"])))
        commit = max(self.commit, max(m["commit"] for m in self.dvc.values()))
        if commit > len(selected["log"]):
            raise AssertionError("selected log shorter than known committed prefix")
        for report in self.dvc.values():
            for left, right in zip(selected["log"][:report["commit"]], report["log"][:report["commit"]]):
                if left["hash"] != right["hash"]:
                    raise AssertionError("view change discarded a committed operation")
        payload = {"view": self.view, "commit": commit, "log": selected["log"]}

        def started():
            self.svc.clear()
            self.dvc.clear()
            self.acks = {self.id: len(self.log)}
            self.last_contact = self.env.now
            self.last_progress = self.env.now
            self._trace("start_view", commit=self.commit, length=len(self.log))
            for target in range(self.n):
                if target != self.id:
                    self._send_snapshot(target, "start_view")
            self._reply_committed()

        self._install(payload, started)
