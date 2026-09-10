"""Checksums and specified byte hashes."""
import hashlib
import zlib


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def sha256_digest(data):
    return hashlib.sha256(data).digest()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def murmur3_32(data, seed=0):
    mask = 0xFFFFFFFF
    result = seed & mask
    blocks_end = len(data) - len(data) % 4
    for offset in range(0, blocks_end, 4):
        value = int.from_bytes(data[offset:offset + 4], "little")
        value = value * 0xCC9E2D51 & mask
        value = ((value << 15) | (value >> 17)) & mask
        value = value * 0x1B873593 & mask
        result ^= value
        result = ((result << 13) | (result >> 19)) & mask
        result = (result * 5 + 0xE6546B64) & mask
    if data[blocks_end:]:
        value = int.from_bytes(data[blocks_end:], "little")
        value = value * 0xCC9E2D51 & mask
        value = ((value << 15) | (value >> 17)) & mask
        result ^= value * 0x1B873593 & mask
    result ^= len(data)
    result ^= result >> 16
    result = result * 0x85EBCA6B & mask
    result ^= result >> 13
    result = result * 0xC2B2AE35 & mask
    result ^= result >> 16
    return result & mask
