#!/usr/bin/env python3
"""Real TCP/process/disk chaos supervisor; no virtual runtime is imported."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.production.framing import HEADER, MAGIC, FRAGMENT_MAGIC, MAX_FRAME, frame, read_frame, rpc
from src.consensus.types import Entry, ZERO_HASH, digest
from src.testing.linearizability_checker import reference_query


class FaultProxy:
    """Independent TCP connections with faults applied to directed framed traffic."""
    def __init__(self, targets, ports, seed):
        self.targets, self.ports = targets, ports
        self.rng = random.Random(seed)
        self.blocked = frozenset()
        self.enabled = False
        self.stats = {"connections": 0, "forwarded": 0, "partition_drops": 0,
                      "socket_drops": 0, "jittered": 0, "corrupted": 0}
        self.ready = threading.Event()
        self.loop = None
        self.failure = None
        self.thread = threading.Thread(target=self._run, name="chaos-tcp-proxies", daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("proxy startup timed out")
        if self.failure:
            raise RuntimeError(self.failure)

    async def _pipe(self, reader, writer):
        while True:
            header = await reader.readexactly(HEADER.size)
            magic, size, source, target, _ = HEADER.unpack(header)
            if magic not in (MAGIC, FRAGMENT_MAGIC) or not 0 < size <= MAX_FRAME:
                raise ValueError("invalid proxy frame")
            body = await reader.readexactly(size)
            if (source, target) in self.blocked:
                self.stats["partition_drops"] += 1
                continue
            if self.enabled:
                if self.rng.random() < 0.004:
                    self.stats["socket_drops"] += 1
                    writer.transport.abort()
                    return
                delay = self.rng.random() * 0.012
                self.stats["jittered"] += 1
                await asyncio.sleep(delay)
                if self.rng.random() < 0.0008:
                    changed = bytearray(body)
                    changed[self.rng.randrange(size)] ^= 1
                    body = bytes(changed)
                    self.stats["corrupted"] += 1
            writer.write(header + body)
            await writer.drain()
            self.stats["forwarded"] += 1

    async def _connection(self, reader, writer, target):
        upstream = None
        tasks = []
        self.stats["connections"] += 1
        try:
            incoming, upstream = await asyncio.open_connection(*self.targets[target])
            tasks = [asyncio.create_task(self._pipe(reader, upstream)),
                     asyncio.create_task(self._pipe(incoming, writer))]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (OSError, EOFError, ValueError, asyncio.IncompleteReadError, ConnectionError):
            self.stats["closed_connections"] = self.stats.get("closed_connections", 0) + 1
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            if upstream is not None:
                upstream.close()

    async def _serve(self):
        servers = []
        try:
            for node, port in self.ports.items():
                async def accept(reader, writer, target=node):
                    await self._connection(reader, writer, target)
                servers.append(await asyncio.start_server(accept, "127.0.0.1", port))
            self.ready.set()
            await self.stop_event.wait()
        finally:
            for server in servers:
                server.close()
                await server.wait_closed()

    def _run(self):
        async def run():
            self.loop = asyncio.get_running_loop()
            self.stop_event = asyncio.Event()
            await self._serve()
        try:
            asyncio.run(run())
        except Exception as error:
            self.failure = repr(error)
            self.ready.set()

    def close(self):
        if self.loop is not None and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stop_event.set)
        self.thread.join(10)
        if self.thread.is_alive() or self.failure:
            raise RuntimeError(f"proxy shutdown failed: {self.failure}")


class Supervisor:
    def __init__(self, args):
        self.args = args
        self.out = args.output.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "logs").mkdir()
        self.transcript = (self.out / "terminal.log").open("w", buffering=1)
        self.history_file = (self.out / "client-history.jsonl").open("w", buffering=1)
        self.targets = {i: ("127.0.0.1", args.base_port + i) for i in range(5)}
        self.ports = {i: args.proxy_port + i for i in range(5)}
        self.peers = self.out / "peers.json"
        self.peers.write_text(json.dumps({i: ["127.0.0.1", port] for i, port in self.ports.items()}))
        self.processes, self.incarnations, self.exits = {}, {}, []
        self.paused = set()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.completed, self.highest, self.retries = 0, 0, 0
        self.history = []
        self.faults = {"SIGSTOP": 0, "SIGCONT": 0, "SIGKILL": 0, "partitions": 0}
        self.proxy = None
        self.deadline = time.monotonic() + args.timeout
        self.started = time.monotonic()

    def emit(self, event, **values):
        with self.lock:
            line = json.dumps({"event": event, "elapsed": round(time.monotonic() - self.started, 3), **values}, sort_keys=True)
            print(line, flush=True)
            self.transcript.write(line + "\n")

    def start(self, node, bootstrap=False):
        incarnation = self.incarnations.get(node, 0) + 1
        self.incarnations[node] = incarnation
        path = self.out / "logs" / f"node-{node}-{incarnation}.log"
        command = [str(ROOT / "bin/aegisdbd"), "--node", str(node), "--port", str(self.targets[node][1]),
                   "--peers", str(self.peers), "--data-dir", str(self.out / "data" / f"node_{node}"),
                   "--checkpoint-every", "128", "--workers", "4"]
        if bootstrap:
            command.append("--bootstrap")
        with path.open("wb") as output:
            process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
        self.processes[node] = (process, incarnation, path)
        self.emit("spawn", node=node, pid=process.pid, incarnation=incarnation, argv=command, log=str(path))

    def reap(self, node, expected, reason):
        process, incarnation, path = self.processes[node]
        status = process.wait(timeout=15)
        result = {"node": node, "pid": process.pid, "incarnation": incarnation,
                  "exit_status": status, "expected": expected, "reason": reason, "log": str(path)}
        self.exits.append(result)
        del self.processes[node]
        self.emit("process_exit", **result)
        if status != expected:
            raise AssertionError(f"node {node} unexpected exit {status}: {path.read_text()[-6000:]}")

    def kill(self, node):
        process = self.processes[node][0]
        os.kill(process.pid, signal.SIGKILL)
        self.faults["SIGKILL"] += 1
        self.emit("signal", node=node, pid=process.pid, signal="SIGKILL")
        self.reap(node, -signal.SIGKILL, "chaos")

    def status(self, node, kind="status", timeout=1):
        return rpc(self.targets[node], node, {"type": kind}, timeout=timeout)

    def wait_ready(self, nodes=range(5), timeout=30):
        deadline = min(self.deadline, time.monotonic() + timeout)
        pending = set(nodes)
        while pending and time.monotonic() < deadline:
            for node in list(pending):
                process = self.processes[node][0]
                if process.poll() is not None:
                    self.reap(node, 0, "unexpected startup exit")
                    raise AssertionError("daemon exited during startup")
                try:
                    self.status(node)
                    pending.remove(node)
                except (OSError, EOFError):
                    continue
            if pending:
                time.sleep(0.05)
        if pending:
            raise TimeoutError(f"nodes failed to open TCP listeners: {pending}")

    def request(self, client, seq):
        key, second = f"k{(client + seq) % 24:02}", f"k{(client * 7 + seq + 1) % 24:02}"
        request = {"client": client, "seq": seq, "reads": [key, second]}
        if seq % 4:
            request["writes"] = {key: client * 100000 + seq, second: -seq}
        if seq % 13 == 0:
            request["expect"] = {key: -999999}
        if seq % 9 == 0:
            request["query"] = {"group_by": [], "aggregates": {
                "sum": ["SUM", "value"], "count": ["COUNT", None],
                "min": ["MIN", "value"], "max": ["MAX", "value"], "avg": ["AVG", "value"]}}
        return request

    def client(self, client, count):
        connection, connected = None, None
        candidate = client % 5
        rng = random.Random(self.args.seed + client + 100)
        try:
            for seq in range(1, count + 1):
                request = self.request(client, seq)
                with self.lock:
                    invocation = time.monotonic_ns()
                    floor = self.highest
                while not self.stop.is_set() and time.monotonic() < self.deadline:
                    try:
                        if connection is None or connected != candidate:
                            if connection is not None:
                                connection.close()
                            connection = socket.create_connection(self.targets[candidate], timeout=1)
                            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                            connection.settimeout(1)
                            connected = candidate
                        connection.sendall(frame(1000 + client, candidate, {"type": "client", "request": request}))
                        reply_deadline = time.monotonic() + 1.2
                        while True:
                            _, target, reply = read_frame(connection)
                            if target != 1000 + client:
                                raise AssertionError("reply delivered to wrong client")
                            if reply["type"] == "redirect":
                                candidate = reply["primary"] if reply["ready"] else rng.randrange(5)
                                time.sleep(0.01)
                                break
                            if reply["type"] == "error":
                                raise AssertionError(reply)
                            if reply["type"] != "reply" or reply["client"] != client:
                                raise AssertionError("invalid transaction response")
                            if reply["seq"] != seq:
                                if time.monotonic() > reply_deadline:
                                    raise TimeoutError("only obsolete responses received")
                                continue
                            with self.lock:
                                completion = time.monotonic_ns()
                                record = {"request": request, "invoked_ns": invocation, "completed_ns": completion,
                                          "floor": floor, "reply": reply}
                                self.history.append(record)
                                self.history_file.write(json.dumps(record, sort_keys=True) + "\n")
                                self.completed += 1
                                self.highest = max(self.highest, reply["index"])
                                if self.completed % 250 == 0:
                                    self.emit("load_progress", completed=self.completed, committed_index=self.highest,
                                              retries=self.retries)
                            break
                        if reply["type"] == "reply" and reply["seq"] == seq:
                            break
                    except (OSError, EOFError):
                        if connection is not None:
                            connection.close()
                        connection, connected = None, None
                        candidate = rng.randrange(5)
                    with self.lock:
                        self.retries += 1
                    time.sleep(0.005)
                else:
                    raise TimeoutError(f"client {client} stopped at sequence {seq}")
                if self.args.client_delay:
                    time.sleep(self.args.client_delay)
        finally:
            if connection is not None:
                connection.close()

    def chaos(self, futures):
        rng = random.Random(self.args.seed)
        round_number = 0
        while not all(future.done() for future in futures):
            if time.monotonic() >= self.deadline:
                raise TimeoutError("real chaos workload exceeded deadline")
            for future in futures:
                if future.done():
                    future.result()
            for node, (process, _, _) in list(self.processes.items()):
                if process.poll() is not None:
                    self.reap(node, 0, "unexpected runtime exit")
                    raise AssertionError("a daemon exited without a supervisor signal")
            if not self.args.chaos:
                time.sleep(0.1)
                continue
            if self.completed < 64:
                time.sleep(0.05)
                continue
            # Every round leaves at least three unpaused, mutually connected replicas.
            node = rng.randrange(5)
            if round_number % 3 == 0:
                targeted = round_number == 0
                if targeted:
                    observed = self.status(0)
                    node, old_view = observed["primary"], observed["view"]
                    baseline = self.completed
                pid = self.processes[node][0].pid
                os.kill(pid, signal.SIGSTOP)
                self.paused.add(node)
                self.faults["SIGSTOP"] += 1
                self.emit("signal", node=node, pid=pid, signal="SIGSTOP", targeted_primary=targeted)
                try:
                    time.sleep(6 if targeted else 0.35)
                    if targeted:
                        available = [self.status(i) for i in range(5) if i != node]
                        new_view = max(s["view"] for s in available)
                        with self.lock:
                            progressed = any(h["reply"]["view"] > old_view for h in self.history[baseline:])
                        if new_view <= old_view or not progressed:
                            raise AssertionError("quorum failed to elect and serve clients while the primary was stopped")
                        self.faults["primary_failovers"] = self.faults.get("primary_failovers", 0) + 1
                        self.emit("primary_failover_verified", stopped_node=node, old_view=old_view,
                                  new_view=new_view, completed_before=baseline, completed_after=self.completed)
                finally:
                    os.kill(pid, signal.SIGCONT)
                    self.paused.remove(node)
                    self.faults["SIGCONT"] += 1
                    self.emit("signal", node=node, pid=pid, signal="SIGCONT")
            elif round_number % 3 == 1:
                self.kill(node)
                time.sleep(0.2)
                self.start(node)
                self.wait_ready([node])
            else:
                minority = set(rng.sample(range(5), 2))
                # Alternate two-sided and asymmetric (half-duplex) partitions.
                asymmetric = (round_number // 3) % 2 == 0
                blocked = {(a, b) for a in range(5) for b in range(5) if a != b
                           and ((a in minority and b not in minority)
                                or (not asymmetric and b in minority and a not in minority))}
                self.proxy.blocked = frozenset(blocked)
                self.faults["partitions"] += 1
                self.emit("partition", minority=sorted(minority), half_duplex=asymmetric, blocked=sorted(blocked))
                time.sleep(0.7)
                self.proxy.blocked = frozenset()
                self.emit("partition_healed")
            round_number += 1
            time.sleep(0.25)

    def settle(self):
        deadline = min(self.deadline, time.monotonic() + 60)
        while time.monotonic() < deadline:
            statuses = []
            try:
                statuses = [self.status(i) for i in range(5)]
            except (OSError, EOFError):
                time.sleep(0.1)
                continue
            if all(s["status"] == "normal" and not s["busy"] for s in statuses) and len({s["view"] for s in statuses}) == 1:
                commits = {s["commit"] for s in statuses}
                if len(commits) == 1 and min(commits) >= self.highest:
                    return statuses
            time.sleep(0.1)
        raise TimeoutError(f"replicas failed to converge: {statuses}")

    def verify(self, stage):
        statuses = self.settle()
        audits = [self.status(i, "audit", timeout=15) for i in range(5)]
        for audit in audits:
            (self.out / f"{stage}-node-{audit['node']}.json").write_text(json.dumps(audit, sort_keys=True))
        log = audits[0]["log"]
        if not all(a["log"] == log and a["state"] == audits[0]["state"] and a["digest"] == audits[0]["digest"] for a in audits):
            raise AssertionError("replica committed histories, state or SSTable bytes diverged")
        invocations = {(h["request"]["client"], h["request"]["seq"]): h for h in self.history}
        if len(invocations) != self.args.requests or len(self.history) != self.args.requests:
            raise AssertionError("workload did not complete the requested number of unique operations")
        state, previous, seen = {}, ZERO_HASH, set()
        for index, wire in enumerate(log, 1):
            entry = Entry.parse(wire)
            if entry.index != index or entry.previous != previous:
                raise AssertionError("log index/hash-chain mismatch")
            previous = entry.hash
            req = entry.request
            identity = (req["client"], req["seq"])
            if identity in seen or identity not in invocations:
                raise AssertionError("duplicated or unknown transaction in committed log")
            seen.add(identity)
            history = invocations[identity]
            reply = history["reply"]
            accepted = all(state.get(key) == value for key, value in req.get("expect", {}).items())
            expected = {"reads": {key: state.get(key) for key in req.get("reads", [])}, "accepted": accepted}
            if "query" in req:
                expected["query"] = reference_query(state, req["query"])
            if accepted:
                state.update(req.get("writes", {}))
            if req != history["request"] or reply["request_hash"] != digest(req) or reply["hash"] != entry.hash:
                raise AssertionError("response does not identify the requested committed operation")
            if reply["index"] != index or index <= history["floor"] or reply["result"] != expected:
                raise AssertionError(f"linearizability/transaction result mismatch at {index}: {reply} != {expected}")
        if seen != set(invocations) or state != audits[0]["state"]:
            raise AssertionError("acknowledged transaction lost or replayed state differs")
        # Explicit interval check independently reconstructs the real-time floor.
        completions = sorted((h["completed_ns"], h["reply"]["index"]) for h in self.history)
        cursor, high = 0, 0
        for h in sorted(self.history, key=lambda item: item["invoked_ns"]):
            while cursor < len(completions) and completions[cursor][0] < h["invoked_ns"]:
                high = max(high, completions[cursor][1])
                cursor += 1
            if h["reply"]["index"] <= high:
                raise AssertionError("completed-before-invoked real-time order violated")
        result = {"operations": len(log), "replicas": 5, "strict_serializability": True,
                  "sstable_digest": audits[0]["digest"],
                  "identical_sstable_digests": True, "log_sha256": hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest(),
                  "views": [s["view"] for s in statuses], "recovered_sstables": [s["storage"]["recovered_sstable_count"] for s in statuses]}
        self.emit("verified", stage=stage, **result)
        return result

    def checkpoint_all(self):
        for node in range(5):
            deadline = min(self.deadline, time.monotonic() + 20)
            while time.monotonic() < deadline:
                status = self.status(node, "checkpoint")
                if status["checkpoint_requested"]:
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("could not request durable checkpoint")
        self.settle()
        statuses = [self.status(node) for node in range(5)]
        if any(s["last_checkpoint"] != s["commit"] or s["sstables"] == 0 for s in statuses):
            raise AssertionError("final checkpoints did not persist immutable tables")
        self.emit("all_checkpointed", commit=statuses[0]["commit"], files=[s["sstables"] for s in statuses])

    def exercise_quorum_storage_recovery(self):
        statuses = self.settle()
        primary = statuses[0]["primary"]
        node = next(i for i in range(5) if i != primary)
        committed = statuses[node]["commit"]
        self.kill(node)
        directory = self.out / "data" / f"node_{node}"
        pointer = json.loads((directory / "CURRENT").read_text())
        generation = directory / pointer["generation"]
        manifest = json.loads((generation / "manifest.json").read_text())
        descriptions = manifest["storage"]["files"]
        if not descriptions:
            raise AssertionError("corruption exercise requires a published SSTable")
        name = descriptions[0]["name"]
        table = generation / "tables" / name[:2] / name
        before = hashlib.sha256(table.read_bytes()).hexdigest()
        fd = os.open(table, os.O_RDWR)
        try:
            offset = os.fstat(fd).st_size - 1
            os.lseek(fd, offset, os.SEEK_SET)
            old = os.read(fd, 1)
            if len(old) != 1:
                raise AssertionError("cannot select SSTable corruption byte")
            os.lseek(fd, offset, os.SEEK_SET)
            if os.write(fd, bytes([old[0] ^ 1])) != 1:
                raise OSError("short corruption injection write")
            os.fsync(fd)
        finally:
            os.close(fd)
        self.faults["sstable_corruptions"] = self.faults.get("sstable_corruptions", 0) + 1
        self.emit("sstable_corrupted", node=node, path=str(table), offset=offset,
                  before_sha256=before, after_sha256=hashlib.sha256(table.read_bytes()).hexdigest())
        self.start(node)
        self.wait_ready([node])
        statuses = self.settle()
        if statuses[node]["commit"] < committed or statuses[node]["storage"]["quarantined"]:
            raise AssertionError("corrupted replica failed to recover committed state")
        log_path = self.processes[node][2]
        events = []
        for line in log_path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        kinds = {event.get("event") for event in events}
        if not {"journal_quarantined", "state_transfer_recovered"} <= kinds:
            raise AssertionError("quarantine and quorum state-transfer evidence is missing")
        result = {"node": node, "commit": statuses[node]["commit"], "log": str(log_path),
                  "events": [event for event in events if event.get("event") in
                             {"journal_quarantined", "state_transfer_recovered"}]}
        self.emit("quorum_storage_recovery_verified", **result)
        return result

    def verify_retried_results(self):
        statuses = self.settle()
        primary = statuses[0]["primary"]
        grouped = {}
        for history in self.history:
            grouped.setdefault(history["request"]["client"], []).append(history)

        def replay(histories):
            with socket.create_connection(self.targets[primary], timeout=10) as connection:
                connection.settimeout(10)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for history in sorted(histories, key=lambda h: h["request"]["seq"]):
                    request = history["request"]
                    connection.sendall(frame(1000 + request["client"], primary, {"type": "client", "request": request}))
                    while True:
                        reply = read_frame(connection)[2]
                        if reply.get("type") == "reply" and reply["seq"] == request["seq"]:
                            break
                        if reply.get("type") != "reply":
                            raise AssertionError(f"unexpected retry response: {reply}")
                    for field in ("client", "seq", "index", "hash", "request_hash", "result"):
                        if reply[field] != history["reply"][field]:
                            raise AssertionError(f"durable deduplication/result mismatch: {field}")
            return len(histories)

        with ThreadPoolExecutor(max_workers=self.args.clients) as pool:
            count = sum(pool.map(replay, grouped.values()))
        after = self.settle()
        if any(s["commit"] != statuses[0]["commit"] for s in after):
            raise AssertionError("retrying committed identities appended duplicate operations")
        self.emit("durable_deduplication_verified", repeated_requests=count, commit=after[0]["commit"])
        return {"requests": count, "unchanged_commit": after[0]["commit"]}

    def stop_all(self):
        for node in list(self.paused):
            os.kill(self.processes[node][0].pid, signal.SIGCONT)
            self.paused.remove(node)
        for process, _, _ in self.processes.values():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
        for node in list(self.processes):
            self.reap(node, 0, "graceful shutdown")

    def run(self):
        report = {"status": "failed", "configuration": vars(self.args) | {"output": str(self.out)}}
        try:
            self.emit("configuration", **report["configuration"])
            self.proxy = FaultProxy(self.targets, self.ports, self.args.seed)
            for node in range(5):
                self.start(node, bootstrap=True)
            self.wait_ready()
            self.proxy.enabled = self.args.chaos
            pool = ThreadPoolExecutor(max_workers=self.args.clients, thread_name_prefix="live-client")
            try:
                counts = [self.args.requests // self.args.clients + (i < self.args.requests % self.args.clients)
                          for i in range(self.args.clients)]
                futures = [pool.submit(self.client, i, count) for i, count in enumerate(counts)]
                self.chaos(futures)
                for future in futures:
                    future.result()
            finally:
                self.stop.set()
                pool.shutdown(wait=True, cancel_futures=True)
            self.proxy.enabled = False
            self.proxy.blocked = frozenset()
            report["before_restart"] = self.verify("before-restart")
            self.checkpoint_all()
            if self.args.chaos:
                report["quorum_storage_recovery"] = self.exercise_quorum_storage_recovery()
                report["after_quorum_repair"] = self.verify("after-quorum-repair")
            for node in list(self.processes):
                self.kill(node)
            for node in range(5):
                self.start(node)
            self.wait_ready()
            report["after_restart"] = self.verify("after-restart")
            if not all(report["after_restart"]["recovered_sstables"]):
                raise AssertionError("one or more restarted daemons did not load real SSTables")
            for field in ("log_sha256", "sstable_digest"):
                if report["before_restart"][field] != report["after_restart"][field]:
                    raise AssertionError(f"full-cluster crash changed {field}")
            report["durable_deduplication"] = self.verify_retried_results()
            if self.args.chaos and not all(self.faults.values()):
                raise AssertionError("workload finished without exercising every configured OS fault class")
            if self.args.chaos and not all(self.proxy.stats[key] for key in ("partition_drops", "socket_drops", "jittered", "corrupted")):
                raise AssertionError("a required TCP fault class was not exercised")
            self.stop_all()
            report["status"] = "passed"
        except BaseException as error:
            report["error"] = repr(error)
            report["traceback"] = traceback.format_exc()
            self.emit("failure", error=repr(error), traceback=report["traceback"])
        finally:
            self.stop.set()
            for node in list(self.processes):
                process, incarnation, path = self.processes[node]
                if process.poll() is None:
                    if node in self.paused:
                        os.kill(process.pid, signal.SIGCONT)
                    process.terminate()
                    try:
                        process.wait(10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(5)
                result = {"node": node, "pid": process.pid, "incarnation": incarnation,
                          "exit_status": process.returncode, "reason": "failure cleanup", "log": str(path)}
                self.exits.append(result)
                self.emit("process_exit", **result)
            if self.proxy is not None:
                try:
                    self.proxy.close()
                except Exception as error:
                    report["proxy_error"], report["status"] = repr(error), "failed"
            report.update(completed=self.completed, retries=self.retries, faults=self.faults,
                          proxy={} if self.proxy is None else self.proxy.stats, processes=self.exits,
                          elapsed_seconds=round(time.monotonic() - self.started, 3))
            (self.out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            self.emit("result", status=report["status"], completed=self.completed,
                      report=str(self.out / "report.json"), faults=self.faults)
            self.history_file.close()
            self.transcript.close()
        return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new directory for data, logs and evidence")
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--clients", type=int, default=16)
    parser.add_argument("--base-port", type=int, default=18001)
    parser.add_argument("--proxy-port", type=int, default=19001)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--client-delay", type=float, default=0.005)
    parser.add_argument("--chaos", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.requests < 16 or not 1 <= args.clients <= 64 or args.timeout <= 0 or args.client_delay < 0:
        parser.error("invalid workload bounds")
    return Supervisor(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
