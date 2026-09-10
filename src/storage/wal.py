"""Checksummed fixed-block WAL with complete-prefix crash recovery."""
from dataclasses import dataclass
import struct
import zlib

BLOCK_SIZE = 4096
MAGIC = b"AEGWAL01"
HEADER = struct.Struct(">8sQQIIIIII")
PAYLOAD_SIZE = BLOCK_SIZE - HEADER.size
MAX_RECORD = 64 * 1024 * 1024


@dataclass(frozen=True)
class ScanResult:
    records: list
    valid_blocks: int
    error: str | None

    @property
    def next_sequence(self):
        return len(self.records) + 1


def encode_record(seq, payload):
    if type(seq) is not int or not 1 <= seq < (1 << 64):
        raise ValueError("WAL sequence must be positive UInt64")
    if not isinstance(payload, bytes):
        raise TypeError("WAL payload must be bytes")
    if len(payload) > MAX_RECORD:
        raise ValueError("WAL record exceeds 64 MiB")
    count = max(1, (len(payload) + PAYLOAD_SIZE - 1) // PAYLOAD_SIZE)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    result = []
    for part in range(count):
        chunk = payload[part * PAYLOAD_SIZE:(part + 1) * PAYLOAD_SIZE]
        header = HEADER.pack(MAGIC, seq, len(payload), part, count, len(chunk), checksum, zlib.crc32(chunk) & 0xFFFFFFFF, 0)
        header = header[:-4] + struct.pack(">I", zlib.crc32(header[:-4]) & 0xFFFFFFFF)
        result.append(header + chunk + bytes(PAYLOAD_SIZE - len(chunk)))
    return result


def scan(blocks):
    records, chunks = [], []
    valid = offset = 0
    expected = None
    for block in blocks:
        offset += 1
        if not isinstance(block, bytes) or len(block) != BLOCK_SIZE:
            return ScanResult(records, valid, "truncated WAL block")
        header = block[:HEADER.size]
        magic, seq, size, part, count, length, checksum, chunk_crc, header_crc = HEADER.unpack(header)
        if magic != MAGIC or zlib.crc32(header[:-4]) & 0xFFFFFFFF != header_crc:
            return ScanResult(records, valid, "invalid WAL header")
        if seq != len(records) + 1 or part != len(chunks):
            return ScanResult(records, valid, "nonconsecutive WAL sequence or fragment")
        parts = max(1, (size + PAYLOAD_SIZE - 1) // PAYLOAD_SIZE)
        chunk_size = min(PAYLOAD_SIZE, max(0, size - part * PAYLOAD_SIZE))
        if size > MAX_RECORD or count != parts or not 0 <= part < count or length != chunk_size:
            return ScanResult(records, valid, "invalid WAL bounds")
        if part == 0:
            expected = (count, size, checksum)
        elif (count, size, checksum) != expected:
            return ScanResult(records, valid, "inconsistent WAL fragments")
        chunk = block[HEADER.size:HEADER.size + length]
        if zlib.crc32(chunk) & 0xFFFFFFFF != chunk_crc or any(block[HEADER.size + length:]):
            return ScanResult(records, valid, "corrupt WAL payload")
        chunks.append(chunk)
        if len(chunks) == count:
            payload = b"".join(chunks)
            if len(payload) != size or zlib.crc32(payload) & 0xFFFFFFFF != checksum:
                return ScanResult(records, valid, "invalid WAL record checksum")
            records.append((seq, payload))
            valid, chunks = offset, []
    return ScanResult(records, valid, "incomplete WAL record" if chunks else None)


class WAL:
    BLOCK_SIZE = BLOCK_SIZE
    PAYLOAD_SIZE = PAYLOAD_SIZE
    encode_record = staticmethod(encode_record)
    scan = staticmethod(scan)
