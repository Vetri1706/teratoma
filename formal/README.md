# Finite VSR verification

The [unbounded Lean development](unbounded/README.md) and its [checked results](../docs/UNBOUNDED_VERIFICATION.md) extend the protocol safety work beyond the finite exploration described below.

Run `python3 -B formal/check_model.py` for the default two-view model, or
`python3 -B formal/check_model.py --max-view 2 --max-log 2` for three views.
Both use three replicas, two operation values, at most two log entries per
replica, and at most one recovering replica. The checker exhausts an exact
breadth-first frontier with no depth, state-count or time cutoff. Success
requires an empty frontier, every safety invariant to hold, zero deadlocks,
and zero unexpected protocol quiescent states.

The executed three-view model reached 1,938,640 states and 7,299,033 transitions.
TypeOK, Agreement, LeaderCompleteness and LogMatching held throughout; deadlocks
and unexpected quiescence were both zero. The mutation command
`python3 -B formal/check_model.py --mutation --expect-counterexample`
deliberately selects the stalest election report and detects a lost committed
entry. This negative control checks that a broken election can fail the verifier.

## Correspondence

`AegisDB_VSR.tla` specifies the same bounded transition system. Python bit masks
correspond to TLA+ acknowledgment sets; node tuples correspond to records; a
missing Python proposal corresponds to `installed[v] = FALSE`. The supplied
TLA+ configurations use the default bounds. The executable checker is a direct
state explorer, not a TLA+ interpreter. Java/TLC was unavailable locally; the
TLA+ module was source-reviewed but not parsed or executed by TLC.

Prepare and StartView delivery, cached acknowledgments, commit notices,
proposals, view-change initiation, crashes and recovery are nondeterministic.
Durable views cannot regress. DoViewChange atomically collects current quorum
reports; individual delayed report packets are abstracted. Recovery retains
durable logs and promised views, requires a quorum, and uses subsequent delivery
to catch up. Byzantine behavior and erased durable logs are outside this model.

## Meaning of exhaustive verification

The safety statements hold at every reachable state within the declared finite
model. The observer's commit history checks safety without guarding transitions.
No-change message deliveries are omitted; the TLA+ specification admits ordinary
stuttering. Crash edges change replica state. The checker separately detects
states with no non-crash action, distinguishing completed bounded workloads
from unexpected protocol quiescence.

This is a finite safety proof, not an unbounded inductive proof, an executable
refinement proof, or a storage/strict-serializability proof. No fairness-cycle
liveness analysis is performed. Real-process tests separately check the
implementation's recorded transaction histories and recovery behavior.
