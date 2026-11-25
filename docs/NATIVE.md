# Native process and storage experiments

The AegisDB native adapter runs separate daemons over TCP, with filesystem-backed
storage. It is an experimental POSIX implementation for isolated tests. It has
no peer authentication, administrative authentication, or TLS. Keep the default
loopback binding and use disposable data. See [the operating contract](FAULT_MODEL.md).

## Prerequisites

- A Linux or macOS host. The historical report used macOS arm64; inspect current CI for
  Linux coverage. Native Windows is not supported by these scripts.
- CPython 3.11+ with matching `Python.h` and embedding library. An interpreter
  alone may not include the development headers/library on Linux.
- A C11 compiler with pthreads. The build defaults to `clang`; set `CC` to select
  another compatible compiler.
- The `file` command. macOS builds also use `otool`.
- Sanitizer runtimes only if you request `--sanitizers`.

Run commands from the repository root. There are no third-party Python package
dependencies. The executable embeds the interpreter used at build time; keep
`bin/`, `src/`, and that matching Python runtime together.

## Build and check

```sh
python3 -B scripts/build_native.py
python3 -B scripts/verify_production.py --output artifacts/checks-local
```

The build produces `bin/aegisdbd`, the native queue library, and the queue stress
executable. It rewrites `native/build_report.json` with local commands and
results. That file currently contains a historical report; do not commit an
accidental replacement when submitting an unrelated change.

The verification runner executes the complete Python test suite, the bounded
model, and its mutation control. It preserves command output and exit statuses
under the chosen output directory. It does not run the real-process chaos
workload or Lean proof.

Optional checks:

```sh
python3 -B scripts/build_native.py --sanitizers
python3 -B formal/check_model.py --max-view 2 --max-log 2
python3 -B scripts/run_os_chaos.py --requests 5000 --clients 16 --output artifacts/chaos-local
```

Use a **new** chaos output directory. The supervisor starts five daemons and five
fault proxies, drives concurrent clients, injects OS/process/network faults,
checks damaged-file repair, and retries requests after complete-cluster restart.
Its default ports start at 18001 for daemons and 19001 for proxies; use
`--base-port` and `--proxy-port` if those ranges are occupied. Inspect
`--help` for the current workload controls.

Inspect each sanitizer's status in the build report. An unavailable compiler or
runtime can be recorded as unavailable without failing the whole build; a zero
build exit is not evidence that every requested sanitizer ran.

## Start a local cluster

The [supplied peers](../config/peers.json) use localhost ports 8001–8005. In five
separate terminals, run the following commands:

```sh
bin/aegisdbd --node 0 --bind 127.0.0.1 --port 8001 --peers config/peers.json --data-dir data/node_0 --bootstrap
bin/aegisdbd --node 1 --bind 127.0.0.1 --port 8002 --peers config/peers.json --data-dir data/node_1 --bootstrap
bin/aegisdbd --node 2 --bind 127.0.0.1 --port 8003 --peers config/peers.json --data-dir data/node_2 --bootstrap
bin/aegisdbd --node 3 --bind 127.0.0.1 --port 8004 --peers config/peers.json --data-dir data/node_3 --bootstrap
bin/aegisdbd --node 4 --bind 127.0.0.1 --port 8005 --peers config/peers.json --data-dir data/node_4 --bootstrap
```

Use `--bootstrap` only for the initial cluster with empty directories. Restart
with the same node/port/data directory and omit `--bootstrap`. An empty or
damaged store otherwise needs an available healthy quorum to recover. An OS
lock prevents two daemons from opening the same directory.

Ctrl+C or SIGTERM requests a graceful shutdown. Track the processes you started;
stop only those processes. Do not remove a data directory while its daemon runs.

## Send a transaction

Run this Python example from the repository root after the cluster is ready:

```python
from src.production.framing import rpc

request = {
    "client": 0,
    "seq": 1,
    "reads": ["alice", "bob"],
    "expect": {"alice": None},
    "writes": {"alice": 100, "bob": 50},
}
response = rpc(("127.0.0.1", 8001), 0,
               {"type": "client", "request": request}, source=1000)
print(response)
```

A follower can return a redirect with the current primary and view. After a
redirect or timeout, retry the **same identity and contents** on the indicated
primary. Use source `1000 + client`, one outstanding request per client, and
increasing sequence numbers for new requests. This low-level example does not
implement a retry loop.

Reads use the pre-write snapshot. Expectations accept all writes or abort them
together. Reads and analytical queries enter the replicated order. For example,
add this `query` field to a new transaction:

```json
{"group_by": [], "aggregates": {"total": ["sum", "value"], "count": ["count", null]}, "filters": [["value", ">=", 0]]}
```

Administrative status, audit, and checkpoint RPCs use source 999. Those routing
IDs do not provide authentication.

## Persistence and resource limits

WAL appends complete an OS fsync before protocol acknowledgement. Checkpoints
publish immutable SSTables and deduplication results through a checksummed
CURRENT manifest. Startup loads the SSTables and replays the WAL tail. Complete
corruption quarantines a store until quorum recovery supplies replacement data.
These mechanisms rely on the filesystem/device honoring fsync and atomic rename.

Logical messages are limited to 64 MiB. New requests are rejected before retained
consensus history exceeds 48 MiB. That admission limit does not bound total
memory use: retained client results and serialized checkpoints can grow much
larger than the log, especially for repeated reads of large values. History and
deduplication-result pruning are not implemented. Do not infer a scalability or
bounded-memory guarantee from the log limit.

Dynamic membership is not implemented. Query execution and consensus application
remain serialized within a replica. C SPSC queue operations are lock-free; the
full runtime is not.
