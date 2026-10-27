"""RFC 1413 responder restricted to FluxFTP's direct control connections."""
import re
import selectors
import socket
import threading
import time


class IdentService:
    def __init__(self):
        self.lock = threading.RLock()
        self.registration = threading.Condition()
        self.enabled = False
        self.only_connecting = True
        self.name, self.system, self.port = "FluxFTP", "OTHER", 113
        self.connecting = set()
        self.connections = {}
        self.listener = None
        self.thread = None

    def configure(self, enabled=False, only_connecting=True, name="FluxFTP", system="OTHER", port=113):
        name, system = name.strip(), system.strip()
        if not name or len(name) > 128 or any(not 32 <= ord(c) <= 126 for c in name):
            raise ValueError("Ident name must contain 1–128 printable ASCII characters.")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", system):
            raise ValueError("Ident system must contain 1–64 letters, digits or hyphens (for example OTHER or UNIX).")
        if not 1 <= int(port) <= 65535:
            raise ValueError("Ident port must be between 1 and 65535.")
        with self.lock:
            if self.port != int(port):
                self._stop()
            self.enabled, self.only_connecting = enabled, only_connecting
            self.name, self.system, self.port = name, system, int(port)
            self._sync()

    def begin(self, owner):
        with self.lock:
            self.connecting.add(owner)
            try:
                self._sync()
            except OSError:
                self.connecting.discard(owner)
                raise

    def register(self, owner, sock):
        with self.lock:
            with self.registration:
                self.connections[owner] = (sock.getsockname()[:2], sock.getpeername()[:2])
                self.registration.notify_all()

    def ready(self, owner):
        with self.lock:
            self.connecting.discard(owner)
            self._sync()

    def release(self, owner):
        with self.lock:
            self.connecting.discard(owner)
            self.connections.pop(owner, None)
            if self.only_connecting and not self.connecting:
                self._stop()

    def close(self):
        with self.lock:
            self.enabled = False
            self.connecting.clear()
            self.connections.clear()
            self._stop()

    def _sync(self):
        wanted = self.enabled and (not self.only_connecting or bool(self.connecting))
        if not wanted:
            self._stop()
        elif self.listener is None:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind(("0.0.0.0", self.port))
                listener.listen(16)
                listener.setblocking(False)
            except OSError as error:
                listener.close()
                raise OSError(f"Cannot start Ident on TCP port {self.port}: {error}") from error
            self.listener = listener
            self.stopping = threading.Event()
            self.thread = threading.Thread(target=self._serve, args=(listener, self.stopping), daemon=True)
            self.thread.start()

    def _stop(self):
        if self.listener is not None:
            self.stopping.set()
            with self.registration:
                self.registration.notify_all()
            self.thread.join()
            self.listener = self.thread = None

    def response(self, query, local_ip, peer_ip):
        match = re.fullmatch(rb"[ \t]*([0-9]{1,5})[ \t]*,[ \t]*([0-9]{1,5})[ \t]*\r\n", query)
        if not match:
            return b"0 , 0 : ERROR : INVALID-PORT\r\n"
        local_port, peer_port = map(int, match.groups())
        pair = f"{local_port} , {peer_port}"
        if not 1 <= local_port <= 65535 or not 1 <= peer_port <= 65535:
            return f"{pair} : ERROR : INVALID-PORT\r\n".encode("ascii")
        # A peer can query as soon as TCP connects, before the connecting
        # thread has returned from connect() and registered its endpoints.
        # Give that registration a bounded grace period without holding the
        # lifecycle lock, which register() and shutdown need.
        endpoints_wanted = ((local_ip, local_port), (peer_ip, peer_port))
        with self.registration:
            self.registration.wait_for(
                lambda: endpoints_wanted in self.connections.copy().values()
                or not (self.connecting.copy() - self.connections.copy().keys())
                or (getattr(self, "stopping", None) is not None and self.stopping.is_set()),
                timeout=0.5,
            )
        # Snapshot without acquiring the lifecycle lock: shutdown joins this thread.
        records = self.connections.copy()
        connecting = self.connecting.copy()
        for owner, endpoints in records.items():
            if endpoints == ((local_ip, local_port), (peer_ip, peer_port)) and self.enabled:
                if not self.only_connecting or owner in connecting:
                    from .site_options import decode
                    settings = decode(getattr(getattr(owner, 'site', None), 'site_options', '{}'))
                    system = settings.get('Connection/ident_system') or self.system
                    name = settings.get('Connection/ident_name') or self.name
                    return f"{pair} : USERID : {system} :{name}\r\n".encode("ascii")
        return f"{pair} : ERROR : NO-USER\r\n".encode("ascii")

    def _serve(self, listener, stopping):
        clients = {}
        with selectors.DefaultSelector() as selector:
            selector.register(listener, selectors.EVENT_READ)
            try:
                while not stopping.is_set():
                    for key, _ in selector.select(0.1):
                        sock = key.fileobj
                        if sock is listener:
                            client, _ = listener.accept()
                            if len(clients) >= 32:
                                client.close()
                                continue
                            client.setblocking(False)
                            clients[client] = [b"", time.monotonic()]
                            selector.register(client, selectors.EVENT_READ)
                            continue
                        try:
                            data = sock.recv(512)
                            clients[sock][0] += data
                            query = clients[sock][0]
                            if data and b"\n" not in query and len(query) <= 512:
                                continue
                            if data and len(query) <= 512:
                                reply = self.response(query, sock.getsockname()[0], sock.getpeername()[0])
                                sock.settimeout(0.1)
                                sock.sendall(reply)
                        except OSError:
                            pass
                        selector.unregister(sock)
                        clients.pop(sock)
                        sock.close()
                    for sock, (_, started) in list(clients.items()):
                        if time.monotonic() - started > 60:
                            selector.unregister(sock)
                            clients.pop(sock)
                            sock.close()
            finally:
                for sock in clients:
                    sock.close()
                listener.close()
