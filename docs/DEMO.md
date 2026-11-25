# Replay a failure, then inspect the evidence

The [online replay lab](https://vetri1706.github.io/teratoma/) makes AegisDB's deterministic simulator visible.
It shows recorded observations from the Python engine, not an independent
JavaScript imitation of the protocol. No account, database server, compiler, or
third-party Python package is needed.

## Run locally

Use Python 3.11 or later on Windows, macOS, or Linux. From a checkout:

```sh
git clone https://github.com/Vetri1706/teratoma.git
cd teratoma
python -m src.demo
```

On a system where Python 3 is named `python3`, use that command instead.
Open `artifacts/demo/index.html` in your browser. The report works directly from
disk; it does not need an HTTP server or network access. The command prints the
absolute path and one PASS line per completed scenario. An invariant violation
raises an error; the generator does not label an incomplete run as passing.

To choose a seed and one experiment:

```sh
python -m src.demo --seed 19 --scenario primary-crash --output artifacts/my-run
```

`--output` is a **directory**. Existing report files in that directory are replaced.
Use a fresh directory to preserve previous experiments. `traces.json` contains the
observations, while `traces.js` packages the same data for a browser without a server.

## Four controlled experiments

Each experiment starts two clients and five replicas, using a quorum of three.
The seed controls request values, event ordering, and virtual device latency.
Background random faults are disabled so the selected fault is easy to follow.

| Scenario | At virtual tick 300 | What to inspect |
| --- | --- | --- |
| `healthy` | Continue without injecting a fault | Commit indices advance and replicas agree. |
| `primary-crash` | Stop the current primary | The surviving replicas change views and complete requests before the stopped node returns. |
| `partition` | Isolate the primary and a second node from the other three | The majority can progress; the pair cannot form a quorum. |
| `corruption` | Stop node 4, flip a checked WAL header bit, reboot at tick 320 | The replica takes the existing quorum recovery path. |

At tick 1000 the network heals and stopped replicas reboot. At tick 1400 the
clients stop generating new requests. At tick 2000 the generator requires no
pending requests and five normal, live replicas with equal commit indices and
storage digests. It also requires client progress during the fault window.

The browser samples state every **20 virtual ticks**. The independent invariant
checker still runs at **every virtual tick**: 2001 checks per complete recording.
Some short transitions can occur between displayed frames. The result card
describes the **full recording**, while the other counters describe the selected
frame. A replica's primary designation belongs to its local view; during a
partition different views can temporarily name different primaries.

Use Play/Pause, the timeline, or the milestone buttons. The replica table shows
exact view, commit index, log size, recovery count, and storage digest.
All controls work with the keyboard. Playback is opt-in and pauses when its tab
is hidden.

**Copy replay link** captures the selected scenario and virtual tick. For example,
[this partition at tick 980](https://vetri1706.github.io/teratoma/#scenario=partition&tick=980)
shows the majority making progress while two replicas remain isolated. Opening
the link restores that frame. A link to localhost works only on that computer;
for reports opened directly from disk, share the generated report folder instead.

## What this verifies

The existing `InvariantChecker` independently interprets transaction results,
checks a witnessed serial order against real-time constraints, compares replica
storage, and checks persistence of acknowledged operations in the virtual disks.
The demo adds end-of-run recovery and convergence assertions.

These are finite, reproducible tests. Virtual ticks are **not milliseconds**;
completion counts are **not throughput benchmarks**. A replay is not evidence of
real-process crash safety, an implementation proof, or production readiness.
See the [fault model](FAULT_MODEL.md), [verification scope](VERIFICATION.md), and
[separate Lean model](../formal/unbounded/README.md) for those boundaries.

## Reproduce the published data

```sh
python -m src.demo --seed 7 --output docs/lab
python -m unittest tests.test_demo -v
```

The tests compare every committed frame against a fresh engine execution. They
also check real primary loss, a bidirectional 2/3 partition, quorum recovery after
WAL corruption, deterministic replay, and generation into a standalone directory.

There is no browser telemetry, external font, CDN, or server-side execution in the
lab. Outbound links lead to this repository's source and documentation.
