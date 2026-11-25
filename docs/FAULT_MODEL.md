# Operating and verification contract

The experimental native adapter under `src/production/` uses real TCP sockets,
OS threads, and filesystem storage. Its directory name does not certify
production readiness. The virtual OS supports deterministic tests and replay.

## Runtime assumptions

- Membership is fixed and odd, with at least three replicas. The default five-node
  cluster requires three distinct durable acknowledgments.
- Replicas are trusted crash-fault participants. Routing IDs and administrative
  RPCs are not authenticated; CRC32 and SHA-256 detect accidental corruption.
  Default listeners bind to localhost.
- The filesystem and device must honor successful fsync and atomic rename.
  Tests exercise SIGKILL and damaged files, not physical power removal.
- Quorum progress requires a sufficiently long interval of successful disk I/O,
  delivered messages and OS scheduling. Connectivity alone cannot promise
  immediate progress during unbounded delay or repeated crashes.
- Repair requires an available healthy quorum. Destroying every durable copy is
  outside the durability contract.
- Clients retry identical identities and contents, with one outstanding request
  per identity. Logged reads and transactions provide one serial order.

## Durable transitions and checkpoints

A network worker decodes complete integrity-checked messages and publishes them
through a native SPSC queue. The actor serializes protocol transitions and holds
its inbox while an OS disk worker persists the corresponding WAL record.
The disk worker fsyncs before reporting completion. The actor then applies that
record and emits the prepare acknowledgment or client commit response.

Every checkpoint freezes the actor's committed storage image and records the
exact durable WAL sequence it covers. The disk worker writes immutable SSTables,
checksummed manifest and a new WAL generation, fsyncs files and directories, then
atomically replaces CURRENT and fsyncs its parent. The previous generation is
retained through publication. Every newly created ancestor directory is synced
in its parent before the store accepts writes.

A short final WAL block or incomplete fragmented record is a crash tail and can
be truncated. Checksum failure in a complete block, missing CURRENT, damaged
manifest or corrupt SSTable quarantines the store. Quorum recovery publishes a
new complete generation before clearing quarantine or becoming eligible to vote.
The supervisor's corruption test never supplies replacement data itself.

Commit-based file naming, sorted encodings, flush triggers and compaction
publication deadlines preserve equal physical SSTable bytes at equal commit
indices. Recovery restores existing tables and pending compaction metadata
directly; it replays only post-checkpoint WAL records.

## Resource and execution bounds

Logical transport messages are bounded at 64 MiB; physical continuation payloads
are 256 KiB. Per-worker unsent data is bounded at 256 MiB with a 1 MiB write
window. Protocol retransmission handles dropped sends. The retained consensus
log has a 48 MiB admission bound on log entries. That limit does not include
retained client results or serialized checkpoints, which can grow much larger
than the log, especially with repeated reads of large values. It provides no
total-memory bound or guarantee that complete state-transfer payloads remain
representable. History and deduplication-result pruning are not implemented;
neither is dynamic membership. These are experiments, not a scalability claim.

The C queue's push/pop operations are lock-free; Python allocation, reference
ownership, serialization and consensus are not claimed to be lock-free.
The compiled daemon embeds the installed CPython runtime and loads this
repository's Python modules. Payload framing uses memoryviews; ordinary socket
writes still use kernel buffers. Analytical query application is serialized
inside the consensus actor.

## Evidence and formal scope

The OS supervisor independently interprets every completed transaction and
query, verifies its returned index/hash/result, and checks real-time ordering
from invocation/completion intervals. It compares all five full committed logs,
logical states and SSTable digests, both before and after recovery. Replaying
all client identities after simultaneous crashes checks persisted result
deduplication without new log entries. These are checks of the recorded history,
not observations at every OS scheduler step.

The standalone formal model and exhaustive checker establish Agreement,
LeaderCompleteness and LogMatching for all reachable states of the specified
finite transition system. The [formal documentation](../formal/README.md)
defines bounds and abstraction assumptions. The checker is not an unbounded
inductive proof or a refinement proof of the C/Python executable, filesystem,
network stack or transaction implementation.
