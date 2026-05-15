import base64
import hashlib
import os
import shutil
import stat
import subprocess
import time
import urllib.parse

import httpx

MAX_STREAM = 16 * 1024
MAX_FILE_READ = 64 * 1024
MAX_FETCH_BODY = 256 * 1024
RENDER_DIR = "/var/lib/shedos/render"


def _truncate(s, limit=MAX_STREAM):
    if not isinstance(s, str):
        try:
            s = s.decode("utf-8", errors="replace")
        except Exception:
            s = str(s)
    if len(s) <= limit:
        return s, False
    return s[:limit], True


def tool_bash(command, timeout_s=30):
    try:
        r = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            timeout=float(timeout_s),
        )
    except subprocess.TimeoutExpired as e:
        out, _ = _truncate((e.stdout or b"").decode("utf-8", "replace"))
        err, _ = _truncate((e.stderr or b"").decode("utf-8", "replace"))
        return {
            "stdout": out,
            "stderr": err,
            "exit_code": -1,
            "timed_out": True,
            "truncated": False,
        }
    out, out_trunc = _truncate(r.stdout.decode("utf-8", "replace"))
    err, err_trunc = _truncate(r.stderr.decode("utf-8", "replace"))
    return {
        "stdout": out,
        "stderr": err,
        "exit_code": r.returncode,
        "timed_out": False,
        "truncated": out_trunc or err_trunc,
    }


def tool_read_file(path):
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_FILE_READ + 1)
    except FileNotFoundError:
        return {"error": "not_found", "path": path}
    except IsADirectoryError:
        return {"error": "is_directory", "path": path}
    except PermissionError:
        return {"error": "permission_denied", "path": path}
    truncated = len(data) > MAX_FILE_READ
    if truncated:
        data = data[:MAX_FILE_READ]
    try:
        content = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = base64.b64encode(data).decode("ascii")
        encoding = "base64"
    return {
        "content": content,
        "encoding": encoding,
        "bytes": len(data),
        "truncated": truncated,
    }


def tool_write_file(path, content, encoding="utf-8"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if encoding == "base64":
        data = base64.b64decode(content)
    else:
        data = content.encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return {"bytes_written": len(data), "path": path}


def _mode_str(m):
    return stat.filemode(m)


def tool_list_dir(path):
    try:
        names = sorted(os.listdir(path))
    except FileNotFoundError:
        return {"error": "not_found", "path": path}
    except NotADirectoryError:
        return {"error": "not_a_directory", "path": path}
    except PermissionError:
        return {"error": "permission_denied", "path": path}
    entries = []
    for name in names:
        full = os.path.join(path, name)
        try:
            st = os.lstat(full)
        except OSError as e:
            entries.append({"name": name, "error": str(e)})
            continue
        if stat.S_ISDIR(st.st_mode):
            kind = "dir"
        elif stat.S_ISLNK(st.st_mode):
            kind = "symlink"
        elif stat.S_ISREG(st.st_mode):
            kind = "file"
        else:
            kind = "other"
        entries.append(
            {
                "name": name,
                "type": kind,
                "size": st.st_size,
                "mode": _mode_str(st.st_mode),
            }
        )
    return {"path": path, "entries": entries}


def tool_apk(args):
    return tool_bash(f"apk {args}", timeout_s=120)


def tool_process_list():
    r = subprocess.run(
        ["ps", "-eo", "pid,ppid,user,comm,args"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    lines = r.stdout.splitlines()
    if not lines:
        return {"processes": []}
    procs = []
    for line in lines[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        procs.append(
            {
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "user": parts[2],
                "comm": parts[3],
                "args": parts[4],
            }
        )
    return {"processes": procs}


def tool_process_kill(pid, signal=15):
    try:
        os.kill(int(pid), int(signal))
        return {"ok": True, "pid": int(pid), "signal": int(signal)}
    except ProcessLookupError:
        return {"ok": False, "error": "no_such_process", "pid": int(pid)}
    except PermissionError:
        return {"ok": False, "error": "permission_denied", "pid": int(pid)}


def tool_net_fetch(url):
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as e:
        return {"error": f"transport: {e}"}
    body_bytes = r.content[:MAX_FETCH_BODY]
    truncated = len(r.content) > MAX_FETCH_BODY
    try:
        body = body_bytes.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        body = base64.b64encode(body_bytes).decode("ascii")
        encoding = "base64"
    return {
        "status": r.status_code,
        "headers": dict(r.headers),
        "body": body,
        "body_encoding": encoding,
        "truncated": truncated,
    }


# --- Render tools (open assets in new tabs in the GUI) ---------------------

def _is_url(s):
    return isinstance(s, str) and (s.startswith("http://") or s.startswith("https://"))


def _new_asset_id(seed=""):
    h = hashlib.sha1(f"{seed}{time.time()}".encode("utf-8")).hexdigest()
    return h[:12]


def _stage_local(source, default_name, ext_hint=""):
    """Copy a local file under /var/lib/shedos/render/<id>/<name>.
    Returns (asset_id, web_url, title)."""
    if not os.path.isfile(source):
        raise ValueError(f"file not found: {source}")
    asset_id = _new_asset_id(source)
    dest_dir = os.path.join(RENDER_DIR, asset_id)
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(source) or default_name
    if ext_hint and not name.lower().endswith(ext_hint):
        name += ext_hint
    dest = os.path.join(dest_dir, name)
    shutil.copy2(source, dest)
    return asset_id, f"/render/{asset_id}/{name}", name


def _download_to_render(url, default_name, ext_hint=""):
    """HTTP-fetch and stage. Returns (asset_id, web_url, title)."""
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 ShedOS"})
        r.raise_for_status()
    except Exception as e:
        raise ValueError(f"download failed: {e}")
    name = os.path.basename(urllib.parse.urlparse(url).path) or default_name
    if ext_hint and not name.lower().endswith(ext_hint):
        name += ext_hint
    asset_id = _new_asset_id(url)
    dest_dir = os.path.join(RENDER_DIR, asset_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)
    with open(dest, "wb") as f:
        f.write(r.content)
    return asset_id, f"/render/{asset_id}/{name}", name


def _render_response(rtype, asset_id, url, title):
    """Result envelope the GUI looks for to open a new tab."""
    return {
        "ok": True,
        "render": {"type": rtype, "id": asset_id, "url": url, "title": title},
    }


def tool_render_image(source):
    try:
        if _is_url(source):
            aid, url, title = _download_to_render(source, "image", ".img")
            return _render_response("image", aid, url, title)
        aid, url, title = _stage_local(source, "image", "")
        return _render_response("image", aid, url, title)
    except ValueError as e:
        return {"error": str(e)}


def tool_render_pdf(source):
    try:
        if _is_url(source):
            aid, url, title = _download_to_render(source, "doc.pdf", ".pdf")
            return _render_response("pdf", aid, url, title)
        aid, url, title = _stage_local(source, "doc.pdf", ".pdf")
        return _render_response("pdf", aid, url, title)
    except ValueError as e:
        return {"error": str(e)}


def tool_render_web(url):
    if not _is_url(url):
        return {"error": "render_web needs an http(s):// URL"}
    aid = _new_asset_id(url)
    title = urllib.parse.urlparse(url).netloc or url
    # No download — iframe loads the URL directly. Note: some sites
    # send X-Frame-Options that block embedding; if so the iframe will
    # render blank and the user should use net_fetch + render_markdown
    # instead.
    return _render_response("web", aid, url, title)


SCHEMAS = [
    {
        "name": "bash",
        "description": (
            "Run a shell command as root on Alpine Linux. "
            "Returns stdout, stderr, exit_code. Streams truncated to 16 KiB."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_s": {"type": "number", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "apk",
        "description": (
            "Run the Alpine package manager. Pass the args you'd give apk(8), "
            "e.g. 'add htop' or 'search nginx'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"args": {"type": "string"}},
            "required": ["args"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file (up to 64 KiB). Returns content + encoding.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write a file. Pass content as utf-8 string, or base64 with "
            "encoding='base64'. Creates parent dirs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "encoding": {
                    "type": "string",
                    "enum": ["utf-8", "base64"],
                    "default": "utf-8",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List a directory. Returns entries with type/size/mode.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "process_list",
        "description": "List all running processes.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "process_kill",
        "description": "Send a signal to a pid. Defaults to SIGTERM (15).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer"},
                "signal": {"type": "integer", "default": 15},
            },
            "required": ["pid"],
        },
    },
    {
        "name": "net_fetch",
        "description": (
            "HTTP GET a URL. Body capped at 256 KiB, base64-encoded if "
            "non-utf8."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "render_image",
        "description": (
            "Open an image in a new tab in the GUI. `source` is either an "
            "http(s) URL or a local file path on the guest. Returns a "
            "render envelope the GUI uses to add a new tab. Use this when "
            "the user wants to SEE an image — e.g. you used net_fetch to "
            "grab a JPG/PNG, or you wrote one with write_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
        },
    },
    {
        "name": "render_pdf",
        "description": (
            "Open a PDF in a new tab. `source` is a URL (downloaded first "
            "to bypass anti-embedding headers) or a local guest path. "
            "Chromium's built-in PDF viewer renders it inline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
        },
    },
    {
        "name": "render_web",
        "description": (
            "Open a web page in a new tab via iframe. `url` must be http(s). "
            "Use for sites the user wants to BROWSE. Some sites set "
            "X-Frame-Options that block embedding — if the iframe shows "
            "blank, fall back to net_fetch + render_markdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]


HANDLERS = {
    "bash": tool_bash,
    "apk": tool_apk,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "list_dir": tool_list_dir,
    "process_list": tool_process_list,
    "process_kill": tool_process_kill,
    "net_fetch": tool_net_fetch,
    "render_image": tool_render_image,
    "render_pdf": tool_render_pdf,
    "render_web": tool_render_web,
}


def dispatch(name, args):
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(**(args or {}))
    except TypeError as e:
        return {"error": f"bad args for {name}: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
