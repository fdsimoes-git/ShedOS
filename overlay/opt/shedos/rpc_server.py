"""Line-delimited JSON RPC server over a Unix socket.

Protocol (line-delimited JSON):
    Request:  {"req_id": 1, "method": "sessions.send",
               "params": {"id": "abc", "text": "hello"}}\n
    Response: {"req_id": 1, "event": "assistant_text", "chunk": "hi"}\n
              {"req_id": 1, "event": "tool_use", "name": "bash", ...}\n
              {"req_id": 1, "event": "end_turn"}\n
              ...

For non-streaming methods (sessions.list, sessions.create, etc), one
response is sent: {"req_id": N, "ok": true, "result": ...} or
{"req_id": N, "ok": false, "error": "msg"}.

Multiple clients can be connected simultaneously. Each client operates
independently against shared SessionManager state.
"""
import asyncio
import json
import os
import sys

import config
from sessions import SessionManager


SOCK_PATH = "/run/shedos-brain.sock"


class RpcServer:
    def __init__(self, brain_handler, sock_path=SOCK_PATH):
        self.handler = brain_handler  # async callable for sessions.send
        self.sock_path = sock_path
        self.manager = SessionManager()
        # Per-session asyncio.Lock so concurrent sessions.send calls from
        # different clients (e.g. GUI + TUI) don't interleave turns on the
        # same Session.messages list. Created lazily; cleaned up on delete.
        self._send_locks = {}

    def _send_lock(self, sid):
        lock = self._send_locks.get(sid)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[sid] = lock
        return lock

    async def start(self):
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(self.sock_path), exist_ok=True)
        server = await asyncio.start_unix_server(
            self._handle_client, path=self.sock_path
        )
        os.chmod(self.sock_path, 0o600)
        sys.stderr.write(f"[brain] rpc listening on {self.sock_path}\n")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader, writer):
        addr = writer.get_extra_info("peername")
        sys.stderr.write(f"[brain] client connected {addr}\n")
        # Detect protocol mode by peeking the first byte.
        try:
            first = await reader.readexactly(1)
        except asyncio.IncompleteReadError:
            writer.close()
            return
        if first == b"{":
            # JSON-RPC mode
            await self._serve_json(reader, writer, prefix=first)
        else:
            # Legacy text mode (one session, no streaming events)
            await self._serve_legacy(reader, writer, prefix=first)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    async def _serve_json(self, reader, writer, prefix=b""):
        buf = prefix
        while True:
            try:
                chunk = await reader.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    await self._reply_err(writer, None, f"parse: {e}")
                    continue
                asyncio.create_task(self._dispatch(writer, msg))

    async def _dispatch(self, writer, msg):
        req_id = msg.get("req_id")
        method = msg.get("method")
        params = msg.get("params") or {}
        try:
            if method == "sessions.list":
                await self._reply_ok(writer, req_id,
                                      {"sessions": self.manager.list()})
            elif method == "sessions.create":
                s = self.manager.create(title=params.get("title", "New chat"))
                await self._reply_ok(writer, req_id, s.info())
            elif method == "sessions.delete":
                sid = params.get("id")
                # Wait for any in-flight send on this session to finish
                # before unlinking the JSONL — otherwise the producer
                # would keep appending to a half-deleted session and
                # recreate an orphan log without metadata.
                lock = self._send_locks.get(sid)
                if lock is not None:
                    async with lock:
                        ok = self.manager.delete(sid)
                else:
                    ok = self.manager.delete(sid)
                self._send_locks.pop(sid, None)
                await self._reply_ok(writer, req_id, {"deleted": ok})
            elif method == "sessions.history":
                s = self.manager.get(params.get("id"))
                if s is None:
                    await self._reply_err(writer, req_id, "no such session")
                else:
                    await self._reply_ok(writer, req_id,
                                          {"messages": s.messages,
                                           "info": s.info()})
            elif method == "sessions.set_title":
                s = self.manager.get(params.get("id"))
                if s is None:
                    await self._reply_err(writer, req_id, "no such session")
                else:
                    s.set_title(params.get("title", s.title))
                    await self._reply_ok(writer, req_id, s.info())
            elif method == "sessions.send":
                s = self.manager.get(params.get("id"))
                if s is None:
                    await self._reply_err(writer, req_id, "no such session")
                    return
                # Serialize turns on a single session — without this lock,
                # two concurrent sends would both mutate s.messages and
                # send overlapping Anthropic requests built on a shifting
                # message list.
                async with self._send_lock(s.id):
                    async for event in self.handler(s, params.get("text", "")):
                        event["req_id"] = req_id
                        await self._send_line(writer, event)
                    await self._send_line(writer, {"req_id": req_id, "event": "done"})
            elif method == "ping":
                await self._reply_ok(writer, req_id, {"pong": True})
            else:
                await self._reply_err(writer, req_id, f"unknown method: {method}")
        except Exception as e:
            await self._reply_err(writer, req_id, f"{type(e).__name__}: {e}")

    async def _serve_legacy(self, reader, writer, prefix=b""):
        """Raw text mode for `nc -U`. One session, line-buffered I/O."""
        s = self.manager.get_or_create_default()

        async def writeln(text):
            try:
                writer.write((text + "\n").encode("utf-8", "replace"))
                await writer.drain()
            except Exception:
                pass

        await writeln("ShedOS legacy text client.")
        await writeln("(For the full TUI, run `make tui` from your Mac.)")
        await writeln(f"Session: {s.title} ({s.id})")
        await writeln("> ")

        buf = prefix
        while True:
            try:
                chunk = await reader.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    await writeln("> ")
                    continue
                try:
                    async for event in self.handler(s, text):
                        et = event.get("event")
                        if et == "assistant_text":
                            await writeln(event.get("chunk", ""))
                        elif et == "tool_use":
                            await writeln(
                                f"[tool: {event.get('name')}({event.get('input_summary','')})]"
                            )
                        elif et == "tool_result":
                            pass  # legacy mode hides raw tool output
                        elif et == "error":
                            await writeln(f"[error] {event.get('msg')}")
                except Exception as e:
                    await writeln(f"[error] {type(e).__name__}: {e}")
                await writeln("> ")

    async def _send_line(self, writer, obj):
        try:
            writer.write((json.dumps(obj) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception:
            pass

    async def _reply_ok(self, writer, req_id, result):
        await self._send_line(writer, {"req_id": req_id, "ok": True, "result": result})

    async def _reply_err(self, writer, req_id, msg):
        await self._send_line(writer, {"req_id": req_id, "ok": False, "error": msg})
