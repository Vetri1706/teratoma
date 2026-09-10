"""Canonical protocol records and validation independent of transport."""
from dataclasses import dataclass
import hashlib
import json
import math

ZERO_HASH = "0" * 64


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    if set(request) - {"client", "seq", "reads", "writes", "expect", "query"}:
        raise ValueError("unknown transaction field")
    if type(request.get("client")) is not int or request["client"] < 0:
        raise ValueError("invalid client id")
    if type(request.get("seq")) is not int or request["seq"] < 1:
        raise ValueError("invalid client sequence")
    reads = request.get("reads", [])
    if not isinstance(reads, list) or any(not isinstance(k, str) for k in reads):
        raise ValueError("read keys must be strings")
    for field in ("writes", "expect"):
        values = request.get(field, {})
        if not isinstance(values, dict):
            raise ValueError("transaction maps must be objects")
        for key, value in values.items():
            if not isinstance(key, str) or type(value) not in (int, float, str, type(None)):
                raise ValueError("invalid key or value")
            if type(value) is float and not math.isfinite(value):
                raise ValueError("non-finite float")
            if type(value) is int and not -(1 << 63) <= value < (1 << 63):
                raise ValueError("integer outside Int64")
    if "query" in request:
        query = request["query"]
        if not isinstance(query, dict) or set(query) - {"group_by", "aggregates", "filters"}:
            raise ValueError("invalid query")
        # Operator/schema errors become deterministic query_error results at execution.
    if len(encode(request)) > 65536:
        raise ValueError("transaction exceeds 64 KiB")
    return json.loads(encode(request))


@dataclass(frozen=True)
class Entry:
    index: int
    view: int
    request: dict
    previous: str
    hash: str

    @classmethod
    def create(cls, index, view, request, previous):
        body = {"index": index, "view": view, "request": validate_request(request),
                "previous": previous}
        return cls(**body, hash=digest(body))

    def wire(self):
        return {"index": self.index, "view": self.view, "request": self.request,
                "previous": self.previous, "hash": self.hash}

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict) or set(raw) != {"index", "view", "request", "previous", "hash"}:
            raise ValueError("invalid entry fields")
        if type(raw["index"]) is not int or raw["index"] < 1:
            raise ValueError("invalid operation number")
        if type(raw["view"]) is not int or raw["view"] < 0:
            raise ValueError("invalid entry view")
        entry = cls.create(raw["index"], raw["view"], raw["request"], raw["previous"])
        if entry.hash != raw["hash"]:
            raise ValueError("entry hash mismatch")
        return entry


def parse_log(raw):
    if not isinstance(raw, list):
        raise ValueError("log must be a list")
    log = [Entry.parse(item) for item in raw]
    previous = ZERO_HASH
    for index, entry in enumerate(log, 1):
        if entry.index != index or entry.previous != previous:
            raise ValueError("noncontiguous hash chain")
        previous = entry.hash
    return log
