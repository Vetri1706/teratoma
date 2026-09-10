#!/usr/bin/env python3
"""Run production checks and preserve exact command output and exit statuses."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "verification")
    parser.add_argument("--max-view", type=int, default=1)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    commands = [
        ("unit-tests", [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("model", [sys.executable, "-B", "formal/check_model.py", "--max-view", str(args.max_view)]),
        ("model-mutation", [sys.executable, "-B", "formal/check_model.py", "--mutation", "--expect-counterexample"]),
    ]
    results = []
    for name, command in commands:
        print("$ " + " ".join(command), flush=True)
        started = time.monotonic()
        child = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (args.output / f"{name}.log").write_text(child.stdout)
        print(child.stdout, end="", flush=True)
        result = {"name": name, "command": command, "exit_status": child.returncode,
                  "elapsed_seconds": round(time.monotonic() - started, 3)}
        if name.startswith("model"):
            try:
                report = json.loads(child.stdout)
                (args.output / f"{name}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            except ValueError:
                result["invalid_json"] = True
        results.append(result)
        print(json.dumps(result), flush=True)
    (args.output / "checks.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(result["exit_status"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
