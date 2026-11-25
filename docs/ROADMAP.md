# Roadmap

The project's current purpose is to make replication and recovery behavior
reproducible and inspectable. These are contribution directions, not delivery
commitments or claims that the features already exist.

## Make experiments easier to reproduce

- Extend automated platform coverage with explicit native toolchain versions.
- Publish retained verification artifacts tied to a source commit, including
  failed attempts and unavailable sanitizer runs.
- Add small trace examples for more election/recovery edge cases, with a stated
  expected outcome and links to the responsible transitions.
- Improve native dependency diagnostics without silently skipping required checks.

A useful change in this area lets a new contributor reproduce one result from a
clean checkout and identify exactly what was tested.

## Strengthen implementation evidence

- Add focused regression cases for transaction identities, fragmented messages,
  checkpoint publication, and interrupted recovery.
- Expand real-process workload coverage across disk/filesystem and network fault
  cases while retaining independent result checks.
- Define repeatable performance experiments before making throughput or latency
  claims. Separate simulator ticks from native elapsed time.

## Explore larger design changes

History-prefix pruning, dynamic membership, authenticated/encrypted transport,
and a packaged client API are not implemented. Each needs a design discussion,
explicit compatibility requirements, and tests before it belongs in the runtime.
These changes would not by themselves establish production readiness.

## Connect proof and implementation

The finite checker and Lean development model protocol behavior separately from
the executable. A machine-checked refinement of byte parsing, durable writes,
and runtime transitions to the abstract model remains open. Liveness under a
stated fairness condition is also outside the current Lean safety proof.

Useful intermediate work includes a precise correspondence table, executable
cross-checks of matching traces, or narrower proof obligations. Describe the
property actually established instead of calling the whole engine verified.

See [CONTRIBUTING.md](../CONTRIBUTING.md) to propose or reproduce a focused change.
