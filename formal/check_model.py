"""Exhaustive checker for the finite VSR abstraction in AegisDB_VSR.tla."""
from __future__ import annotations
import argparse
from collections import Counter, deque
from itertools import combinations
import json
import sys
import time

NORMAL, VIEW_CHANGE, RECOVERY = 0, 1, 2
N, VALUES, QUORUM = 3, 2, 2


def replace(items, index, value):
    return items[:index] + (value,) + items[index + 1:]


def initial(max_view, max_log):
    node = (0, 0, NORMAL, (), 0)
    return ((node,) * N, ((),) + (None,) * max_view,
            (0,) * (max_view + 1), (0,) * ((max_view + 1) * max_log),
            (0,) * (max_view + 1), ())


def successors(state, max_view, max_log, mutation=False):
    nodes, proposals, floors, acks, announced, decided = state
    recovering = sum(node[2] == RECOVERY for node in nodes)
    for replica, node in enumerate(nodes):
        view, normal_view, mode, log, commit = node
        if mode != RECOVERY and recovering == 0:
            yield f"Crash({replica})", (replace(nodes, replica, (view, normal_view, RECOVERY, log, commit)), proposals, floors, acks, announced, decided)
        if mode == RECOVERY and N - recovering >= QUORUM:
            restored = NORMAL if view == normal_view else VIEW_CHANGE
            yield f"Recover({replica})", (replace(nodes, replica, (view, normal_view, restored, log, commit)), proposals, floors, acks, announced, decided)
        if mode != RECOVERY:
            for target in range(view + 1, max_view + 1):
                yield f"StartViewChange({replica},{target})", (replace(nodes, replica, (target, normal_view, VIEW_CHANGE, log, commit)), proposals, floors, acks, announced, decided)
        if mode == NORMAL and replica == view % N and proposals[view] == log:
            if len(log) < max_log:
                for value in range(1, VALUES + 1):
                    new_log = log + (view * VALUES + value,)
                    slot = view * max_log + len(log)
                    new_acks = replace(acks, slot, acks[slot] | (1 << replica))
                    yield f"Propose({replica},{value})", (replace(nodes, replica, (view, normal_view, mode, new_log, commit)), replace(proposals, view, new_log), floors, new_acks, announced, decided)
            if commit < len(log) and acks[view * max_log + commit].bit_count() >= QUORUM:
                next_commit = commit + 1
                history = decided
                if next_commit > len(decided):
                    history = decided + ((log[commit], view),)
                yield f"Commit({replica},{next_commit})", (replace(nodes, replica, (view, normal_view, mode, log, next_commit)), proposals, floors, acks, replace(announced, view, max(announced[view], next_commit)), history)
    for view, sent in enumerate(proposals):
        if sent is None:
            continue
        for replica, node in enumerate(nodes):
            old_view, old_normal, mode, old_log, commit = node
            if mode == RECOVERY or old_view > view:
                continue
            for length in range(floors[view], len(sent) + 1):
                received = sent[:length]
                if length < commit or received[:commit] != old_log[:commit]:
                    continue
                if old_view == view and mode == NORMAL and (length < len(old_log) or received[:len(old_log)] != old_log):
                    continue
                new_acks = acks
                for index in range(length):
                    slot = view * max_log + index
                    new_acks = replace(new_acks, slot, new_acks[slot] | (1 << replica))
                new_node = (view, view, NORMAL, received, commit)
                target = (replace(nodes, replica, new_node), proposals, floors, new_acks, announced, decided)
                if target != state:
                    label = "Prepare" if old_view == view and mode == NORMAL else "StartView"
                    yield f"{label}({view},{replica},{length})", target
            known = announced[view]
            if mode == NORMAL and old_view == view and commit < known <= len(old_log) and old_log[:known] == sent[:known]:
                yield f"CommitNotice({view},{replica},{known})", (replace(nodes, replica, (old_view, old_normal, mode, old_log, known)), proposals, floors, acks, announced, decided)
    for view in range(1, max_view + 1):
        primary = view % N
        if proposals[view] is not None or nodes[primary][0] != view or nodes[primary][2] != VIEW_CHANGE:
            continue
        eligible = [replica for replica, node in enumerate(nodes) if node[0] == view and node[2] == VIEW_CHANGE]
        for voters in combinations(eligible, QUORUM):
            if primary not in voters:
                continue
            rank = lambda replica: (nodes[replica][1], len(nodes[replica][3]), -replica)
            selected = min(voters, key=rank) if mutation else max(voters, key=rank)
            log = nodes[selected][3]
            commit = max(nodes[replica][4] for replica in voters)
            if mutation:
                commit = min(commit, len(log))
            new_acks = acks
            for index in range(len(log)):
                slot = view * max_log + index
                new_acks = replace(new_acks, slot, new_acks[slot] | (1 << primary))
            yield f"DoViewChange({view},{voters},source={selected})", (replace(nodes, primary, (view, view, NORMAL, log, commit)), replace(proposals, view, log), replace(floors, view, len(log)), new_acks, replace(announced, view, commit), decided)


def violation(state, max_view, max_log):
    nodes, proposals, floors, acks, announced, decided = state
    if len(nodes) != N:
        return "TypeOK", "incorrect replica domain"
    for replica, (view, normal_view, mode, log, commit) in enumerate(nodes):
        if not (0 <= normal_view <= view <= max_view and mode in (NORMAL, VIEW_CHANGE, RECOVERY)
                and 0 <= commit <= len(log) <= max_log
                and all(type(entry) is int and 1 <= entry <= (max_view + 1) * VALUES for entry in log)):
            return "TypeOK", f"replica {replica} has an invalid durable state"
        if len(decided) < commit or log[:commit] != tuple(entry for entry, _ in decided[:commit]):
            return "Agreement", f"replica {replica} disagrees with previously committed history"
        if mode == NORMAL and replica == view % N:
            for index, (entry, committed_view) in enumerate(decided):
                if committed_view < view and (len(log) <= index or log[index] != entry):
                    return "LeaderCompleteness", f"view {view} primary omits earlier committed position {index + 1}"
    for left, right in combinations(nodes, 2):
        for index in range(min(len(left[3]), len(right[3]))):
            if (left[3][index] - 1) // VALUES == (right[3][index] - 1) // VALUES and left[3][:index + 1] != right[3][:index + 1]:
                return "LogMatching", f"same-view entry at position {index + 1} has different prefixes"
    return None


def describe(state):
    nodes, proposals, floors, acks, announced, decided = state
    labels = ["Normal", "ViewChange", "Recovery"]
    return {"nodes": [{"view": n[0], "last_normal_view": n[1], "status": labels[n[2]], "log": list(n[3]), "commit": n[4]} for n in nodes], "proposals": proposals, "floors": floors, "ack_masks": acks, "announced": announced, "decided": decided}


def trace_to(state, parents):
    path = []
    while parents[state] is not None:
        previous, action = parents[state]
        path.append({"action": action, "state": describe(state)})
        state = previous
    path.append({"action": "Init", "state": describe(state)})
    path.reverse()
    return path


def check(max_view=1, max_log=2, mutation=False):
    started = time.monotonic()
    first = initial(max_view, max_log)
    queue = deque([(first, 0)])
    parents = {first: None}
    transitions = depth_reached = deadlocks = terminal = unexpected_quiescent = recovery_at_view_bound = 0
    actions = Counter()
    quiescence_examples = []
    while queue:
        state, depth = queue.popleft()
        depth_reached = max(depth_reached, depth)
        failure = violation(state, max_view, max_log)
        if failure is not None:
            return {"status": "counterexample", "exhaustive": False, "invariant": failure[0], "reason": failure[1], "states_discovered": len(parents), "transitions_examined": transitions, "counterexample": trace_to(state, parents), "elapsed_seconds": round(time.monotonic() - started, 3)}
        enabled = list(successors(state, max_view, max_log, mutation))
        if not enabled:
            deadlocks += 1
        substantive = [label for label, _ in enabled if not label.startswith("Crash(")]
        final = len(state[5]) == max_log and all(node[2] == NORMAL and node[4] == max_log for node in state[0])
        if not substantive:
            if final:
                terminal += 1
            else:
                unexpected_quiescent += 1
        if (not enabled or (not substantive and not final)) and len(quiescence_examples) < 3:
            quiescence_examples.append(trace_to(state, parents))
        if max(node[0] for node in state[0]) == max_view and any(label.startswith("Recover(") for label, _ in enabled):
            recovery_at_view_bound += 1
        for label, target in enabled:
            transitions += 1
            actions[label.split("(", 1)[0]] += 1
            if target not in parents:
                parents[target] = (state, label)
                queue.append((target, depth + 1))
    status = "verified" if deadlocks == 0 and unexpected_quiescent == 0 else "quiescence_failure"
    return {"status": status, "exhaustive": True, "frontier_remaining": 0,
        "scope": "full reachable state space of the finite abstraction; no depth truncation",
        "bounds": {"replicas": N, "values": VALUES, "max_log": max_log, "views": list(range(max_view + 1)), "simultaneous_recovery": 1},
        "invariants": ["TypeOK", "Agreement", "LeaderCompleteness", "LogMatching"],
        "states_explored": len(parents), "transitions_examined": transitions,
        "maximum_shortest_path_depth": depth_reached, "deadlocks": deadlocks,
        "intentional_bounded_terminal_states": terminal, "unexpected_protocol_quiescent_states": unexpected_quiescent,
        "quiescence_examples": quiescence_examples, "recovery_enabled_at_max_view_states": recovery_at_view_bound,
        "action_counts": dict(sorted(actions.items())), "liveness_checked": False,
        "elapsed_seconds": round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-view", type=int, default=1)
    parser.add_argument("--max-log", type=int, default=2)
    parser.add_argument("--mutation", action="store_true")
    parser.add_argument("--expect-counterexample", action="store_true")
    arguments = parser.parse_args()
    if arguments.max_view < 1 or arguments.max_log < 1:
        parser.error("at least two views and one log position required")
    report = check(arguments.max_view, arguments.max_log, arguments.mutation)
    report["mutation"] = arguments.mutation
    print(json.dumps(report, sort_keys=True, indent=2))
    if arguments.expect_counterexample:
        return 0 if report["status"] == "counterexample" else 3
    return 0 if report["status"] == "verified" else 2


if __name__ == "__main__":
    sys.exit(main())
