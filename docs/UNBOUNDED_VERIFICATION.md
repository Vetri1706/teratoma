# Historical unbounded protocol safety verification

This document preserves the original proof-build report. Its raw build log,
kernel audit, source-hash report, and proof archive are not included in this
repository; their original paths appear below as plain paths, not working links.
The source and pinned toolchain are available for a fresh verification run.
This document does not claim that the current revision has just passed Lean.
Consult [GitHub Actions](https://github.com/Vetri1706/teratoma/actions) and its exact
revision, or run the command below, for current evidence.

The original report states that Lean 4.19.0 successfully rebuilt all 10 project
modules from a clean build directory, with warnings treated as errors. It records
51 theorem declarations in 1222 lines of Lean, all checked with no open proof
obligations.

| Executed command | Exit status | Seconds |
| --- | ---: | ---: |
| lean-version | 0 | 1.526 |
| lake-clean | 0 | 0.264 |
| lake-build | 0 | 30.752 |
| kernel-audit | 0 | 0.516 |

The original dependency audit records twelve exported results, depending only on
Lean's standard `propext` and `Quot.sound` foundations. It reports no project
axioms, admitted proofs, compiler-trust proof shortcuts, or uses of
`Classical.choice` in those results. The verification runner also rejects
forbidden proof escape tokens.

## Statements in the proof development

The state machine is parameterized by `n : Nat`, an arbitrary fixed primary selection function `Nat → Fin n`, and an arbitrary command type. Quorums are strict majorities of `Fin n`, with intersection proved for every `n`. Views, logs, message histories, and execution lengths have no fixed upper bound.

- `initial_invariant`, `invariant_preserved`, and `reachable_invariant` establish the supporting invariant from initialization and every operational transition. Safety is not an election or commit precondition.
- `leaderCompleteness` derives preservation of a certified prefix in any later-view proposal using majority intersection, report fencing, and strong induction on views.
- `reachable_agreement` and `all_time_agreement` establish equality of applied commands at the same index, including observations at different times and different replicas.
- `past_commit_in_future_leader` establishes that an earlier committed prefix is present in the base selected by every later-view elected primary.
- `execution_promises_monotone`, `execution_accepted_views_monotone`, `execution_applied_indices_monotone`, and `execution_applied_prefix` establish monotone terms and applied indices and preservation of applied values.

`Execution` quantifies over an entire function `Nat → State`. The theorems hold at all natural-number times and across any two times in that execution; no finite state exploration or depth cutoff is used.

Two further checked theorems exhibit valid executions. One applies an arbitrary command. The other uses a two-of-three quorum, commits and applies an arbitrary command, changes views, and elects a different primary retaining the command. These exhibit satisfiable protocol guards without supplying invariant premises.

## Precise scope

This is an unbounded inductive safety proof of the [VSR transition system](../formal/unbounded/Aegis/Protocol.lean). Crash/restart preserves durable state. Message histories permit delayed and duplicate delivery. Structural full-log comparisons model log consistency and state transfer. The proof covers no disk corruption, erased durable state, byte parser, or OS syscall transition. It does not prove eventual progress under a fairness condition.

The raw length of an uncommitted log may decrease during legitimate reconciliation. The monotonic index property concerns applied progress and preserved committed prefixes. No per-entry Raft LogMatching theorem is claimed: this VSR model uses command lists and snapshot views, not per-entry Raft terms.

The executable Python/C daemon and its byte-level persistence operations have not been proved to refine these Lean transitions. Its real-socket, real-disk chaos results remain separate [implementation evidence](VERIFICATION.md). In particular, the proof does not assert collision-free SHA-256 over unbounded inputs. Thus the finite-model limitation has been removed for the protocol proof; an end-to-end executable correctness theorem has not been established.

## Reproduce and inspect

```sh
python3 -B scripts/verify_unbounded.py
```

Install Lean and Lake 4.19.0 first and put them on `PATH`, or pass
`--lean-home /path/to/lean-4.19.0`. No Lean binaries are bundled in this clone.
The version is pinned in the source. The original macOS arm64 toolchain archive
hash is recorded in [toolchain-origin.json](../formal/unbounded/toolchain-origin.json);
that hash does not describe installations for other platforms.

Included source:

- [Proof sources and model contract](../formal/unbounded/README.md)
- [Main safety theorems](../formal/unbounded/Aegis/Safety.lean)

Original evidence paths (not included in this clone):

| Evidence | Original path |
| --- | --- |
| Successful terminal transcript | `artifacts/unbounded/complete-terminal.log` |
| Clean build output | `artifacts/unbounded/lake-build.log` |
| Theorem statements and kernel dependency audit | `artifacts/unbounded/kernel-audit.log` |
| Exit statuses and source hashes | `artifacts/unbounded/report.json` |
| Proof/evidence archive | `artifacts/aegisdb-unbounded-proof.zip` |

A fresh `verify_unbounded.py` run writes its own logs, source hashes, theorem
dependencies, and exit statuses under `artifacts/unbounded/` by default. Preserve
those outputs with the tested commit when sharing a new verification claim.
