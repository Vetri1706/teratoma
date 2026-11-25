"""Generate a portable replay of the real deterministic engine, without a server.

Run ``python -m src.demo`` from the repository root, then open the printed HTML
path. The browser replays recorded state; it does not run the Python protocol.
"""
import argparse
import json
from pathlib import Path
import shutil

from src.testing.vopr_harness import Cluster

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "healthy": ("Steady state", "Follow a transaction through five replicas.",
                "What does agreement look like before anything breaks?"),
    "primary-crash": ("Lose the primary", "Stop the leader while clients keep writing.",
                      "Can the surviving replicas make progress without node 0?"),
    "partition": ("Split the network", "Isolate the primary with one other replica.",
                  "Why can three replicas make progress while two cannot?"),
    "corruption": ("Damage a disk", "Corrupt a replica's persisted WAL, then reboot it.",
                   "How does a replica recover when its own log cannot be trusted?"),
}
FAULT_TICK, HEAL_TICK, DRAIN_TICK, END_TICK = 300, 1000, 1400, 2000


def record_scenario(name, seed=7):
    """Record observations only; all transitions use the existing VSR simulator."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")
    cluster = Cluster(seed, faults=False)
    cluster.start_clients(2)
    milestones = [{"tick": 0, "title": "Start two clients", "detail": "Five replicas; quorum = 3."}]
    phase = {"name": "Warm up", "detail": "Clients submit deterministic transactions."}
    fault_info = {}

    def inject_fault():
        fault_info["completed_before"] = len(cluster.checker.completed)
        if name == "healthy":
            phase.update(name="Steady state", detail="No injected faults in this control run.")
            title, detail = "Control run", "Continue with all five replicas connected."
        elif name == "primary-crash":
            primary = next(r for r in cluster.replicas if r.alive and r.status == "normal" and r.primary)
            fault_info["node"] = primary.id
            primary.crash()
            title, detail = f"Stop node {primary.id}", "The primary stays down until tick 1000."
            phase.update(name="Primary offline", detail="Survivors must elect a new primary.")
        elif name == "partition":
            primary = next(r for r in cluster.replicas if r.alive and r.status == "normal" and r.primary)
            minority = {primary.id, (primary.id + 1) % len(cluster.replicas)}
            cluster.network.blocked = {(a, b) for a in range(5) for b in range(5)
                                       if (a in minority) != (b in minority)}
            fault_info["minority"] = sorted(minority)
            title, detail = "Partition 2 / 3", "Blocked links are bidirectional; clients keep retrying."
            phase.update(name="Network split", detail="The isolated pair cannot form a quorum of three.")
        else:
            replica = cluster.replicas[4]
            replica.crash()
            # Checked WAL header bytes, not harmless padding. Mirrors the
            # existing corrupt-WAL recovery regression test.
            replica.disk.corrupt(0, 12, 1)
            cluster.env.schedule(20, replica.reboot, "demo-corruption-reboot")
            fault_info["node"] = replica.id
            title, detail = "Corrupt node 4 WAL", "Reboot at tick 320; recovery must obtain quorum evidence."
            phase.update(name="Disk corruption", detail="A damaged replica must recover before participating.")
        milestones.append({"tick": cluster.env.now, "title": title, "detail": detail})

    def heal():
        fault_info["completed_during_fault"] = len(cluster.checker.completed) - fault_info["completed_before"]
        fault_info["new_requests_completed_during_fault"] = sum(
            FAULT_TICK < cluster.checker.invocations[identity]["tick"]
            and completed["tick"] < HEAL_TICK
            for identity, completed in cluster.checker.completed.items())
        cluster.chaos.heal_all()
        phase.update(name="Recover and converge", detail="All links are restored and every replica is running.")
        milestones.append({"tick": cluster.env.now, "title": "Restore the cluster",
                           "detail": "Heal the network and reboot any stopped replica."})

    def drain():
        cluster.client_enabled = False
        phase.update(name="Drain outstanding work", detail="Stop new requests; let accepted work complete.")
        milestones.append({"tick": cluster.env.now, "title": "Stop new requests",
                           "detail": "Finish pending transactions, then compare all five replicas."})

    cluster.env.schedule(FAULT_TICK, inject_fault, "demo-inject")
    cluster.env.schedule(HEAL_TICK, heal, "demo-heal")
    cluster.env.schedule(DRAIN_TICK, drain, "demo-drain")
    frames = []
    for tick in range(0, END_TICK + 1, 20):
        cluster.run(tick)  # Includes the independent checker at every virtual tick.
        frames.append({
            "tick": tick, "phase": dict(phase),
            "completed": len(cluster.checker.completed), "checks": cluster.checker.checks,
            "pending": sum(state["pending"] is not None for state in cluster.clients.values()),
            "network": dict(cluster.network.stats), "blocked": sorted(cluster.network.blocked),
            "nodes": [{"id": r.id, "alive": r.alive, "status": r.status,
                       "view": r.view, "primary": r.primary, "commit": r.commit,
                       "log": len(r.log), "keys": len(r.machine.storage.snapshot()),
                       "digest": r.machine.storage.digest(), "recoveries": r.stats["recoveries"]}
                      for r in cluster.replicas],
        })
    if any(state["pending"] is not None for state in cluster.clients.values()):
        raise AssertionError("demo ended with pending transactions")
    if any(not r.alive or r.status != "normal" for r in cluster.replicas):
        raise AssertionError("demo replicas did not recover")
    if len({r.commit for r in cluster.replicas}) != 1 or len({r.machine.storage.digest() for r in cluster.replicas}) != 1:
        raise AssertionError("demo replicas did not converge")
    if fault_info["new_requests_completed_during_fault"] < 1:
        raise AssertionError("demo completed no newly invoked requests during its fault window")
    if name == "corruption" and cluster.replicas[4].stats["recoveries"] < 1:
        raise AssertionError("corrupted replica did not take the recovery path")
    milestones.append({"tick": END_TICK, "title": "Convergence checked",
                       "detail": "All five replicas have the same commit index and storage digest."})
    title, summary, question = SCENARIOS[name]
    return {"id": name, "title": title, "summary": summary, "question": question,
            "seed": seed, "milestones": milestones, "frames": frames,
            "result": {"passed": True, "transactions": len(cluster.checker.completed),
                       "invariant_checks": cluster.checker.checks,
                       "progress_during_fault": fault_info["completed_during_fault"],
                       "new_requests_completed_during_fault": fault_info["new_requests_completed_during_fault"],
                       "commit_index": cluster.replicas[0].commit,
                       "digest": cluster.replicas[0].machine.storage.digest()}}


def generate(output, seed=7, scenario="all"):
    names = list(SCENARIOS) if scenario == "all" else [scenario]
    data = {"schema_version": 1, "engine": "AegisDB deterministic simulator",
            "sample_interval": 20, "scenarios": [record_scenario(name, seed) for name in names]}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, separators=(",", ":"), sort_keys=True)
    (output / "traces.json").write_text(encoded + "\n", encoding="utf-8")
    # The same report also opens directly from disk, without fetch/CORS or a
    # local HTTP server. Escape HTML delimiters in case the schema later grows.
    script_data = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (output / "traces.js").write_text("window.AEGIS_DEMOS = " + script_data + ";\n", encoding="utf-8")
    for asset in ("index.html", "styles.css", "app.js"):
        source = ROOT / "docs/lab" / asset
        target = output / asset
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/demo"), help="report directory")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    args = parser.parse_args(argv)
    data = generate(args.output, args.seed, args.scenario)
    for scenario in data["scenarios"]:
        result = scenario["result"]
        print(f"PASS {scenario['id']}: {result['transactions']} transactions, "
              f"{result['invariant_checks']} checks, five replicas converged")
    print(f"Open {(args.output / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
