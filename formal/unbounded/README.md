# Unbounded VSR safety development

This Lean 4 project models viewstamped replication using an arbitrary replica
count `n : Nat`, a fixed primary-selection function `Nat → Fin n`, arbitrary
command values, natural-number views, and lists of unrestricted length. There
is no enumeration bound on replicas, views, commands, log length, message
history, crashes, or execution length. Infinite executions are functions from
`Nat` to states, with the transition relation required at every adjacent pair.

`Aegis/Protocol.lean` defines operational transitions for view-change initiation,
report transmission, election, proposal, acceptance, acknowledgement, commit,
application, crash, and recovery. Election selects a quorum report with the
largest last-normal view, breaking ties by longest log. A commit requires a
majority of acknowledgement records. Neither transition requires an agreement
or leader-completeness predicate as a guard.

`Aegis/Quorum.lean` proves majority intersection for arbitrary replica counts.
`Aegis/LocalInvariants.lean` derives the auxiliary message-history and durable
state invariants from initialization and preserves them under every transition.
`Aegis/ViewChange.lean` uses quorum intersection and induction on views to derive
preservation of certified prefixes. `Aegis/Safety.lean` lifts these results to
reachable states and infinite executions.

The safety argument accounts for delayed and duplicate messages by retaining
histories and permitting a transition to select any eligible historical record.
Messages can remain undelivered indefinitely. Reports fence later
acknowledgements through durable promised views. Crashes retain accepted logs,
promised views, and applied state; recovery makes the replica available again.

## Running the checker

Use Lean 4.19.0 and run from the repository root:

```sh
python3 scripts/verify_unbounded.py
```

Install Lean and Lake 4.19.0 and put them on `PATH`, or select an installation with
`--lean-home /path/to/lean-4.19.0`. No Lean binaries are bundled in this clone.
The runner invokes `lake build`, prints theorem
types and kernel dependencies, and records command output, exit statuses, and
source SHA-256 hashes in `artifacts/unbounded/`. It rejects proof escapes and
custom axioms. The dependency report explicitly lists any use of Lean's standard
logical foundations (`propext`, `Quot.sound`). These are not
protocol assumptions or externally asserted consensus lemmas.

## Interpreting the statements

Agreement concerns values actually applied at the same index. Leader
completeness concerns a previously committed prefix in a later elected primary's
selected base. Promised views and applied indices are monotone. Speculative log
length is not required to be monotone: replacing an uncommitted suffix during
view change is part of the protocol.

The replica count is arbitrary and finite, as required for majority quorums;
there is no fixed upper bound on it. The execution can contain infinitely many
operations; each state contains a finite prefix. Safety does not require a
fairness assumption, reliable delivery, a live quorum, or eventual recovery.
These are conditions for progress, which is a separate property.

## Connection to the daemon

The Lean transition system is the subject of these proofs. Its acceptance and
state-transfer action installs a structural log atomically; its crash action
retains already-durable state. The daemon realizes those operations through
Python/C code, CRC-protected messages, hash chains, WAL syncs, and SSTable files.
The real-process chaos evidence checks that implementation separately. A checked
refinement from the daemon's byte-level operations to these Lean transitions is
not established by compiling this project. Cryptographic hash equality is not
used as an axiom equating arbitrary unbounded logs in the proof.
