"""Canonical JSON codec with strict decoding."""
import json


def canonical_encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_decode(data):
    def invalid_constant(value):
        raise ValueError(f"non-finite JSON number {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key {key!r}")
            result[key] = value
        return result

    return json.loads(data.decode("utf-8"), parse_constant=invalid_constant, object_pairs_hook=unique_object)
