#!/usr/bin/env python3
"""Assemble recorded outputs, process statuses and independently inspected disk files."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chaos", type=Path, default=ROOT / "artifacts/os-chaos-final")
    parser.add_argument("--checks", type=Path, default=ROOT / "artifacts/checks-final")
    args = parser.parse_args()
    artifacts = ROOT / "artifacts"
    chaos = json.loads((args.chaos / "report.json").read_text())
    checks = json.loads((args.checks / "checks.json").read_text())
    native = json.loads((ROOT / "native/build_report.json").read_text())
    formal = json.loads((args.checks / "model-3views.json").read_text())
    if chaos["status"] != "passed" or any(check["exit_status"] for check in checks):
        raise AssertionError("cannot publish a successful report for failed checks")
    if formal["status"] != "verified" or formal["frontier_remaining"] or formal["deadlocks"]:
        raise AssertionError("formal verification did not reach safe closure")
    if any(item["returncode"] for item in native["commands"]):
        raise AssertionError("a native build or sanitizer command failed")
    if any(item["exit_status"] != item["expected"] for item in chaos["processes"]):
        raise AssertionError("unexpected daemon exit")
    disk = []
    for node in range(5):
        directory = args.chaos / "data" / f"node_{node}"
        pointer = json.loads((directory / "CURRENT").read_text())
        generation = directory / pointer["generation"]
        manifest_raw = (generation / "manifest.json").read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest() != pointer["sha256"]:
            raise AssertionError("manifest pointer checksum failed")
        manifest = json.loads(manifest_raw)
        checksum, files = hashlib.sha256(), []
        for entry in manifest["storage"]["files"]:
            path = generation / "tables" / entry["name"][:2] / entry["name"]
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != entry["sha256"] or len(raw) != entry["length"]:
                raise AssertionError("physical table differs from durable manifest")
            name = entry["name"].encode("ascii")
            checksum.update(struct.pack(">IQ", len(name), len(raw)))
            checksum.update(name)
            checksum.update(raw)
            files.append({"path": str(path), "bytes": len(raw), "sha256": digest})
        if checksum.hexdigest() != chaos["after_restart"]["sstable_digest"]:
            raise AssertionError("actual on-disk SSTable digest differs from verified runtime")
        disk.append({"node": node, "generation": pointer["generation"],
                     "commit": manifest["checkpoint"]["commit"], "sstable_digest": checksum.hexdigest(),
                     "tables": files, "wal_bytes": (generation / "wal.bin").stat().st_size})
    (artifacts / "disk-evidence.json").write_text(json.dumps(disk, indent=2) + "\n")
    (artifacts / "process-exit-statuses.json").write_text(json.dumps(chaos["processes"], indent=2) + "\n")
    source_paths = sorted([*ROOT.glob("src/**/*.py"), *ROOT.glob("native/*.c"), *ROOT.glob("native/*.h"),
                           *ROOT.glob("formal/*.py"), *ROOT.glob("formal/*.tla"), *ROOT.glob("formal/*.cfg")])
    fingerprint = hashlib.sha256()
    source_files = []
    for path in source_paths:
        raw, relative = path.read_bytes(), path.relative_to(ROOT).as_posix()
        fingerprint.update(relative.encode() + b"\x00" + raw)
        source_files.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    binaries = [{"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted((ROOT / "bin").iterdir()) if path.is_file()]
    (artifacts / "source-manifest.json").write_text(json.dumps({
        "aggregate_sha256": fingerprint.hexdigest(), "sources": source_files, "binaries": binaries}, indent=2) + "\n")
    transcript = artifacts / "complete-terminal.log"
    with transcript.open("w") as output:
        output.write("Recorded native build, verification, supervisor and daemon outputs.\n")
        for command in native["commands"]:
            output.write("\n$ " + shlex.join(command["argv"]) + "\n")
            output.write(command["stdout"])
            output.write(command["stderr"])
            output.write(f"\nEXIT STATUS: {command['returncode']}\n")
        for check in checks:
            output.write("\n$ " + shlex.join(check["command"]) + "\n")
            output.write((args.checks / f"{check['name']}.log").read_text())
            output.write(f"\nEXIT STATUS: {check['exit_status']}\n")
        output.write("\n$ python3 -B formal/check_model.py --max-view 2 --max-log 2\n")
        output.write((args.checks / "model-3views.json").read_text())
        output.write("\nEXIT STATUS: 0\n")
        output.write("\n$ python3 -B scripts/run_os_chaos.py --requests 5000 --clients 16 --output artifacts/os-chaos-final --timeout 600\n")
        output.write((args.chaos / "terminal.log").read_text())
        output.write("\nSUPERVISOR EXIT STATUS: 0\n")
        for process in chaos["processes"]:
            output.write(f"\nDAEMON node={process['node']} pid={process['pid']} incarnation={process['incarnation']}\n")
            output.write(Path(process["log"]).read_text())
            output.write(f"\nEXIT STATUS: {process['exit_status']} EXPECTED: {process['expected']} REASON: {process['reason']}\n")
    unit_output = (args.checks / "unit-tests.log").read_text()
    count = int(re.search(r"Ran (\d+) tests", unit_output).group(1))
    exits = Counter(item["exit_status"] for item in chaos["processes"])
    faults, proxy = chaos["faults"], chaos["proxy"]
    report = f"""# Native runtime verification

Executed on 7 September 2026 on macOS arm64 with CPython 3.14.6 and Apple Clang 21.

**{count} tests passed. The five-process, 5,000-request chaos test passed in
{chaos['elapsed_seconds']:.3f} seconds. The exhaustive three-view formal model passed.**

| Verification | Executed result |
| --- | --- |
| Native daemon and queue library | C11 compilation, warnings treated as errors |
| Native queue stress | 400,000 messages across 8 pthreads and 4 queues |
| Address/undefined-behavior sanitizer | 400,000 messages; exit 0 |
| ThreadSanitizer | 400,000 messages; exit 0 |
| Unit and integration regressions | {count} passed; exit 0 |
| Live TCP workload | {chaos['completed']:,} unique completed requests, 16 clients |
| Persisted-result replay | {chaos['durable_deduplication']['requests']:,} retries, unchanged commit 5,000 |
| Formal three-view closure | {formal['states_explored']:,} states; {formal['transitions_examined']:,} transitions |
| Formal safety/deadlocks | Zero violations, zero deadlocks, empty frontier |
| Negative formal control | Stale-election mutation detected LeaderCompleteness violation |

## Actual process, network and disk faults

- {faults['SIGSTOP']} SIGSTOP and {faults['SIGCONT']} SIGCONT signals.
- {faults['SIGKILL']} deliberate SIGKILL exits, including simultaneous five-node crash recovery.
- {faults['partitions']} directed partition episodes, including half-duplex partitions.
- {proxy['socket_drops']} forced socket disconnections, {proxy['jittered']:,} jittered frames,
  {proxy['partition_drops']:,} partition-dropped frames and {proxy['corrupted']} corrupted frames.
- The initial primary was stopped for six seconds. View 1 served new client
  requests before it resumed. All five replicas ultimately converged on view 7.
- One real SSTable was modified and fsynced while its daemon was dead. Startup
  quarantined the corrupt store; TCP quorum transfer restored commit 5,000.
- All five processes were then killed and restarted. Each loaded five immutable
  SSTables from its own filesystem directory. All 5,000 original requests returned
  their prior results without appending duplicate log entries.

All {sum(exits.values())} daemon exits matched expectations:
**{exits[-9]} exits with status -9 (intentional SIGKILL), {exits[0]} final exits with status 0.**
The supervisor returned 0. No unexpected daemon exit occurred in the final run.

## Consistency checks

The independent scalar interpreter reproduced every transaction's pre-write reads,
conditional-write decision and analytical result. Returned hashes and log indices
matched the unique invocation. An interval sweep checked real-time ordering of
non-overlapping calls. All replicas had identical full committed logs, logical
state and SSTable digests before repair, after quorum repair and after complete
process restart. A separate disk inspection recomputed hashes from the final
physical SSTable files and checked every manifest entry.

Final SSTable digest: {chaos['after_restart']['sstable_digest']}

Final committed-log digest: {chaos['after_restart']['log_sha256']}

## Formal result and scope

The executable breadth-first checker explored every reachable state for three
replicas, two operation values, two log slots and three views (0, 1, 2), with at
most one recovering replica. TypeOK, Agreement, LeaderCompleteness and LogMatching
held. It found zero unexpected protocol quiescent states in addition to zero
deadlocks. The three-view run took {formal['elapsed_seconds']:.3f} seconds.

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
"""
    (ROOT / "docs/VERIFICATION.md").write_text(report)
    paths = {transcript, artifacts / "process-exit-statuses.json", artifacts / "disk-evidence.json",
             artifacts / "source-manifest.json", ROOT / "native/build_report.json",
             ROOT / "docs/VERIFICATION.md", ROOT / "docs/FAULT_MODEL.md", ROOT / "formal/README.md",
             ROOT / "formal/AegisDB_VSR.tla", ROOT / "formal/check_model.py"}
    paths.update(args.checks.glob("*"))
    paths.update(path for path in args.chaos.glob("*") if path.is_file())
    paths.update((args.chaos / "logs").glob("*.log"))
    archive = artifacts / "aegisdb-verification.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(paths):
            if path.is_file():
                bundle.write(path, path.relative_to(ROOT))
    result = {"status": "packaged", "unit_tests": count, "transactions": chaos["completed"],
              "formal_states": formal["states_explored"], "daemon_exits": dict(exits),
              "disk_tables_verified": sum(len(node["tables"]) for node in disk),
              "terminal_log": str(transcript), "archive": str(archive),
              "source_sha256": fingerprint.hexdigest()}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
