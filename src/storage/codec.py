"""Canonical bounded encodings for storage primitives."""
import math
import struct
MAX_FIELD = 64 * 1024 * 1024


def encode_value(value):
    if value is None:
        return b"\x00"
    if type(value) is int:
        if not -(1 << 63) <= value < (1 << 63):
            raise ValueError("integer outside signed Int64")
        return b"\x01" + struct.pack(">q", value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite floats are unsupported")
        return b"\x02" + struct.pack(">d", value)
    if type(value) is str:
        raw = value.encode("utf-8")
        if len(raw) > MAX_FIELD:
            raise ValueError("string exceeds field bound")
        return b"\x03" + struct.pack(">I", len(raw)) + raw
    raise TypeError("values must be Int64, Float64, Utf8String, or null")


def decode_value(raw):
    if not raw:
        raise ValueError("missing value tag")
    tag = raw[0]
    if tag == 0 and len(raw) == 1:
        return None
    if tag == 1 and len(raw) == 9:
        return struct.unpack(">q", raw[1:])[0]
    if tag == 2 and len(raw) == 9:
        value = struct.unpack(">d", raw[1:])[0]
        if not math.isfinite(value):
            raise ValueError("non-finite stored float")
        return value
    if tag == 3 and len(raw) >= 5:
        size = struct.unpack(">I", raw[1:5])[0]
        if size <= MAX_FIELD and len(raw) == size + 5:
            return raw[5:].decode("utf-8")
    raise ValueError("invalid encoded value")


def encode_key(key):
    if type(key) is not str:
        raise TypeError("keys must be strings")
    raw = key.encode("utf-8")
    if len(raw) > MAX_FIELD:
        raise ValueError("key exceeds field bound")
    return raw
