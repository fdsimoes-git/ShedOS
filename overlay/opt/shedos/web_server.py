#!/usr/bin/env python3
"""ShedOS web GUI server.

Serves the SPA at /opt/shedos/web/ and bridges the browser frontend to
the brain daemon over a WebSocket. Each WS frame is a JSON message; the
server forwards user prompts to the brain's Unix-socket RPC and streams
events back as WS frames.

Endpoints:
    GET  /                         -> index.html
    GET  /static/<path>           -> static assets
    GET  /api/sessions             -> list sessions
    POST /api/sessions             -> create session
    DEL  /api/sessions/<id>        -> delete session
    GET  /api/sessions/<id>/messages -> history
    PUT  /api/sessions/<id>/title  -> rename
    WS   /ws                       -> bidirectional event stream
"""
import asyncio
import json
import os
import socket
import sys

from aiohttp import WSMsgType, web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
RENDER_DIR = "/var/lib/shedos/render"
BRAIN_SOCK = "/run/shedos-brain.sock"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8080
# Single source of truth for the ShedOS version lives in
# /etc/shedos/version (written by build.sh from config/version) so we
# can't drift between the wizard's banner and the Settings UI's display.
SHEDOS_VERSION = config.shedos_version()


class BrainClient:
    """Async wrapper around the brain's line-delimited JSON RPC."""

    def __init__(self, sock_path=BRAIN_SOCK):
        self.sock_path = sock_path
        self.reader = None
        self.writer = None
        self._req_id = 0
        self._lock = asyncio.Lock()

    async def connect(self):
        for _ in range(60):
            try:
                # 16MiB limit so big sessions.history responses (long
                # conversations serialize the full message list as ONE
                # JSON line) don't trip readline's default 64KB cap.
                self.reader, self.writer = await asyncio.open_unix_connection(
                    self.sock_path, limit=16 * 1024 * 1024
                )
                return
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(1)
        raise RuntimeError(f"could not connect to {self.sock_path}")

    async def close(self):
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    async def _send(self, msg):
        line = (json.dumps(msg) + "\n").encode("utf-8")
        self.writer.write(line)
        await self.writer.drain()

    async def _drop_connection(self):
        if self.writer is not None:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.reader = None
        self.writer = None

    async def _ensure_connected(self):
        # If a previous request hit EOF (brain restart, supervised respawn)
        # the reader returns empty forever. Drop and reconnect so the next
        # request goes through instead of failing for the lifetime of
        # shedos-web.
        if self.reader is None or self.writer is None or self.reader.at_eof():
            await self._drop_connection()
            await self.connect()

    async def call(self, method, **params):
        async with self._lock:
            await self._ensure_connected()
            req_id = self._next_id()
            try:
                await self._send({"req_id": req_id, "method": method,
                                  "params": params})
                while True:
                    line = await self.reader.readline()
                    if not line:
                        await self._drop_connection()
                        raise RuntimeError("brain disconnected")
                    msg = json.loads(line.decode("utf-8"))
                    if msg.get("req_id") != req_id:
                        continue
                    if msg.get("ok"):
                        return msg.get("result")
                    if msg.get("ok") is False:
                        raise RuntimeError(msg.get("error", "rpc error"))
                    if msg.get("event") == "done":
                        return None
            except (BrokenPipeError, ConnectionResetError) as e:
                await self._drop_connection()
                raise RuntimeError(f"brain disconnected: {e}")

    async def stream_send(self, sid, text):
        """Yield events from sessions.send until done."""
        async with self._lock:
            await self._ensure_connected()
            req_id = self._next_id()
            try:
                await self._send({"req_id": req_id, "method": "sessions.send",
                                  "params": {"id": sid, "text": text}})
                while True:
                    line = await self.reader.readline()
                    if not line:
                        await self._drop_connection()
                        raise RuntimeError("brain disconnected")
                    msg = json.loads(line.decode("utf-8"))
                    if msg.get("req_id") != req_id:
                        continue
                    if msg.get("ok") is False:
                        raise RuntimeError(msg.get("error", "send failed"))
                    if msg.get("event") == "done":
                        return
                    yield msg
            except (BrokenPipeError, ConnectionResetError) as e:
                await self._drop_connection()
                raise RuntimeError(f"brain disconnected: {e}")


# ---- HTTP handlers ---------------------------------------------------------

async def handle_index(request):
    return web.FileResponse(os.path.join(WEB_DIR, "index.html"))


async def handle_sessions_list(request):
    brain = request.app["brain"]
    res = await brain.call("sessions.list")
    return web.json_response(res)


async def handle_sessions_create(request):
    brain = request.app["brain"]
    body = await request.json() if request.body_exists else {}
    res = await brain.call("sessions.create", title=body.get("title", "New chat"))
    return web.json_response(res)


async def handle_sessions_delete(request):
    brain = request.app["brain"]
    sid = request.match_info["sid"]
    res = await brain.call("sessions.delete", id=sid)
    return web.json_response(res)


async def handle_sessions_history(request):
    brain = request.app["brain"]
    sid = request.match_info["sid"]
    res = await brain.call("sessions.history", id=sid)
    return web.json_response(res)


async def handle_sessions_title(request):
    brain = request.app["brain"]
    sid = request.match_info["sid"]
    body = await request.json()
    res = await brain.call("sessions.set_title", id=sid,
                           title=body.get("title", ""))
    return web.json_response(res)


def _primary_ip():
    """Best-effort: find the LAN IP without resolving DNS."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # connecting to a UDP socket doesn't actually send anything
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def handle_settings_get(request):
    persona_choice = config.load_persona_choice()
    persona_text = config.load_persona()
    style = config.load_style()
    try:
        host = socket.gethostname()
    except Exception:
        host = "shedos"
    # `available` lists what PUT /api/settings will accept for the
    # `persona` field. "custom" is intentionally excluded — to use a
    # custom persona the user writes /etc/shedos/persona.txt directly
    # (via SSH); it's surfaced in `active` so the UI can show that the
    # current persona came from that file, not from a preset choice.
    return web.json_response({
        "persona": {
            "active": persona_choice,
            "available": list(config.PERSONA_PRESETS),
            "text": persona_text,
        },
        "style": style,
        "system": {
            "version": SHEDOS_VERSION,
            "hostname": host,
            "ip": _primary_ip(),
        },
    })


async def handle_settings_put(request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        return web.json_response(
            {"error": "request body is not valid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response(
            {"error": "request body must be a JSON object"}, status=400)

    # Validate every provided field BEFORE writing anything, so a bad
    # `persona` doesn't half-apply alongside a good `style` (which would
    # leave the system in an inconsistent state and still return 400).
    style_in = body.get("style", None)
    persona_in = body.get("persona", None)
    if "style" in body and not isinstance(style_in, dict):
        return web.json_response(
            {"error": "`style` must be an object"}, status=400)
    if "persona" in body and not isinstance(persona_in, str):
        return web.json_response(
            {"error": "`persona` must be a string"}, status=400)
    if isinstance(persona_in, str) and persona_in not in config.PERSONA_PRESETS:
        return web.json_response(
            {"error": f"unknown persona preset: {persona_in!r}"},
            status=400)
    if not isinstance(style_in, dict) and not isinstance(persona_in, str):
        return web.json_response(
            {"error": "no recognised field; expected `style` or `persona`"},
            status=400)

    # All inputs valid — apply.
    out = {}
    if isinstance(style_in, dict):
        out["style"] = config.save_style(style_in)
    if isinstance(persona_in, str):
        config.save_persona_choice(persona_in)
        out["persona"] = config.load_persona_choice()
    return web.json_response(out)


async def handle_ws(request):
    """WebSocket: bidirectional event stream.

    Client → server: {"type": "send", "session_id": "...", "text": "..."}
                     {"type": "ping"}
    Server → client: {"event": "...", ...}  (forwarded brain events)
                     {"type": "pong"}
                     {"type": "error", "msg": "..."}
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    brain = request.app["brain"]

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            req = json.loads(msg.data)
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "msg": "bad json"})
            continue

        kind = req.get("type")
        if kind == "ping":
            await ws.send_json({"type": "pong"})
        elif kind == "send":
            sid = req.get("session_id")
            text = req.get("text", "")
            try:
                async for ev in brain.stream_send(sid, text):
                    await ws.send_json(ev)
            except Exception as e:
                await ws.send_json({"event": "error",
                                    "msg": f"{type(e).__name__}: {e}"})
            finally:
                # Always emit _stream_done so the frontend re-enables the
                # composer even when the brain raises (deleted session,
                # daemon restart, etc).
                await ws.send_json({"event": "_stream_done"})
        else:
            await ws.send_json({"type": "error",
                                "msg": f"unknown type: {kind}"})

    return ws


def make_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_post("/api/sessions", handle_sessions_create)
    app.router.add_delete("/api/sessions/{sid}", handle_sessions_delete)
    app.router.add_get("/api/sessions/{sid}/messages", handle_sessions_history)
    app.router.add_put("/api/sessions/{sid}/title", handle_sessions_title)
    app.router.add_get("/api/settings", handle_settings_get)
    app.router.add_put("/api/settings", handle_settings_put)
    app.router.add_get("/ws", handle_ws)
    if os.path.isdir(WEB_DIR):
        app.router.add_static("/static", WEB_DIR)
    # Render assets (images, PDFs, etc.) live under /var/lib/shedos/render
    # and are served at /render/<asset_id>/<filename>. Brain's render_*
    # tools stage files there.
    os.makedirs(RENDER_DIR, exist_ok=True)
    app.router.add_static("/render", RENDER_DIR, show_index=False)
    return app


async def amain():
    app = make_app()
    brain = BrainClient()
    await brain.connect()
    app["brain"] = brain
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    sys.stderr.write(f"[shedos-web] listening on http://{LISTEN_HOST}:{LISTEN_PORT}\n")
    while True:
        await asyncio.sleep(3600)


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
