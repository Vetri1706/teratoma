import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from src.production.persistence import DurableStore, CorruptStore, freeze_storage, restore_storage
from src.storage import LSMTree, WAL


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "nested" / "database"

    def tearDown(self):
        self.temporary.cleanup()

    def run_child(self, source, *arguments):
        return subprocess.run([sys.executable, "-B", "-c", source, str(self.directory), *arguments],
                              capture_output=True, text=True, timeout=40)

    def wal_path(self):
        pointer = json.loads((self.directory / "CURRENT").read_text())
        return self.directory / pointer["generation"] / "wal.bin"

    def tree(self, count=10):
        tree = LSMTree(flush_every=2, compaction_budget=1)
        for index in range(1, count + 1):
            tree.apply(index, {f"k{index % 7}": index, "shared": index * 2})
        return tree

    def test_fsynced_multiblock_record_survives_sigkill(self):
        source = """
import os, signal, sys
from src.production.persistence import DurableStore
store = DurableStore(sys.argv[1])
sequence = store.append({'kind': 'append', 'payload': 'α' * 9000})
print(sequence, flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""
        child = self.run_child(source)
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
        self.assertEqual(child.stdout.strip(), "1")
        with DurableStore(self.directory) as store:
            self.assertEqual(store.recover(), [{"kind": "append", "payload": "α" * 9000}])
            before = store.fsync_count
            self.assertEqual(store.append({"kind": "commit", "commit": 1}), 2)
            self.assertGreater(store.fsync_count, before)

    def test_real_torn_tail_and_missing_fragments_recover(self):
        for length in (719, WAL.BLOCK_SIZE):
            directory = self.directory / str(length)
            source = """
import json, os, signal, sys
from pathlib import Path
from src.production.persistence import DurableStore
from src.storage import WAL
path = Path(sys.argv[1]) / sys.argv[2]
store = DurableStore(path)
store.append({'kind': 'view', 'view': 1})
pointer = json.loads((path / 'CURRENT').read_text())
fd = os.open(path / pointer['generation'] / 'wal.bin', os.O_WRONLY | os.O_APPEND)
raw = WAL.encode_record(2, b'x' * 10000)[0][:int(sys.argv[2])]
view = memoryview(raw)
while view:
    view = view[os.write(fd, view):]
os.fsync(fd)
os.kill(os.getpid(), signal.SIGKILL)
"""
            child = self.run_child(source, str(length))
            self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
            with DurableStore(directory) as store:
                self.assertEqual(store.recover(), [{"kind": "view", "view": 1}])
                self.assertEqual(store.truncated_tail_bytes, length)
                self.assertEqual(store.append({"kind": "view", "view": 2}), 2)

    def test_complete_record_corruption_is_quarantined(self):
        with DurableStore(self.directory) as store:
            store.append({"kind": "view", "view": 1})
            store.append({"kind": "commit", "commit": 1})
        wal = self.wal_path()
        raw = bytearray(wal.read_bytes())
        raw[100] ^= 1
        with wal.open("r+b") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with self.assertRaises(CorruptStore):
            DurableStore(self.directory)
        self.assertEqual(wal.read_bytes(), bytes(raw))
        self.assertEqual((self.directory / "quarantine-wal.bin").read_bytes(), bytes(raw))
        with DurableStore(self.directory, allow_quarantine=True) as store:
            with self.assertRaises(CorruptStore):
                store.append({"kind": "view", "view": 3})
            tree = self.tree()
            checkpoint = {"kind": "install", "commit": tree.index}
            store.replace_from_quorum(checkpoint, freeze_storage(tree))
            self.assertEqual(store.recover(), [checkpoint])
            self.assertEqual(store.recovered_storage.files(), tree.files())

    def test_checkpoint_files_pending_merge_and_tail_survive_sigkill(self):
        source = """
import os, signal, sys
from src.production.persistence import DurableStore, freeze_storage
from src.storage import LSMTree
store = DurableStore(sys.argv[1])
tree = LSMTree(flush_every=2, compaction_budget=1)
for index in range(1, 11):
    tree.apply(index, {f'k{index % 7}': index, 'shared': index * 2})
store.append({'kind': 'view', 'view': 1})
store.save_checkpoint({'kind': 'install', 'commit': 10}, freeze_storage(tree), expected_sequence=1)
store.append({'kind': 'view', 'view': 2})
os.kill(os.getpid(), signal.SIGKILL)
"""
        child = self.run_child(source)
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
        original = self.tree()
        with DurableStore(self.directory) as store:
            self.assertEqual(store.recover(), [{"kind": "install", "commit": 10}, {"kind": "view", "view": 2}])
            restored = store.recovered_storage
            self.assertGreater(store.recovered_sstable_count, 0)
            self.assertIsNotNone(restored._job)
            self.assertEqual(restored.files(), original.files())
            for index in range(11, 80):
                writes = {f"k{index % 7}": index}
                original.apply(index, writes)
                restored.apply(index, writes)
                self.assertEqual(restored.files(), original.files())
                self.assertEqual(restored.snapshot(), original.snapshot())

    def test_sigkill_during_real_checkpoint_publication(self):
        source = """
import sys
from pathlib import Path
from src.production.persistence import DurableStore
from src.storage import LSMTree
path = Path(sys.argv[1])
store = DurableStore(path)
store.append({'kind': 'view', 'view': 1})
tree = LSMTree(flush_every=1, memtable_max_bytes=131072)
for index in range(1, 193):
    tree.apply(index, {f'key-{index:04d}': 'x' * 60000})
(path / 'READY').write_text('ready')
sys.stdin.readline()
store.save_checkpoint({'kind': 'install', 'commit': 192}, tree, expected_sequence=1)
sys.stdin.readline()
"""
        child = subprocess.Popen([sys.executable, "-B", "-c", source, str(self.directory)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 35
            while not (self.directory / "READY").exists():
                self.assertIsNone(child.poll(), "checkpoint subprocess exited before READY")
                self.assertLess(time.monotonic(), deadline, "checkpoint subprocess did not become ready")
                time.sleep(0.005)
            child.stdin.write("start\n")
            child.stdin.flush()
            generation = self.directory / "gen-00000000000000000002"
            while not generation.exists():
                self.assertIsNone(child.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.001)
            child.kill()
            _, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, -signal.SIGKILL, stderr)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
        with DurableStore(self.directory) as store:
            records = store.recover()
            self.assertIn(records, [[{"kind": "view", "view": 1}], [{"kind": "install", "commit": 192}]])
            if store.recovered_storage is not None:
                self.assertEqual(store.recovered_storage.index, 192)
                self.assertEqual(len(store.recovered_storage.items()), 192)

    def test_checkpoint_guard_freezing_and_generation_retention(self):
        original = self.tree()
        frozen = freeze_storage(original)
        expected = original.snapshot()
        original.apply(11, {"later": 123})
        restored = restore_storage(frozen.metadata, frozen.files)
        self.assertEqual(restored.snapshot(), expected)
        with DurableStore(self.directory) as store:
            store.append({"kind": "view", "view": 1})
            with self.assertRaises(ValueError):
                store.save_checkpoint({"kind": "install"}, frozen, expected_sequence=0)
            for index in range(3):
                store.save_checkpoint({"kind": "install", "generation": index}, frozen, expected_sequence=1)
            self.assertEqual(len(list(self.directory.glob("gen-*"))), 2)
            self.assertEqual(store.recover(), [{"kind": "install", "generation": 2}])

    def test_process_lock_and_missing_current_fail_closed(self):
        with DurableStore(self.directory) as store:
            store.append({"kind": "view", "view": 1})
            source = """
import sys
from src.production.persistence import DurableStore, StoreLocked
try:
    DurableStore(sys.argv[1])
except StoreLocked:
    sys.exit(17)
sys.exit(1)
"""
            child = self.run_child(source)
            self.assertEqual(child.returncode, 17, child.stderr)
        (self.directory / "CURRENT").unlink()
        with self.assertRaises(CorruptStore):
            DurableStore(self.directory)


if __name__ == "__main__":
    unittest.main()
