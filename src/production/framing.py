"""CRC-protected TCP frames with bounded, ordered logical-message continuation."""
import hashlib
import itertools
import socket
import struct
import zlib
from src.common.codec import canonical_encode, canonical_decode

MAGIC = b"AEG1"
FRAGMENT_MAGIC = b"AEF1"
HEADER = struct.Struct("!4sIIII")
FRAGMENT_HEADER = struct.Struct("!QII32s")
MAX_FRAME = 16 * 1024 * 1024
FRAGMENT_BYTES = 256 * 1024
MAX_MESSAGE = 64 * 1024 * 1024
_message_ids = itertools.count(1)


class FrameError(ValueError):
    """Malformed frame or explicit transport resource limit."""


def _physical_header(magic, source, target, buffers):
    length = sum(len(buffer) for buffer in buffers)
    checksum = zlib.crc32(struct.pack("!III", length, source, target))
    for buffer in buffers:
        checksum = zlib.crc32(buffer, checksum)
    return HEADER.pack(magic, length, source, target, checksum & 0xFFFFFFFF)


def _physical_frame(magic, source, target, body):
    return b"".join((_physical_header(magic, source, target, (body,)), body))


class OutboundMessage:
    """Own canonical bytes; produce bounded physical frames only as requested."""
    def __init__(self, source, target, payload):
        if type(source) is not int or type(target) is not int or not 0 <= source < 2**32 or not 0 <= target < 2**32:
            raise FrameError("invalid frame routing")
        if not isinstance(payload, dict):
            raise FrameError("logical payload must be an object")
        self.body = canonical_encode(payload)
        if not 0 < len(self.body) <= MAX_MESSAGE:
            raise FrameError("logical message exceeds 64 MiB limit")
        self.source, self.target = source, target
        self.fragmented = len(self.body) > FRAGMENT_BYTES
        self.message_id = next(_message_ids) if self.fragmented else 0
        if self.message_id >= 2**64:
            raise FrameError("transport message identifiers exhausted")
        self.digest = hashlib.sha256(self.body).digest() if self.fragmented else None
        count = (len(self.body) + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
        self.wire_size = len(self.body) + (count * (HEADER.size + FRAGMENT_HEADER.size) if self.fragmented else HEADER.size)

    def _iter_frame_buffers(self):
        body = memoryview(self.body)
        if not self.fragmented:
            yield (_physical_header(MAGIC, self.source, self.target, (body,)), body)
            return
        for offset in range(0, len(body), FRAGMENT_BYTES):
            chunk = body[offset:offset + FRAGMENT_BYTES]
            metadata = FRAGMENT_HEADER.pack(self.message_id, len(body), offset, self.digest)
            header = _physical_header(FRAGMENT_MAGIC, self.source, self.target, (metadata, chunk))
            yield header, metadata, chunk

    def iter_buffers(self):
        """Yield headers and read-only views into the owned canonical body."""
        for buffers in self._iter_frame_buffers():
            yield from buffers

    def iter_frames(self):
        for buffers in self._iter_frame_buffers():
            yield b"".join(buffers)


def frame(source, target, payload):
    """Blocking-client convenience; the reactor uses lazy OutboundMessage objects."""
    return b"".join(OutboundMessage(source, target, payload).iter_frames())


def _validate_header(magic, length):
    maximum = MAX_FRAME if magic == MAGIC else FRAGMENT_HEADER.size + FRAGMENT_BYTES
    if magic not in (MAGIC, FRAGMENT_MAGIC) or not 0 < length <= maximum:
        raise FrameError("invalid frame header")


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.assembly = None

    @staticmethod
    def _decode(body):
        try:
            payload = canonical_decode(body)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise FrameError(f"invalid JSON payload: {error}") from error
        if not isinstance(payload, dict):
            raise FrameError("frame payload must be an object")
        return payload

    def _accept(self, magic, source, target, body):
        if magic == MAGIC:
            if self.assembly is not None:
                raise FrameError("logical messages interleaved during continuation")
            return source, target, self._decode(body)
        if len(body) <= FRAGMENT_HEADER.size:
            raise FrameError("empty continuation")
        identifier, total, offset, digest = FRAGMENT_HEADER.unpack_from(body)
        chunk = body[FRAGMENT_HEADER.size:]
        if not 0 < total <= MAX_MESSAGE or offset + len(chunk) > total:
            raise FrameError("continuation exceeds logical bounds")
        key = (source, target, identifier, total, digest)
        if self.assembly is None:
            if offset != 0:
                raise FrameError("continuation does not start at zero")
            self.assembly = (key, bytearray())
        expected, collected = self.assembly
        if key != expected or offset != len(collected):
            raise FrameError("continuation identity or offset changed")
        collected.extend(chunk)
        if len(collected) < total:
            return None
        self.assembly = None
        raw = bytes(collected)
        if hashlib.sha256(raw).digest() != digest:
            raise FrameError("logical message SHA-256 mismatch")
        return source, target, self._decode(raw)

    def feed(self, data):
        self.buffer.extend(data)
        messages, offset = [], 0
        while len(self.buffer) - offset >= HEADER.size:
            magic, length, source, target, checksum = HEADER.unpack_from(self.buffer, offset)
            _validate_header(magic, length)
            end = offset + HEADER.size + length
            if len(self.buffer) < end:
                break
            body = bytes(self.buffer[offset + HEADER.size:end])
            actual = zlib.crc32(struct.pack("!III", length, source, target) + body) & 0xFFFFFFFF
            if actual != checksum:
                raise FrameError("frame CRC32 mismatch")
            message = self._accept(magic, source, target, body)
            if message is not None:
                messages.append(message)
            offset = end
        if offset:
            del self.buffer[:offset]
        if len(self.buffer) > MAX_FRAME + HEADER.size:
            raise FrameError("receive buffer exceeded limit")
        return messages


def read_frame(sock):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = sock.recv(size - len(data))
            if not part:
                raise EOFError("TCP peer closed during frame")
            data.extend(part)
        return bytes(data)
    decoder = Decoder()
    while True:
        header = exact(HEADER.size)
        magic, length, _, _, _ = HEADER.unpack(header)
        _validate_header(magic, length)
        messages = decoder.feed(header + exact(length))
        if messages:
            return messages[0]


def rpc(address, target, payload, source=999, timeout=5):
    with socket.create_connection(address, timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(frame(source, target, payload))
        return read_frame(connection)[2]
