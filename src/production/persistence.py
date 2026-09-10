"""POSIX fsync WAL generations, immutable SSTables, and atomic CURRENT manifests."""
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from threading import RLock
from src.storage import LSMTree, WAL, SSTable
from src.storage.compaction import Compaction
from src.storage.codec import encode_key, encode_value
GENERATION = re.compile(r"gen-[0-9]{20}\Z")
TABLE_NAME = re.compile(r"L([012])-[0-9]{20}-[0-9]{8,20}\.sst\Z")


class CorruptStore(RuntimeError):
    """Disk contents cannot safely be treated as a crash-truncated suffix."""


class StoreLocked(RuntimeError):
    """Another process owns this data directory."""


@dataclass(frozen=True)
class FrozenStorage:
    metadata: dict
    files: dict


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json(raw):
    def reject(value):
        raise ValueError("non-finite JSON number")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=reject)


def _uint(value, field, minimum=0):
    if type(value) is not int or not minimum <= value < (1 << 64):
        raise CorruptStore(f"invalid {field}")
    return value


def freeze_storage(storage):
    files = storage.files()
    job = storage._job
    metadata = {"flush_every": storage.flush_every, "memtable_max_bytes": storage.memtable_max_bytes,
        "compaction_budget": storage.compaction_budget, "index": storage.index, "serial": storage._serial,
        "memtable": storage.memtable.entries(),
        "files": [{"name": name, "length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                  for name, raw in sorted(files.items())], "digest": storage.digest(),
        "pending": None if job is None else {"sources": [name for name, _ in job.sources],
            "level": job.level, "publication_index": job.publication_index, "output_name": job.output_name}}
    return FrozenStorage(_decode_json(_json_bytes(metadata)), dict(files))


def restore_storage(metadata, files):
    """Restore from actual table files and memtable metadata, without replaying history."""
    try:
        tree = LSMTree(flush_every=_uint(metadata["flush_every"], "flush interval", 1),
            memtable_max_bytes=_uint(metadata["memtable_max_bytes"], "memtable bound", 32),
            compaction_budget=_uint(metadata["compaction_budget"], "compaction budget", 1))
        tree.index = _uint(metadata["index"], "commit index")
        tree._serial = _uint(metadata["serial"], "file serial")
        descriptions = metadata["files"]
        names = [entry["name"] for entry in descriptions]
        if names != sorted(set(names)) or set(names) != set(files):
            raise CorruptStore("checkpoint table manifest is inconsistent")
        for description in descriptions:
            name = description["name"]
            match, raw = TABLE_NAME.fullmatch(name), files[name]
            if match is None or len(raw) != description["length"] or hashlib.sha256(raw).hexdigest() != description["sha256"]:
                raise CorruptStore("checkpoint SSTable checksum mismatch")
            table = SSTable.from_bytes(raw)
            if table.level != int(match.group(1)) or any(version > tree.index for _, version, _ in table.entries):
                raise CorruptStore("checkpoint SSTable metadata mismatch")
            tree._tables[name] = table
        previous = None
        for key, version, value in metadata["memtable"]:
            encode_key(key)
            encode_value(value)
            if previous is not None and key <= previous:
                raise CorruptStore("checkpoint memtable is not strictly ordered")
            previous = key
            version = _uint(version, "memtable version", 1)
            if version > tree.index:
                raise CorruptStore("checkpoint memtable exceeds commit index")
            tree.memtable.put(key, value, version)
        if tree.memtable.byte_size > tree.memtable_max_bytes or tree.digest() != metadata["digest"]:
            raise CorruptStore("checkpoint storage digest or memory bound mismatch")
        pending = metadata["pending"]
        if pending is not None:
            names, level = pending["sources"], pending["level"]
            publication = _uint(pending["publication_index"], "compaction publication index", 1)
            output = pending["output_name"]
            match = TABLE_NAME.fullmatch(output)
            if len(names) != len(set(names)) or not names or level not in (1, 2) or publication <= tree.index:
                raise CorruptStore("invalid checkpoint compaction")
            if match is None or int(match.group(1)) != level or output in files:
                raise CorruptStore("invalid checkpoint compaction output")
            sources = [(name, tree._tables[name]) for name in names]
            if not all(table.level == 0 if level == 1 else table.level in (1, 2) for _, table in sources):
                raise CorruptStore("invalid compaction source level")
            tree._job = Compaction(sources, level, publication, output)
            tree._job.advance(tree._job.input_count)
        return tree
    except CorruptStore:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise CorruptStore(f"invalid storage checkpoint: {error}") from error


class DurableStore:
    def __init__(self, data_dir, allow_quarantine=False):
        self.path = Path(data_dir).absolute()
        self.fsync_count = self.recovered_sstable_count = self.truncated_tail_bytes = 0
        self.maintenance_error = self.recovered_storage = None
        self._lock = RLock()
        self._lock_fd = self._wal_fd = None
        self._generation = self._manifest = None
        self._records = []
        self._base_sequence, self._next_sequence = 0, 1
        self._poisoned = self._closed = False
        self._quarantine_error = None
        missing, ancestor = [], self.path
        while not ancestor.exists():
            missing.append(ancestor)
            ancestor = ancestor.parent
        for directory in reversed(missing):
            directory.mkdir(exist_ok=True, mode=0o700)
            self._sync_directory(directory.parent)
        try:
            self._lock_fd = os.open(self.path / "LOCK", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise StoreLocked(f"data directory is already in use: {self.path}") from error
                raise
            if (self.path / "CORRUPT").exists():
                raise CorruptStore("store quarantined; verified quorum recovery required")
            if not (self.path / "CURRENT").exists():
                if any(GENERATION.fullmatch(entry.name) for entry in self.path.iterdir()):
                    self._quarantine("CURRENT is missing while WAL generations exist")
                self._publish_generation(None, None, 0)
            else:
                self._load_current()
            self._recover_locked()
        except CorruptStore as error:
            self._poisoned, self._quarantine_error = True, str(error)
            if not allow_quarantine:
                self.close()
                raise
        except BaseException:
            self.close()
            raise

    def _sync(self, fd):
        os.fsync(fd)
        self.fsync_count += 1

    def _sync_directory(self, path):
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            self._sync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _write_all(fd, raw):
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("write made no progress")
            view = view[count:]

    def _write_file(self, path, raw):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            self._write_all(fd, raw)
            self._sync(fd)
        finally:
            os.close(fd)

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("durable store is closed")
        if self._quarantine_error is not None:
            raise CorruptStore(self._quarantine_error)
        if self._poisoned:
            raise RuntimeError("durable store encountered an uncertain write; reopen to recover")
        if self._wal_fd is None:
            raise RuntimeError("durable store has no active WAL")

    def _quarantine(self, reason, source=None):
        self._poisoned, self._quarantine_error = True, reason
        try:
            backup = self.path / "quarantine-wal.bin"
            if source is not None and source.is_file() and not backup.exists():
                self._write_file(backup, source.read_bytes())
            marker = self.path / "CORRUPT"
            if not marker.exists():
                self._write_file(marker, _json_bytes({"reason": reason, "generation": self._generation}))
            self._sync_directory(self.path)
        except OSError as error:
            raise CorruptStore(f"{reason}; quarantine persistence failed: {error}") from error
        raise CorruptStore(reason)

    def _load_current(self):
        try:
            pointer = _decode_json((self.path / "CURRENT").read_bytes())
            name = pointer["generation"]
            if not isinstance(name, str) or GENERATION.fullmatch(name) is None:
                raise ValueError("invalid generation name")
            raw = (self.path / name / "manifest.json").read_bytes()
            if hashlib.sha256(raw).hexdigest() != pointer["sha256"]:
                raise ValueError("manifest checksum mismatch")
            manifest = _decode_json(raw)
            if manifest["format"] != 1 or manifest["generation"] != name:
                raise ValueError("manifest version or generation mismatch")
            base = _uint(manifest["base_sequence"], "WAL base sequence")
            checkpoint, storage_metadata = manifest["checkpoint"], manifest["storage"]
            if checkpoint is not None and not isinstance(checkpoint, dict):
                raise ValueError("checkpoint record is not an object")
            if (checkpoint is None) != (storage_metadata is None):
                raise ValueError("incomplete checkpoint metadata")
            files = {}
            if storage_metadata is not None:
                for description in storage_metadata["files"]:
                    table_name = description["name"]
                    match = TABLE_NAME.fullmatch(table_name)
                    if match is None:
                        raise ValueError("invalid SSTable filename")
                    files[table_name] = (self.path / name / "tables" / f"L{match.group(1)}" / table_name).read_bytes()
                self.recovered_storage = restore_storage(storage_metadata, files)
                self.recovered_sstable_count = len(files)
            self._generation, self._manifest = name, manifest
            self._base_sequence = base
            self._wal_fd = os.open(self.path / name / "wal.bin", os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
        except (OSError, KeyError, TypeError, ValueError, CorruptStore, RecursionError) as error:
            self._quarantine(f"cannot recover checkpoint: {error}")

    def _recover_locked(self):
        self._ensure_open()
        wal_path = self.path / self._generation / "wal.bin"
        raw = wal_path.read_bytes()
        blocks = [raw[offset:offset + WAL.BLOCK_SIZE] for offset in range(0, len(raw), WAL.BLOCK_SIZE)]
        result = WAL.scan(blocks)
        records = []
        for _, payload in result.records:
            try:
                record = _decode_json(payload)
                if not isinstance(record, dict):
                    raise ValueError("WAL record is not an object")
            except (UnicodeError, ValueError, TypeError, RecursionError) as error:
                self._quarantine(f"invalid WAL record encoding: {error}", wal_path)
            records.append(record)
        if result.error is not None:
            if result.error not in ("truncated WAL block", "incomplete WAL record"):
                self._quarantine(f"WAL corruption: {result.error}", wal_path)
            valid_size = result.valid_blocks * WAL.BLOCK_SIZE
            os.ftruncate(self._wal_fd, valid_size)
            self._sync(self._wal_fd)
            self.truncated_tail_bytes += len(raw) - valid_size
        checkpoint = self._manifest["checkpoint"]
        self._records = ([] if checkpoint is None else [checkpoint]) + records
        self._next_sequence = len(records) + 1
        os.lseek(self._wal_fd, 0, os.SEEK_END)
        return _decode_json(_json_bytes(self._records))

    def recover(self):
        with self._lock:
            return self._recover_locked()

    @property
    def last_sequence(self):
        return self._base_sequence + self._next_sequence - 1

    @property
    def stats(self):
        return {"fsync_count": self.fsync_count, "recovered_sstable_count": self.recovered_sstable_count,
                "truncated_tail_bytes": self.truncated_tail_bytes, "current_generation": self._generation,
                "last_sequence": self.last_sequence, "quarantined": self._quarantine_error is not None}

    def append(self, record):
        if not isinstance(record, dict):
            raise TypeError("durable WAL records must be dictionaries")
        payload = _json_bytes(record)
        frozen = _decode_json(payload)
        with self._lock:
            self._ensure_open()
            if self.last_sequence + 1 >= (1 << 64):
                raise OverflowError("WAL sequence exhausted")
            frames = WAL.encode_record(self._next_sequence, payload)
            try:
                for block in frames:
                    self._write_all(self._wal_fd, block)
                self._sync(self._wal_fd)
            except BaseException:
                self._poisoned = True
                raise
            self._records.append(frozen)
            sequence = self._base_sequence + self._next_sequence
            self._next_sequence += 1
            return sequence

    def _publish_generation(self, checkpoint, frozen, base_sequence):
        previous_generation = self._generation
        existing = [int(entry.name[4:]) for entry in self.path.iterdir() if GENERATION.fullmatch(entry.name)]
        name = f"gen-{max(existing, default=0) + 1:020d}"
        directory = self.path / name
        directory.mkdir(mode=0o700)
        tables_directory = directory / "tables"
        tables_directory.mkdir(mode=0o700)
        for level in range(3):
            (tables_directory / f"L{level}").mkdir(mode=0o700)
        restored = None
        if frozen is not None:
            restored = restore_storage(frozen.metadata, frozen.files)
            for table_name, raw in sorted(frozen.files.items()):
                match = TABLE_NAME.fullmatch(table_name)
                if match is None:
                    raise ValueError("invalid checkpoint table name")
                self._write_file(tables_directory / f"L{match.group(1)}" / table_name, raw)
        for level in range(3):
            self._sync_directory(tables_directory / f"L{level}")
        self._sync_directory(tables_directory)
        manifest = {"format": 1, "generation": name,
                    "base_sequence": _uint(base_sequence, "checkpoint base sequence"),
                    "checkpoint": checkpoint, "storage": None if frozen is None else frozen.metadata}
        raw_manifest = _json_bytes(manifest)
        self._write_file(directory / "manifest.json", raw_manifest)
        self._write_file(directory / "wal.bin", b"")
        self._sync_directory(directory)
        new_fd = os.open(directory / "wal.bin", os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
        temporary = self.path / f".CURRENT-{name}"
        try:
            pointer = _json_bytes({"generation": name, "sha256": hashlib.sha256(raw_manifest).hexdigest()})
            self._write_file(temporary, pointer)
            os.replace(temporary, self.path / "CURRENT")
            self._sync_directory(self.path)
        except BaseException:
            os.close(new_fd)
            raise
        old_fd = self._wal_fd
        self._wal_fd, self._generation, self._manifest = new_fd, name, manifest
        self._base_sequence, self._next_sequence = base_sequence, 1
        self._records = [] if checkpoint is None else [checkpoint]
        self.recovered_storage = restored
        if old_fd is not None:
            os.close(old_fd)
        keep = {name, previous_generation}
        try:
            for entry in self.path.iterdir():
                if GENERATION.fullmatch(entry.name) and entry.name not in keep and entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
            self._sync_directory(self.path)
        except OSError as error:
            self.maintenance_error = str(error)

    def save_checkpoint(self, install_record, storage, expected_sequence=None):
        if not isinstance(install_record, dict):
            raise TypeError("checkpoint install record must be a dictionary")
        checkpoint = _decode_json(_json_bytes(install_record))
        source = storage if isinstance(storage, FrozenStorage) else freeze_storage(storage)
        frozen = FrozenStorage(_decode_json(_json_bytes(source.metadata)), dict(source.files))
        with self._lock:
            self._ensure_open()
            if expected_sequence is not None and expected_sequence != self.last_sequence:
                raise ValueError("checkpoint does not cover latest durable WAL sequence")
            try:
                self._publish_generation(checkpoint, frozen, self.last_sequence)
            except BaseException:
                self._poisoned = True
                raise

    def replace_from_quorum(self, install_record, storage):
        if not isinstance(install_record, dict):
            raise TypeError("repair install record must be a dictionary")
        checkpoint = _decode_json(_json_bytes(install_record))
        source = storage if isinstance(storage, FrozenStorage) else freeze_storage(storage)
        frozen = FrozenStorage(_decode_json(_json_bytes(source.metadata)), dict(source.files))
        with self._lock:
            if self._closed or self._lock_fd is None:
                raise RuntimeError("durable store is closed")
            try:
                self._publish_generation(checkpoint, frozen, 0)
                marker = self.path / "CORRUPT"
                if marker.exists():
                    marker.unlink()
                    self._sync_directory(self.path)
            except BaseException:
                self._poisoned = True
                raise
            self._poisoned, self._quarantine_error = False, None
            return self.last_sequence

    def close(self):
        with self._lock:
            self._closed = True
            try:
                if self._wal_fd is not None:
                    os.close(self._wal_fd)
                    self._wal_fd = None
            finally:
                if self._lock_fd is not None:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    finally:
                        os.close(self._lock_fd)
                        self._lock_fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
