import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = ".dylib" if sys.platform == "darwin" else ".so"
        cls.lib = ctypes.CDLL(str(ROOT / "bin" / ("libaegis_native" + suffix)))
        cls.lib.aegis_queue_create.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        cls.lib.aegis_queue_create.restype = ctypes.c_void_p
        cls.lib.aegis_queue_push.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.aegis_queue_push.restype = ctypes.c_int
        cls.lib.aegis_queue_pop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        cls.lib.aegis_queue_pop.restype = ctypes.c_int64
        cls.lib.aegis_queue_destroy.argtypes = [ctypes.c_void_p]
        cls.lib.aegis_queue_destroy.restype = None

    def queue(self, capacity=2, maximum=32):
        pointer = self.lib.aegis_queue_create(capacity, maximum)
        self.assertTrue(pointer)
        self.addCleanup(self.lib.aegis_queue_destroy, pointer)
        return pointer

    def push(self, pointer, data):
        buffer = ctypes.create_string_buffer(data, max(1, len(data)))
        return self.lib.aegis_queue_push(pointer, buffer, len(data))

    def pop(self, pointer, capacity=32):
        buffer = ctypes.create_string_buffer(max(1, capacity))
        length = self.lib.aegis_queue_pop(pointer, buffer, capacity)
        return length, buffer.raw[:length] if length > 0 else b""

    def test_capacity_wraparound_binary_data_and_empty(self):
        pointer = self.queue()
        for iteration in range(100):
            data = bytes([iteration, 0, 255])
            self.assertEqual(self.push(pointer, data), 1)
            self.assertEqual(self.push(pointer, b"second"), 1)
            self.assertEqual(self.push(pointer, b"full"), 0)
            self.assertEqual(self.pop(pointer), (3, data))
            self.assertEqual(self.pop(pointer), (6, b"second"))
            self.assertEqual(self.pop(pointer), (0, b""))

    def test_small_destination_preserves_message_and_rejects_invalid_input(self):
        pointer = self.queue(maximum=8)
        self.assertEqual(self.push(pointer, b"retained"), 1)
        self.assertEqual(self.pop(pointer, 2), (-1, b""))
        self.assertEqual(self.pop(pointer), (8, b"retained"))
        self.assertEqual(self.push(pointer, b""), -1)
        self.assertEqual(self.push(pointer, b"too large"), -1)
        self.assertEqual(self.lib.aegis_queue_push(pointer, None, 1), -1)
        self.assertEqual(self.lib.aegis_queue_pop(pointer, None, 8), -1)
        for capacity, maximum in ((0, 8), (2, 0), (ctypes.c_size_t(-1).value, 8), (2, ctypes.c_size_t(-1).value)):
            self.assertFalse(self.lib.aegis_queue_create(capacity, maximum))
        self.lib.aegis_queue_destroy(None)

    def test_eight_native_threads_four_channels_400000_messages(self):
        result = subprocess.run([str(ROOT / "bin" / "aegis_queue_stress"), "100000"],
                                capture_output=True, text=True, timeout=60, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report["channels"], 4)
        self.assertEqual(report["messages_per_channel"], 100000)
        self.assertEqual(report["verified_messages"], 400000)

    def test_embedded_executable_identity_and_cwd_independence(self):
        executable = ROOT / "bin" / "aegisdbd"
        with executable.open("rb") as stream:
            magic = stream.read(4)
        if sys.platform == "darwin":
            self.assertIn(magic, (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"))
        else:
            self.assertEqual(magic, b"\x7fELF")
        result = subprocess.run([str(executable), "--help"], cwd=tempfile.gettempdir(),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
