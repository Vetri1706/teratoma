"""Atomic deterministic transactions and logged snapshot reads."""
from src.storage import LSMTree
from src.query import QueryEngine
from .types import digest


class StateMachine:
    def __init__(self):
        self.storage = LSMTree()
        self.results = {}
        self.requests = {}
        self.query = QueryEngine()
        self.applied = 0

    def rows(self):
        return [{"key": key, "value": value} for key, value in self.storage.items()]

    def apply(self, entry):
        if entry.index != self.applied + 1:
            raise AssertionError("application order violation")
        request = entry.request
        identity = (request["client"], request["seq"])
        fingerprint = digest(request)
        if identity in self.results:
            if self.requests[identity] != fingerprint:
                raise AssertionError("request identity reused for different transaction")
            self.storage.apply(entry.index, {})
            self.applied = entry.index
            return self.results[identity]
        reads = {key: self.storage.get(key) for key in request.get("reads", [])}
        accepted = all(self.storage.get(key) == value
                       for key, value in request.get("expect", {}).items())
        result = {"reads": reads, "accepted": accepted}
        if "query" in request:
            try:
                result["query"] = self.query.execute(self.rows(), **request["query"])
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                result["query_error"] = str(error)
        self.storage.apply(entry.index, request.get("writes", {}) if accepted else {})
        self.applied = entry.index
        self.results[identity] = result
        self.requests[identity] = fingerprint
        return result
