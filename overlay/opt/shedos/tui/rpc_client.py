"""Sync wrapper around the brain's JSON-RPC socket.

Used by the TUI which is otherwise prompt_toolkit-driven (sync). For
streaming methods (sessions.send), provides a generator that yields
each event as the daemon writes it.
"""
import json
import socket

SOCK_PATH = "/run/shedos-brain.sock"


class RpcError(Exception):
    pass


class RpcClient:
    def __init__(self, sock_path=SOCK_PATH):
        self.sock_path = sock_path
        self.sock = None
        self._buf = b""
        self._req_id = 0

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        self.sock = s
        return self

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def _send(self, msg):
        line = (json.dumps(msg) + "\n").encode("utf-8")
        self.sock.sendall(line)

    def _recv_line(self):
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RpcError("daemon closed connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        try:
            return json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise RpcError(f"bad line from daemon: {e}")

    # --- Non-streaming calls ---------------------------------------------------

    def call(self, method, **params):
        req_id = self._next_id()
        self._send({"req_id": req_id, "method": method, "params": params})
        while True:
            msg = self._recv_line()
            if msg.get("req_id") != req_id:
                # event from a different in-flight call — ignore for sync use
                continue
            if msg.get("ok") is True:
                return msg.get("result")
            if msg.get("ok") is False:
                raise RpcError(msg.get("error", "unknown error"))
            # streaming event sneaking onto a non-streaming call — ignore
            if msg.get("event") == "done":
                return None

    # --- Streaming sessions.send ----------------------------------------------

    def send(self, session_id, text):
        """Yield events from sessions.send until 'done' arrives."""
        req_id = self._next_id()
        self._send({
            "req_id": req_id,
            "method": "sessions.send",
            "params": {"id": session_id, "text": text},
        })
        while True:
            msg = self._recv_line()
            if msg.get("req_id") != req_id:
                continue
            if msg.get("ok") is False:
                raise RpcError(msg.get("error", "send failed"))
            ev = msg.get("event")
            if ev == "done":
                return
            yield msg

    # --- Sugar ----------------------------------------------------------------

    def list_sessions(self):
        return self.call("sessions.list").get("sessions", [])

    def create_session(self, title="New chat"):
        return self.call("sessions.create", title=title)

    def delete_session(self, sid):
        return self.call("sessions.delete", id=sid)

    def history(self, sid):
        return self.call("sessions.history", id=sid)

    def set_title(self, sid, title):
        return self.call("sessions.set_title", id=sid, title=title)

    def ping(self):
        return self.call("ping")
