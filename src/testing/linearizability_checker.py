"""Reference interpreter and witnessed linearizability/durability checker.

The checker uses the replicated serial order as a witness, then independently
checks transaction semantics and real-time constraints of the client history.
It is intentionally separate from storage, query operators, and state-machine
implementation. Finite traces are evidence, not an unbounded protocol proof.
"""
import json
from src.storage import WAL
from src.consensus.types import digest


def reference_query(state, spec):
    rows = [{"key": key, "value": value} for key, value in sorted(state.items())]
    operators = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
                 ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
                 "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
    for column, op, value in spec.get("filters", []):
        if op == "is_null":
            rows = [row for row in rows if row[column] is None]
        elif op == "is_not_null":
            rows = [row for row in rows if row[column] is not None]
        else:
            rows = [row for row in rows if row[column] is not None and value is not None
                    and operators[op](row[column], value)]
    columns = spec.get("group_by", [])
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[c] for c in columns), []).append(row)
    if not columns and not rows:
        groups[()] = []
    output = []
    for key, members in groups.items():
        result = dict(zip(columns, key))
        for alias, (op, column) in spec["aggregates"].items():
            values = [row[column] for row in members if row[column] is not None] if column else []
            op = op.lower()
            if op == "count":
                value = len(members) if column is None else len(values)
            elif not values:
                value = None
            elif op == "sum":
                value = sum(values)
            elif op == "min":
                value = min(values)
            elif op == "max":
                value = max(values)
            elif op == "avg":
                value = sum(values) / len(values)
            else:
                raise ValueError("unknown reference aggregate")
            result[alias] = value
        output.append(result)
    return sorted(output, key=lambda item: json.dumps(item, sort_keys=True))


class InvariantChecker:
    def __init__(self, env, replicas):
        self.env, self.replicas = env, replicas
        self.entries = []
        self.state = {}
        self.results = {}
        self.invocations = {}
        self.completed = {}
        self.highest_completed = 0
        self.disk_cache = {}
        self.logical_cache = {}
        self.checks = 0
        self.storage_comparisons = 0
        self.durability_checks = 0

    def invoke(self, request):
        identity = (request["client"], request["seq"])
        if identity in self.invocations:
            raise AssertionError("duplicate client invocation")
        self.invocations[identity] = {"tick": self.env.now, "request": request,
                                      "floor": self.highest_completed}

    def _extend(self, entry):
        identity = (entry.request["client"], entry.request["seq"])
        if identity not in self.invocations:
            raise AssertionError("committed an uninvoked transaction")
        if digest(entry.request) != digest(self.invocations[identity]["request"]):
            raise AssertionError("committed transaction differs from invocation")
        if identity not in self.results:
            request = entry.request
            accepted = all(self.state.get(k) == v for k, v in request.get("expect", {}).items())
            result = {"reads": {key: self.state.get(key) for key in request.get("reads", [])},
                      "accepted": accepted}
            if "query" in request:
                result["query"] = reference_query(self.state, request["query"])
            if accepted:
                self.state.update(request.get("writes", {}))
            self.results[identity] = result
        self.entries.append(entry)

    def observe_commits(self):
        for replica in self.replicas:
            if not replica.alive or replica.status in {"booting", "recovering"}:
                continue
            for entry in replica.log[:replica.commit]:
                if entry.index <= len(self.entries):
                    if self.entries[entry.index - 1].hash != entry.hash:
                        raise AssertionError(f"committed prefix conflict at {entry.index}")
                else:
                    self._extend(entry)
            if replica.commit == len(self.entries) and replica.machine.storage.snapshot() != self.state:
                raise AssertionError("replica state disagrees with independent transaction replay")

    def reply(self, message):
        self.observe_commits()
        identity = (message["client"], message["seq"])
        invocation = self.invocations.get(identity)
        if invocation is None:
            raise AssertionError("reply to nonexistent invocation")
        index = message["index"]
        if not 1 <= index <= len(self.entries):
            raise AssertionError("reply before witnessed commit")
        entry = self.entries[index - 1]
        if entry.hash != message["hash"] or digest(invocation["request"]) != message["request_hash"]:
            raise AssertionError("reply log/request mismatch")
        actual = dict(message["result"])
        if "query" in actual:
            actual["query"] = sorted(actual["query"], key=lambda row: json.dumps(row, sort_keys=True))
        if actual != self.results[identity]:
            raise AssertionError(f"incorrect transaction result: {actual} != {self.results[identity]}")
        if index <= invocation["floor"]:
            raise AssertionError("real-time order violation: stale read or reordered transaction")
        if identity in self.completed:
            if self.completed[identity]["index"] != index:
                raise AssertionError("same client request executed twice")
            return
        self.completed[identity] = {"tick": self.env.now, "index": index}
        self.highest_completed = max(index, self.highest_completed)

    def _durable_log(self, replica):
        revision = replica.disk.revision
        cached = self.disk_cache.get(replica.id)
        if cached and cached[0] == revision:
            return cached[1]
        blocks = []
        index = 0
        while index in replica.disk.blocks:
            blocks.append(replica.disk.blocks[index])
            index += 1
        scan = WAL.scan(blocks)
        log = []
        for _, payload in scan.records:
            record = json.loads(payload)
            if record["kind"] == "install":
                log = record["log"]
            elif record["kind"] == "append":
                log.extend(record["entries"])
        self.disk_cache[replica.id] = (revision, log)
        return log

    def check(self):
        self.checks += 1
        self.observe_commits()
        by_commit = {}
        for replica in self.replicas:
            if not replica.alive or replica.status in {"recovering", "booting"}:
                continue
            key = (replica.generation, replica.commit)
            cached = self.logical_cache.get(replica.id)
            if cached is None or cached[0] != key:
                cached = (key, replica.machine.storage.digest(), replica.machine.storage.snapshot())
                self.logical_cache[replica.id] = cached
            _, physical, logical = cached
            value = (physical, logical)
            if replica.commit in by_commit:
                self.storage_comparisons += 1
                if by_commit[replica.commit] != value:
                    raise AssertionError("physical/logical storage differs at equal commit index")
            by_commit[replica.commit] = value
        if self.highest_completed:
            index = self.highest_completed
            expected = self.entries[index - 1].hash
            if not any(len(log := self._durable_log(replica)) >= index and log[index - 1]["hash"] == expected
                       for replica in self.replicas):
                raise AssertionError("all durable copies of an acknowledged operation were lost")
            self.durability_checks += 1
