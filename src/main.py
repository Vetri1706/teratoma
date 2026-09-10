"""Command-line reproducible simulation runner: python -m src.main."""
import argparse
import json
import sys
from pathlib import Path
from src.testing.vopr_harness import Cluster


def main():
    parser = argparse.ArgumentParser(description="AegisDB deterministic verification")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    parser.add_argument("--ticks", type=int, default=10000)
    parser.add_argument("--recovery-ticks", type=int, default=2500)
    parser.add_argument("--no-chaos", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--failure-trace", type=Path, default=Path("failure-trace.json"))
    args = parser.parse_args()
    if args.ticks < 1 or args.recovery_ticks < 1:
        parser.error("tick budgets must be positive")
    reports = []
    for seed in args.seeds:
        cluster = Cluster(seed, faults=not args.no_chaos)
        try:
            report = cluster.verify(args.ticks, args.recovery_ticks)
        except Exception as error:
            trace = {"seed": seed, "ticks": args.ticks, "recovery_ticks": args.recovery_ticks,
                     "no_chaos": args.no_chaos, "failed_at": cluster.env.now,
                     "error": repr(error), "trace": list(cluster.env.trace)}
            args.failure_trace.write_text(json.dumps(trace, indent=2), encoding="utf-8")
            print(f"FAIL seed={seed} tick={cluster.env.now}: {error}; trace={args.failure_trace}", file=sys.stderr)
            raise
        reports.append(report)
        print(json.dumps(report, sort_keys=True), flush=True)
    if args.report:
        args.report.write_text(json.dumps({"runs": reports}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
