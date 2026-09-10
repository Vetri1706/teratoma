# Native runtime verification

The subsequent [Lean unbounded safety proof](UNBOUNDED_VERIFICATION.md) is verified separately. The finite-model results below remain the original executed evidence.

Executed on 7 September 2026 on macOS arm64 with CPython 3.14.6 and Apple Clang 21.

**61 tests passed. The five-process, 5,000-request chaos test passed in
151.961 seconds. The exhaustive three-view formal model passed.**

| Verification | Executed result |
| --- | --- |
| Native daemon and queue library | C11 compilation, warnings treated as errors |
| Native queue stress | 400,000 messages across 8 pthreads and 4 queues |
| Address/undefined-behavior sanitizer | 400,000 messages; exit 0 |
| ThreadSanitizer | 400,000 messages; exit 0 |
| Unit and integration regressions | 61 passed; exit 0 |
| Live TCP workload | 5,000 unique completed requests, 16 clients |
| Persisted-result replay | 5,000 retries, unchanged commit 5,000 |
| Formal three-view closure | 1,938,640 states; 7,299,033 transitions |
| Formal safety/deadlocks | Zero violations, zero deadlocks, empty frontier |
| Negative formal control | Stale-election mutation detected LeaderCompleteness violation |

## Actual process, network and disk faults

- 40 SIGSTOP and 40 SIGCONT signals.
- 46 deliberate SIGKILL exits, including simultaneous five-node crash recovery.
- 39 directed partition episodes, including half-duplex partitions.
- 213 forced socket disconnections, 51,985 jittered frames,
  4,810 partition-dropped frames and 51 corrupted frames.
- The initial primary was stopped for six seconds. View 1 served new client
  requests before it resumed. All five replicas ultimately converged on view 7.
- One real SSTable was modified and fsynced while its daemon was dead. Startup
  quarantined the corrupt store; TCP quorum transfer restored commit 5,000.
- All five processes were then killed and restarted. Each loaded five immutable
  SSTables from its own filesystem directory. All 5,000 original requests returned
  their prior results without appending duplicate log entries.

All 51 daemon exits matched expectations:
**46 exits with status -9 (intentional SIGKILL), 5 final exits with status 0.**
The supervisor returned 0. No unexpected daemon exit occurred in the final run.

## Consistency checks

The independent scalar interpreter reproduced every transaction's pre-write reads,
conditional-write decision and analytical result. Returned hashes and log indices
matched the unique invocation. An interval sweep checked real-time ordering of
non-overlapping calls. All replicas had identical full committed logs, logical
state and SSTable digests before repair, after quorum repair and after complete
process restart. A separate disk inspection recomputed hashes from the final
physical SSTable files and checked every manifest entry.

Final SSTable digest: 1dc0e7e84ccbd2ccb578db06178a5b123d90f1fc56af72bb6a0ef73ce604397f

Final committed-log digest: 153029036a2828c3dcdec9e5fd74a7a8727436a5548f70018fadc8881dd8b14a

## Formal result and scope

The executable breadth-first checker explored every reachable state for three
replicas, two operation values, two log slots and three views (0, 1, 2), with at
most one recovering replica. TypeOK, Agreement, LeaderCompleteness and LogMatching
held. It found zero unexpected protocol quiescent states in addition to zero
deadlocks. The three-view run took 77.755 seconds.

This is exhaustive verification of the declared finite model. It is not an
unbounded proof or an implementation refinement proof. The TLA+ file expresses
the corresponding model; the executed checker is the standalone Python state
explorer. Java/TLC was not available, so no TLC execution is claimed.
See [formal assumptions and correspondence](../formal/README.md).

## Complete evidence

- [Complete terminal log, including all daemon outputs](../artifacts/complete-terminal.log)
- [Every PID and process exit status](../artifacts/process-exit-statuses.json)
- [Chaos report](../artifacts/os-chaos-final/report.json)
- [All 5,000 invocation/response records](../artifacts/os-chaos-final/client-history.jsonl)
- [Three-view formal output](../artifacts/checks-final/model-3views.json)
- [Negative-control counterexample](../artifacts/checks-final/model-mutation.json)
- [All test results](../artifacts/checks-final/unit-tests.log)
- [Native compilation and sanitizer outputs](../native/build_report.json)
- [Actual disk files and checksums](../artifacts/disk-evidence.json)
- [Source and binary fingerprints](../artifacts/source-manifest.json)
- [Portable evidence archive](../artifacts/aegisdb-verification.zip)

An earlier diagnostic run discovered SIGPIPE termination when peers disconnected.
Signal handling was fixed. A later exploratory run was intentionally interrupted
to replace excessive small-packet snapshot traffic with paced streams. Their
original logs remain in artifacts/os-chaos-debug-1 and artifacts/os-chaos-5000-1;
they are not counted as passing verification.

The production runtime uses actual sockets, OS threads, fsync WAL and separately
persisted SSTables. Its resource and trust assumptions are documented in
[the operating contract](FAULT_MODEL.md). The optional deterministic target's
older results remain in verification.json.
