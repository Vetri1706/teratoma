#!/usr/bin/env python3
"""Compile the unbounded Lean development and record its kernel dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "formal" / "unbounded"
VERSION = "4.19.0"
FOUNDATIONS = {"propext", "Quot.sound"}
FORBIDDEN = re.compile(r"\b(sorry|admit|assume|axiom|unsafe|native_decide|sorryAx|implemented_by|extern|trustCompiler|ofReduceBool)\b|debug\.skipKernelTC")
CLAIMS = [
    "Aegis.reachable_agreement",
    "Aegis.reachable_leader_completeness",
    "Aegis.infinite_execution_agreement",
    "Aegis.infinite_execution_leader_completeness",
    "Aegis.execution_promises_monotone",
    "Aegis.execution_accepted_views_monotone",
    "Aegis.execution_applied_indices_monotone",
    "Aegis.execution_applied_prefix",
    "Aegis.all_time_agreement",
    "Aegis.past_commit_in_future_leader",
    "Aegis.nonempty_applied_trace",
    "Aegis.three_replica_election_trace",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lean-home", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "unbounded")
    args = parser.parse_args()
    local = ROOT / ".tools" / "lean" / "lean-4.19.0-darwin_aarch64"
    lean_home = args.lean_home or (local if local.is_dir() else None)
    lean = str(lean_home / "bin" / "lean") if lean_home else shutil.which("lean")
    lake = str(lean_home / "bin" / "lake") if lean_home else shutil.which("lake")
    if not lean or not lake:
        parser.error("Lean 4.19.0 is required; provide --lean-home or put lean and lake on PATH")
    args.output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = str(Path(lean).resolve().parent) + os.pathsep + env.get("PATH", "")
    checks = []
    transcript = []

    def run(name, command):
        started = time.monotonic()
        header = "$ " + " ".join(command) + "\n"
        print(header, end="", flush=True)
        child = subprocess.run(command, cwd=PROJECT, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        entry = {"name": name, "command": command, "exit_status": child.returncode,
                 "elapsed_seconds": round(time.monotonic() - started, 3)}
        checks.append(entry)
        footer = json.dumps(entry, sort_keys=True) + "\n"
        transcript.extend((header, child.stdout, footer))
        (args.output / (name + ".log")).write_text(header + child.stdout + footer)
        print(child.stdout + footer, end="", flush=True)
        return child

    source_hashes = {}
    failures = []
    for source in sorted(PROJECT.rglob("*.lean")):
        if ".lake" in source.parts:
            continue
        data = source.read_bytes()
        relative = str(source.relative_to(ROOT))
        source_hashes[relative] = hashlib.sha256(data).hexdigest()
        # A conservative source gate: prohibited proof escapes fail even in comments.
        matches = sorted(set(FORBIDDEN.findall(data.decode())))
        if matches:
            failures.append({"source": relative, "prohibited_tokens": matches})
    version = run("lean-version", [lean, "--version"])
    if version.returncode or ("version " + VERSION + ",") not in version.stdout:
        failures.append({"toolchain": "Expected Lean " + VERSION})
    dependencies = {}
    if not failures:
        clean = run("lake-clean", [lake, "clean"])
        if clean.returncode:
            failures.append({"clean": "Lean project cleanup failed"})
        build = run("lake-build", [lake, "build"])
        if build.returncode:
            failures.append({"build": "Lean build failed"})
        else:
            audit_source = args.output / "KernelAudit.lean"
            audit_source.write_text("import Aegis\n" + "\n".join(
                "#check " + name + "\n#print axioms " + name for name in CLAIMS) + "\n")
            audit = run("kernel-audit", [lake, "env", "lean", str(audit_source.resolve())])
            if audit.returncode:
                failures.append({"kernel_audit": "Lean rejected the dependency audit"})
            for name in CLAIMS:
                pattern = re.escape("'" + name + "'") + r" depends on axioms:\s*\[([^\]]*)\]"
                match = re.search(pattern, audit.stdout)
                if match:
                    deps = [d.strip() for d in match.group(1).split(",") if d.strip()]
                elif ("'" + name + "' does not depend on any axioms") in audit.stdout:
                    deps = []
                else:
                    failures.append({"theorem": name, "audit": "Missing dependency result"})
                    continue
                dependencies[name] = deps
                unexpected = sorted(set(deps) - FOUNDATIONS)
                if unexpected:
                    failures.append({"theorem": name, "unexpected_axioms": unexpected})
    report = {"toolchain": VERSION, "checks": checks, "source_sha256": source_hashes,
              "theorem_axioms": dependencies, "allowed_lean_foundations": sorted(FOUNDATIONS),
              "failures": failures, "exit_status": int(bool(failures))}
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    transcript.append(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output / "complete-terminal.log").write_text("".join(transcript))
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return report["exit_status"]


if __name__ == "__main__":
    sys.exit(main())
