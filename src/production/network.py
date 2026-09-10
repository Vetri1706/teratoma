"""Fixed native OS-thread pool with real nonblocking TCP and atomic mailboxes."""
from collections import deque
import errno
import selectors
import socket
import threading
from .channels import NativeChannel
from .framing import Decoder, FrameError, OutboundMessage

MAX_PENDING_BYTES = 256 * 1024 * 1024
WRITE_WINDOW_BYTES = 1024 * 1024


class IOWorker:
    def __init__(self, index):
        self.index = index
        self.commands = NativeChannel()
        self.ingress = NativeChannel()
        self.stopping = threading.Event()
        self.selector = selectors.DefaultSelector()
        self.connections = {}
        self.peers = {}
        self.serial = 0
        self.released_bytes = 0
        self.stats = {"received": 0, "buffers_sent": 0, "crc_errors": 0,
                      "disconnects": 0, "ingress_drops": 0, "outbound_drops": 0}
        self.thread = threading.Thread(target=self._run, name=f"aegis-net-{index}", daemon=True)
        self.thread.start()

    def command(self, value):
        return self.commands.put(value)

    def _add(self, sock, connecting=False, peer=None):
        self.serial += 1
        connection = {"socket": sock, "key": self.serial, "decoder": Decoder(),
                      "out": deque(), "bytes": 0, "pending": deque(), "window_bytes": 0,
                      "connecting": connecting, "peer": peer}
        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connections[self.serial] = connection
        self.selector.register(sock, selectors.EVENT_READ | (selectors.EVENT_WRITE if connecting else 0), connection)
        if peer is not None:
            self.peers[peer] = connection
        return connection

    def _close_connection(self, connection):
        if self.connections.pop(connection["key"], None) is None:
            return
        self.released_bytes += connection["bytes"]
        connection["bytes"] = connection["window_bytes"] = 0
        connection["out"].clear()
        connection["pending"].clear()
        if connection["peer"] is not None:
            self.peers.pop(connection["peer"], None)
        try:
            self.selector.unregister(connection["socket"])
        except (KeyError, ValueError):
            self.stats["unregister_errors"] = self.stats.get("unregister_errors", 0) + 1
        connection["socket"].close()
        self.stats["disconnects"] += 1

    def _enqueue(self, connection, wire):
        if not isinstance(wire, OutboundMessage):
            raise TypeError("reactor requires a lazy OutboundMessage")
        if connection["bytes"] + wire.wire_size > MAX_PENDING_BYTES:
            self.stats["outbound_drops"] += 1
            self.released_bytes += wire.wire_size
            self._close_connection(connection)
            return
        connection["pending"].append(iter(wire.iter_buffers()))
        connection["bytes"] += wire.wire_size
        self._fill_window(connection)
        self.selector.modify(connection["socket"], selectors.EVENT_READ | selectors.EVENT_WRITE, connection)

    def _fill_window(self, connection):
        while connection["pending"] and connection["window_bytes"] < WRITE_WINDOW_BYTES:
            try:
                raw = next(connection["pending"][0])
            except StopIteration:
                connection["pending"].popleft()
                continue
            connection["out"].append(raw if isinstance(raw, memoryview) else memoryview(raw))
            connection["window_bytes"] += len(raw)

    def _process_commands(self):
        for _ in range(256):
            command = self.commands.get()
            if command is None:
                break
            kind = command[0]
            if kind == "accept":
                self._add(command[1])
            elif kind == "reply":
                connection = self.connections.get(command[1])
                if connection is not None:
                    self._enqueue(connection, command[2])
                else:
                    self.released_bytes += command[2].wire_size
            elif kind == "send":
                _, peer, address, wire = command
                connection = self.peers.get(peer)
                if connection is None:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setblocking(False)
                    result = sock.connect_ex(address)
                    if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
                        sock.close()
                        self.stats["outbound_drops"] += 1
                        self.released_bytes += wire.wire_size
                        continue
                    connection = self._add(sock, connecting=result != 0, peer=peer)
                self._enqueue(connection, wire)
            else:
                raise RuntimeError("unknown I/O worker command")

    def _ready(self, connection, mask):
        sock = connection["socket"]
        if connection["connecting"] and mask & selectors.EVENT_WRITE:
            error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if error:
                raise OSError(error, "TCP connect failed")
            connection["connecting"] = False
        if mask & selectors.EVENT_READ and not connection["connecting"]:
            try:
                data = sock.recv(65536)
            except BlockingIOError:
                data = None
            if data == b"":
                raise EOFError("peer disconnected")
            if data:
                for source, target, payload in connection["decoder"].feed(data):
                    self.stats["received"] += 1
                    if not self.ingress.put((self.index, connection["key"], source, target, payload)):
                        self.stats["ingress_drops"] += 1
        if mask & selectors.EVENT_WRITE and not connection["connecting"]:
            for _ in range(64):
                self._fill_window(connection)
                if not connection["out"]:
                    break
                view = connection["out"][0]
                try:
                    sent = sock.send(view)
                except BlockingIOError:
                    break
                if sent == 0:
                    raise EOFError("socket closed while sending")
                connection["bytes"] -= sent
                connection["window_bytes"] -= sent
                self.released_bytes += sent
                if sent < len(view):
                    connection["out"][0] = view[sent:]
                    break
                connection["out"].popleft()
                self.stats["buffers_sent"] += 1
            if not connection["out"] and not connection["pending"]:
                self.selector.modify(sock, selectors.EVENT_READ, connection)

    def _run(self):
        try:
            while not self.stopping.is_set():
                self._process_commands()
                for key, mask in self.selector.select(0.002):
                    connection = key.data
                    try:
                        self._ready(connection, mask)
                    except (FrameError, ValueError, UnicodeError, RecursionError):
                        self.stats["crc_errors"] += 1
                        self._close_connection(connection)
                    except (OSError, EOFError):
                        self._close_connection(connection)
                if self.released_bytes:
                    released = self.released_bytes
                    if self.ingress.put((self.index, 0, -2, -2, {"released": released})):
                        self.released_bytes = 0
        except BaseException as error:
            self.ingress.put((self.index, 0, -1, -1, {"fatal": repr(error)}))
        finally:
            for connection in list(self.connections.values()):
                self._close_connection(connection)
            self.selector.close()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("network worker failed to stop")
        self.commands.close()
        self.ingress.close()


class TCPNetwork:
    def __init__(self, node_id, bind, peers, env, workers=4):
        self.id, self.peers, self.env = node_id, peers, env
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(bind)
        self.listener.listen(256)
        self.listener.setblocking(False)
        self.workers = [IOWorker(i) for i in range(workers)]
        self.pending = [deque() for _ in self.workers]
        self.outstanding_bytes = [0 for _ in self.workers]
        self.clients = {}
        self.next_worker = 0
        self.handler = None
        self.control = None
        self.received = self.sent = self.dropped = 0

    def register(self, node_id, handler):
        if node_id != self.id:
            raise ValueError("one replica per daemon process")
        self.handler = handler

    def _post(self, index, command):
        cost = command[-1].wire_size
        if cost > MAX_PENDING_BYTES - self.outstanding_bytes[index] or len(self.pending[index]) >= 32768:
            self.dropped += 1
            return False
        self.outstanding_bytes[index] += cost
        self.pending[index].append(command)
        return True

    def _flush_pending(self):
        for index, worker in enumerate(self.workers):
            for _ in range(64):
                if not self.pending[index] or not worker.command(self.pending[index][0]):
                    break
                self.pending[index].popleft()

    def send(self, source, target, payload):
        self.sent += 1
        if target == self.id:
            self.env.schedule(0, lambda: self.handler(source, payload), "local-protocol")
            return
        wire = OutboundMessage(source, target, payload)
        if target in self.peers:
            worker = self.workers[target % len(self.workers)]
            self._post(worker.index, ("send", target, tuple(self.peers[target]), wire))
        elif target in self.clients:
            worker, connection = self.clients[target]
            self._post(worker, ("reply", connection, wire))
        else:
            self.dropped += 1

    def poll(self):
        self._flush_pending()
        for _ in range(64):
            try:
                sock, _ = self.listener.accept()
            except BlockingIOError:
                break
            worker = self.workers[self.next_worker % len(self.workers)]
            self.next_worker += 1
            if not worker.command(("accept", sock)):
                sock.close()
                self.dropped += 1
        for worker in self.workers:
            if not worker.thread.is_alive():
                raise RuntimeError("network worker exited unexpectedly")
            for _ in range(256):
                message = worker.ingress.get()
                if message is None:
                    break
                index, connection, source, target, payload = message
                if source == -2:
                    released = payload["released"]
                    if not 0 <= released <= self.outstanding_bytes[index]:
                        raise AssertionError("outbound queue credit mismatch")
                    self.outstanding_bytes[index] -= released
                    continue
                if source == -1:
                    raise RuntimeError(payload["fatal"])
                if target != self.id:
                    self.dropped += 1
                    continue
                self.received += 1
                if source >= 999:
                    self.clients[source] = (index, connection)
                if payload.get("type") in {"status", "audit", "checkpoint"}:
                    if source != 999:
                        self.dropped += 1
                        continue
                    result = self.control(payload)
                    try:
                        response = OutboundMessage(self.id, source, result)
                    except FrameError as error:
                        response = OutboundMessage(self.id, source, {"type": "error", "error": str(error)})
                    self._post(index, ("reply", connection, response))
                elif self.handler is not None:
                    self.handler(source, payload)

    def stats(self):
        return {"received": self.received, "sent": self.sent, "dropped": self.dropped,
                "outstanding_bytes": list(self.outstanding_bytes), "dispatch_backlog": [len(q) for q in self.pending],
                "workers": [dict(worker.stats) for worker in self.workers]}

    def close(self):
        self.listener.close()
        for worker in self.workers:
            worker.close()
        for pending in self.pending:
            pending.clear()
