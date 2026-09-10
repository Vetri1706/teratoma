# AegisDB

AegisDB runs five independent durable VSR daemons over real TCP. Each process owns
a filesystem data directory, a serialized consensus actor, four native network
threads, a disk worker, and C11 atomic queues. The deterministic simulator remains
an optional test target.

The implementation uses Python's standard library, a C11 compiler, POSIX sockets
and file APIs. There are no third-party Python packages. The compiled daemon embeds
CPython in its own PID; keep `bin/`, `src/`, and the matching Python runtime together.

## Build and verify

```sh
python3 -B scripts/build_native.py --sanitizers
python3 -B scripts/verify_production.py --output artifacts/checks-new
python3 -B scripts/run_os_chaos.py --requests 5000 --clients 16 --output artifacts/chaos-new
python3 -B formal/check_model.py --max-view 2 --max-log 2
python3 scripts/verify_unbounded.py
```

The chaos output directory must be new. The supervisor creates five processes,
five TCP fault proxies and sixteen concurrent clients. It injects actual
SIGSTOP/SIGCONT/SIGKILL, directed partitions, jitter, socket disconnections and
corrupt frames. It also forces primary replacement, damages a real SSTable,
requires quorum repair, kills all five processes, restarts them from disk, and
retries all original requests. Every process exit and response is retained.

See [verification results](docs/VERIFICATION.md), the
[complete terminal transcript](artifacts/complete-terminal.log), and the
[formal model contract](formal/README.md). The [unbounded Lean proof report](docs/UNBOUNDED_VERIFICATION.md) records a clean kernel-checked build and exact theorem dependencies.

## Run a daemon

The supplied [peer configuration](config/peers.json) uses localhost ports 8001–8005.
Launch the following once for each node ID 0–4, changing the port and data directory:

```sh
bin/aegisdbd --node 0 --bind 127.0.0.1 --port 8001 --peers config/peers.json --data-dir data/node_0 --bootstrap
```

Use `--bootstrap` only when creating the initial cluster on empty directories.
Restart with the same command and no `--bootstrap`. A node with empty or damaged
storage otherwise obtains fresh quorum recovery before participating. An exclusive
OS lock prevents two daemons from opening one directory. SIGTERM drains durable
work; abrupt process death uses the same startup recovery path.

## Transactions over TCP

```python
from src.production.framing import rpc

response = rpc(("127.0.0.1", 8001), 0, {
    "type": "client",
    "request": {
        "client": 0, "seq": 1,
        "reads": ["alice", "bob"],
        "expect": {"alice": None},
        "writes": {"alice": 100, "bob": 50}
    }
}, source=1000)
print(response)
```

A follower returns a redirect with the current primary and view. Retry the same
request identity and contents on that primary after a timeout or redirect.
Use source `1000 + client`, one outstanding request per client, and increasing
sequence numbers. Reads use the pre-write snapshot. Expectations either accept
all writes or abort them together. Reads and analytical queries enter the
replicated order and receive quorum-backed responses.

Add a query to a transaction:

```json
{"group_by": [], "aggregates": {"total": ["sum", "value"], "count": ["count", null]}, "filters": [["value", ">=", 0]]}
```

Supported aggregates are SUM, COUNT, MIN, MAX and AVG. The vectorized engine uses
typed column chunks, selection vectors and null bitmaps. Consensus application
and query evaluation remain serialized within a replica; network and durable I/O
run on separate OS threads.

Administrative status, audit and checkpoint RPCs use source 999. The daemon
defaults to localhost. Fixed membership and trusted peer routing are part of the
[operating contract](docs/FAULT_MODEL.md).

## Persistence and transport

WAL records use fixed-size checksummed blocks. Appends complete an OS fsync before
protocol acknowledgment. Checkpoints publish immutable L0/L1/L2 SSTables,
memtable and compaction metadata, and deduplication results through an atomically
replaced, checksummed CURRENT manifest. Startup loads actual SSTable files and
replays only the WAL tail. Incomplete tails are truncated; complete corrupt
records or tables quarantine the node until verified quorum replacement.

Frames have a binary length/routing/CRC32 header. Large messages use ordered
256 KiB fragments and a whole-message SHA-256. The worker sends memoryviews into
one immutable encoded payload, with separate small headers and bounded write
windows. C SPSC queue operations use lock-free acquire/release atomics; Python
object ownership uses the GIL. Queue saturation is measured and recovered through
protocol retries. Logical messages are limited to 64 MiB; new requests are
rejected before retained consensus history exceeds 48 MiB.

## Optional deterministic target

```sh
python3 -B -m src.main --seeds 1 2 3 4 5 --ticks 10000 --report verification.json
```

The simulator uses virtual time, network and disks. It is independent of the
production transport/storage adapters and supports reproducible fault traces.
