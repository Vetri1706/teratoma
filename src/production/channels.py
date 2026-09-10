"""Native lock-free SPSC queues carrying owned Python-object handles."""
import ctypes
from pathlib import Path
import struct
import threading
import sys

_library = None


def library():
    global _library
    if _library is None:
        directory = Path(__file__).resolve().parents[2] / "bin"
        candidates = [directory / "libaegis_native.dylib", directory / "libaegis_native.so"]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise RuntimeError("native queues are not built; run python3 scripts/build_native.py")
        lib = ctypes.CDLL(str(path))
        lib.aegis_queue_create.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        lib.aegis_queue_create.restype = ctypes.c_void_p
        lib.aegis_queue_push.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        lib.aegis_queue_push.restype = ctypes.c_int
        lib.aegis_queue_pop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        lib.aegis_queue_pop.restype = ctypes.c_int64
        lib.aegis_queue_destroy.argtypes = [ctypes.c_void_p]
        lib.aegis_queue_destroy.restype = None
        _library = lib
    return _library


class NativeChannel:
    """One producer, one consumer; payload ownership transfers with a native token.

    The C queue performs acquire/release atomic publication without a mutex.
    CPython owns object allocation/refcounts outside that queue operation.
    """
    def __init__(self, capacity=8192):
        if hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled():
            raise RuntimeError("Python payload ownership requires a GIL-enabled CPython build")
        self.lib = library()
        self.pointer = self.lib.aegis_queue_create(capacity, 8)
        if not self.pointer:
            raise RuntimeError("native SPSC allocation or lock-free atomic check failed")
        self.objects = {}
        self.serial = 0
        self.producer = self.consumer = None
        self.buffer = ctypes.create_string_buffer(8)

    def put(self, value):
        identity = threading.get_ident()
        if self.producer is None:
            self.producer = identity
        if identity != self.producer:
            raise RuntimeError("SPSC producer ownership violated")
        self.serial += 1
        token = struct.pack("=Q", self.serial)
        self.objects[self.serial] = value
        accepted = self.lib.aegis_queue_push(self.pointer, token, 8)
        if accepted != 1:
            del self.objects[self.serial]
            if accepted < 0:
                raise RuntimeError("native queue rejected a valid handle")
            return False
        return True

    def get(self):
        identity = threading.get_ident()
        if self.consumer is None:
            self.consumer = identity
        if identity != self.consumer:
            raise RuntimeError("SPSC consumer ownership violated")
        size = self.lib.aegis_queue_pop(self.pointer, self.buffer, 8)
        if size == 0:
            return None
        if size != 8:
            raise RuntimeError("native queue returned invalid handle")
        token = struct.unpack("=Q", self.buffer.raw)[0]
        return self.objects.pop(token)

    def close(self):
        if self.pointer:
            self.lib.aegis_queue_destroy(self.pointer)
            self.pointer = None
            self.objects.clear()
