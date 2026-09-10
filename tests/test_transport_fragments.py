import threading
import time
import unittest
from src.production.framing import (Decoder, FrameError, HEADER, FRAGMENT_HEADER,
    FRAGMENT_BYTES, FRAGMENT_MAGIC, MAX_MESSAGE, OutboundMessage, _physical_frame, frame, rpc)
from src.production.network import TCPNetwork
from src.production.runtime import RealEnvironment


class TransportFragmentTests(unittest.TestCase):
    def test_buffer_path_preserves_payload_ownership_and_wire_bytes(self):
        payload = {"blob": "q" * (FRAGMENT_BYTES + 10)}
        outbound = OutboundMessage(1, 2, payload)
        buffers = list(outbound.iter_buffers())
        views = [buffer for buffer in buffers if isinstance(buffer, memoryview)]
        self.assertGreater(len(views), 1)
        self.assertTrue(all(view.readonly and view.obj is outbound.body for view in views))
        self.assertEqual(sum(map(len, views)), len(outbound.body))
        self.assertEqual(sum(map(len, buffers)), outbound.wire_size)
        wire = b"".join(buffers)
        self.assertEqual(wire, b"".join(outbound.iter_frames()))
        self.assertEqual(Decoder().feed(wire), [(1, 2, payload)])

    def test_message_larger_than_legacy_frame_limit(self):
        payload = {"blob": "x" * (17 * 1024 * 1024)}
        outbound = OutboundMessage(1, 2, payload)
        decoder, result = Decoder(), []
        count = 0
        for raw in outbound.iter_frames():
            count += 1
            self.assertLessEqual(len(raw), HEADER.size + FRAGMENT_HEADER.size + FRAGMENT_BYTES)
            result.extend(decoder.feed(raw[:7]))
            result.extend(decoder.feed(raw[7:]))
        self.assertGreater(count, 1)
        self.assertEqual(result, [(1, 2, payload)])
        self.assertIsNone(decoder.assembly)
        self.assertEqual(decoder.feed(frame(1, 2, {"next": True})), [(1, 2, {"next": True})])

    def test_fragment_corruption_and_bounds_are_rejected(self):
        outbound = OutboundMessage(1, 2, {"blob": "x" * (FRAGMENT_BYTES + 1)})
        first = bytearray(next(outbound.iter_frames()))
        first[-1] ^= 1
        with self.assertRaisesRegex(FrameError, "CRC32"):
            Decoder().feed(first)
        for total, offset in ((MAX_MESSAGE + 1, 0), (2, 1)):
            body = FRAGMENT_HEADER.pack(1, total, offset, bytes(32)) + b"x"
            with self.assertRaises(FrameError):
                Decoder().feed(_physical_frame(FRAGMENT_MAGIC, 1, 2, body))

    def test_real_worker_fragmented_reply_and_credit_drain(self):
        env = RealEnvironment(1)
        network = TCPNetwork(0, ("127.0.0.1", 0), {0: ("127.0.0.1", 1)}, env, workers=1)
        blob = "z" * (17 * 1024 * 1024)
        network.register(0, lambda source, payload: network.send(0, source, {"blob": blob}))
        result, errors = [], []
        address = network.listener.getsockname()

        def request():
            try:
                result.append(rpc(address, 0, {"type": "echo"}, source=1000, timeout=10))
            except Exception as error:
                errors.append(error)

        client = threading.Thread(target=request)
        try:
            client.start()
            deadline = time.monotonic() + 12
            while (client.is_alive() or any(network.outstanding_bytes)) and time.monotonic() < deadline:
                network.poll()
                env.poll()
                time.sleep(0.001)
            client.join(timeout=1)
            self.assertFalse(client.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(result, [{"blob": blob}])
            self.assertEqual(network.outstanding_bytes, [0])
            self.assertEqual(network.stats()["dropped"], 0)
        finally:
            network.close()
            client.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
