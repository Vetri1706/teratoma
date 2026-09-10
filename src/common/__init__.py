"""Deterministic shared utilities."""
from .codec import canonical_decode, canonical_encode
from .hashing import crc32, murmur3_32, sha256_digest, sha256_hex
from .prng import Xorshift128Plus
