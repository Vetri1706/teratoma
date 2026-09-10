# Unbounded protocol safety verification

Lean 4.19.0 successfully rebuilt all 10 project modules from a clean build directory, with warnings treated as errors. The source contains 51 theorem declarations in 1222 lines of Lean. All declarations were checked; no proof obligations remain open.

| Executed command | Exit status | Seconds |
| --- | ---: | ---: |
| lean-version | 0 | 1.526 |
| lake-clean | 0 | 0.264 |
| lake-build | 0 | 30.752 |
| kernel-audit | 0 | 0.516 |

The dependency audit checked twelve exported results. Every audited result depends only on Lean's standard `propext` and `Quot.sound` foundations. There are no project axioms, admitted proofs, compiler-trust proof shortcuts, or uses of `Classical.choice` in these results. The source gate also rejects forbidden proof escape tokens.

## What is proved

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
python3 scripts/verify_unbounded.py
```

Use `--lean-home /path/to/lean-4.19.0` with another installation. The toolchain is pinned and its downloaded archive hash is recorded in [toolchain-origin.json](../formal/unbounded/toolchain-origin.json).

- [Complete successful terminal transcript](../artifacts/unbounded/complete-terminal.log)
- [Clean Lean build output](../artifacts/unbounded/lake-build.log)
- [Theorem statements and kernel dependency output](../artifacts/unbounded/kernel-audit.log)
- [Exit statuses and source hashes](../artifacts/unbounded/report.json)
- [Proof sources and model contract](../formal/unbounded/README.md)
- [Main safety theorems](../formal/unbounded/Aegis/Safety.lean)
- [Proof and evidence archive](../artifacts/aegisdb-unbounded-proof.zip)
