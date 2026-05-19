import base64
import hashlib
import html as _html
import json as _json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.parse

import httpx

MAX_STREAM = 16 * 1024
MAX_FILE_READ = 64 * 1024
MAX_FETCH_BODY = 256 * 1024
RENDER_DIR = "/var/lib/shedos/render"
RENDER_MANIFEST = "/var/lib/shedos/render-tabs.json"
MAX_RENDER_BYTES = 50 * 1024 * 1024  # 50 MiB cap per render asset
import re as _re


def _safe_filename(name, default="asset"):
    """Sanitize for both filesystem + URL: strip path separators, control
    chars, quotes, and URL-special chars. Keeps spaces / dashes / dots."""
    if not name:
        return default
    name = os.path.basename(name)
    cleaned = _re.sub(r'[^\w.\- ]+', '_', name).strip(' _')
    return cleaned or default


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
    # Content-addressed: same seed -> same id. This is the contract the
    # de-dupe in _add_to_manifest relies on; the previous version mixed
    # time.time() into the hash, which defeated dedupe and let the
    # manifest + asset dir grow unboundedly on repeated render_* calls.
    # Callers MUST pass a seed unique-per-distinct-content (e.g.
    # render_markdown passes "md:" + the WHOLE text, not a prefix).
    h = hashlib.sha1(str(seed).encode("utf-8")).hexdigest()
    return h[:12]


def _stage_local(source, default_name, ext_hint=""):
    """Copy a local file under /var/lib/shedos/render/<id>/<name>.
    Returns (asset_id, web_url, title)."""
    if not os.path.isfile(source):
        raise ValueError(f"file not found: {source}")
    size = os.path.getsize(source)
    if size > MAX_RENDER_BYTES:
        raise ValueError(f"file too large ({size} > {MAX_RENDER_BYTES} bytes)")
    asset_id = _new_asset_id(source)
    dest_dir = os.path.join(RENDER_DIR, asset_id)
    os.makedirs(dest_dir, exist_ok=True)
    raw_name = os.path.basename(source) or default_name
    if ext_hint and not raw_name.lower().endswith(ext_hint):
        raw_name += ext_hint
    name = _safe_filename(raw_name, default_name)
    dest = os.path.join(dest_dir, name)
    shutil.copy2(source, dest)
    # URL-encode the path component so weird-but-safe names still work.
    quoted = urllib.parse.quote(name)
    return asset_id, f"/render/{asset_id}/{quoted}", name


def _download_to_render(url, default_name, ext_hint=""):
    """Stream-download an HTTP asset to disk with a size cap. Returns
    (asset_id, web_url, title)."""
    asset_id = _new_asset_id(url)
    dest_dir = os.path.join(RENDER_DIR, asset_id)
    os.makedirs(dest_dir, exist_ok=True)
    raw_name = os.path.basename(urllib.parse.urlparse(url).path) or default_name
    if ext_hint and not raw_name.lower().endswith(ext_hint):
        raw_name += ext_hint
    name = _safe_filename(raw_name, default_name)
    dest = os.path.join(dest_dir, name)
    try:
        with httpx.stream("GET", url, timeout=30, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 ShedOS"}) as r:
            r.raise_for_status()
            total = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > MAX_RENDER_BYTES:
                        f.close()
                        try:
                            os.unlink(dest)
                        except OSError:
                            pass
                        raise ValueError(
                            f"response exceeded {MAX_RENDER_BYTES} bytes; aborted"
                        )
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise ValueError(f"download failed: {e}")
    quoted = urllib.parse.quote(name)
    return asset_id, f"/render/{asset_id}/{quoted}", name


# --- Render-tab manifest (v0.6.0) ----------------------------------------
# A small JSON file at /var/lib/shedos/render-tabs.json that survives reboots
# and page refreshes. Each entry mirrors the {type, id, url, title} envelope
# the GUI uses to open a tab; the GUI reads the manifest on load and re-
# populates the tab bar from it.
#
# Tool calls run on a worker thread via asyncio.to_thread (see brain.py),
# so two concurrent render_* invocations could otherwise interleave their
# load -> mutate -> save and lose an entry. The lock makes the read-
# modify-write atomic across threads in the brain process. (It doesn't
# protect against a separate process editing the file, but nothing does.)
_MANIFEST_LOCK = threading.Lock()


def _load_manifest():
    try:
        with open(RENDER_MANIFEST, "r") as f:
            data = _json.load(f)
        if isinstance(data, dict) and isinstance(data.get("tabs"), list):
            return data
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        pass
    return {"tabs": []}


def _save_manifest(manifest):
    os.makedirs(os.path.dirname(RENDER_MANIFEST), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(RENDER_MANIFEST),
        prefix="render-tabs-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            _json.dump(manifest, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, RENDER_MANIFEST)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _add_to_manifest(asset_id, rtype, title, url):
    with _MANIFEST_LOCK:
        m = _load_manifest()
        # De-dupe by asset_id (same id implies same content under our
        # content-addressed hash) — without this, repeated render_web
        # on the same URL would pile up. Relies on _new_asset_id being
        # deterministic per-seed; re-introducing a nonce there would
        # silently break dedupe.
        m["tabs"] = [t for t in m["tabs"] if t.get("id") != asset_id]
        m["tabs"].append({
            "id": asset_id,
            "type": rtype,
            "title": title,
            "url": url,
            "created_at": int(time.time()),
        })
        _save_manifest(m)


_ASSET_ID_RE = _re.compile(r'^[0-9a-f]{12}$')


def _validate_asset_id(asset_id):
    """asset_ids are always 12 lowercase hex chars (sha1[:12]). Reject
    anything else — without this, a hand-crafted
    DELETE /api/render-tabs/../../etc would feed path-traversal into
    shutil.rmtree below. CodeQL flagged this as 'uncontrolled data in
    path expression'."""
    if not isinstance(asset_id, str) or not _ASSET_ID_RE.match(asset_id):
        raise ValueError(f"invalid asset_id: {asset_id!r}")


def remove_render_asset(asset_id):
    """Drop the manifest entry AND wipe the asset dir. Called by
    web_server.py's DELETE /api/render-tabs/{id} when the user closes a
    render tab in the GUI."""
    _validate_asset_id(asset_id)
    with _MANIFEST_LOCK:
        m = _load_manifest()
        before = len(m["tabs"])
        m["tabs"] = [t for t in m["tabs"] if t.get("id") != asset_id]
        _save_manifest(m)
    dest_dir = os.path.join(RENDER_DIR, asset_id)
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)
    return before != len(m["tabs"])


def _render_response(rtype, asset_id, url, title):
    """Result envelope the GUI looks for to open a new tab. Side effect:
    appends to the on-disk manifest so the tab persists across refreshes."""
    _add_to_manifest(asset_id, rtype, title, url)
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


# --- Rich-content render tools (v0.6.0) ----------------------------------

def _render_page_html(title, body_html, mono=False):
    """Self-contained styled HTML page. The render tabs load this via
    iframe with a strict no-scripts sandbox, so all styling has to be
    inline. Palette matches the GUI's tokyo-night defaults so embedded
    tabs feel like part of the app."""
    safe_title = _html.escape(title or "")
    font_family = (
        "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace"
        if mono else
        "-apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif"
    )
    return (
        f"<!doctype html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>{safe_title}</title><style>"
        f"html,body{{margin:0;padding:0;background:#1a1b26;color:#c0caf5;"
        f"font-family:{font_family};font-size:14px;line-height:1.55;}}"
        f"body{{padding:24px;}}"
        f"h1,h2,h3,h4{{color:#7aa2f7;margin:1.2em 0 .4em;}}"
        f"a{{color:#7dcfff;}}"
        f"code{{font-family:'JetBrains Mono','SF Mono',Menlo,monospace;"
        f"background:#16161e;padding:1px 5px;border-radius:3px;}}"
        f"pre{{background:#16161e;padding:14px;border-radius:6px;"
        f"overflow-x:auto;}}"
        f"pre code{{background:transparent;padding:0;}}"
        f"table{{border-collapse:collapse;margin:12px 0;}}"
        f"th,td{{border:1px solid #2f334d;padding:6px 12px;}}"
        f"blockquote{{border-left:3px solid #7aa2f7;margin:.6em 0;"
        f"padding:.3em 1em;color:#a9b1d6;}}"
        f"</style></head><body>\n{body_html}\n</body></html>\n"
    )


def _stage_rendered_html(asset_id, html_content):
    """Write a self-contained HTML page to /var/lib/shedos/render/<id>/
    index.html. Atomic via mkstemp + os.replace so a brain crash mid-
    write doesn't leave a half-written page in a permanent location
    (matches the pattern config.save_*() and _save_manifest() use)."""
    dest_dir = os.path.join(RENDER_DIR, asset_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "index.html")
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".index-", suffix=".html")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_content)
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return f"/render/{asset_id}/index.html"


def tool_render_markdown(text, title="markdown"):
    if not isinstance(text, str):
        return {"error": "render_markdown.text must be a string"}
    try:
        import markdown as _md
        body = _md.markdown(
            text, extensions=["fenced_code", "tables", "nl2br"])
    except ImportError:
        body = f"<pre>{_html.escape(text)}</pre>"
    # python-markdown passes raw HTML through by default, including
    # <script>/<style>. The render iframe's sandbox blocks script
    # execution and gives the iframe an opaque origin (no parent-state
    # access) — so this is contained today, but if the sandbox is ever
    # loosened to `allow-scripts` we'd have an XSS vector via prompt-
    # injected markdown. Defense-in-depth: strip the tag pairs most
    # likely to bite.
    body = _re.sub(
        r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
        "", body, flags=_re.IGNORECASE | _re.DOTALL)
    body = _re.sub(r"<\s*(script|style)\b[^>]*/?\s*>",
                    "", body, flags=_re.IGNORECASE)
    page = _render_page_html(title or "markdown", body, mono=False)
    asset_id = _new_asset_id("md:" + text)
    url = _stage_rendered_html(asset_id, page)
    return _render_response("markdown", asset_id, url, title or "markdown")


def tool_render_code(text, language=None, title=None):
    if not isinstance(text, str):
        return {"error": "render_code.text must be a string"}
    title = title or (f"{language} snippet" if language else "code")
    body = None
    try:
        from pygments import highlight as _pyg_highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import (
            TextLexer, get_lexer_by_name, guess_lexer)
        try:
            lexer = (get_lexer_by_name(language) if language
                     else guess_lexer(text))
        except Exception:
            lexer = TextLexer()
        # noclasses=True inlines the styles (no separate CSS file needed)
        formatter = HtmlFormatter(noclasses=True, style="monokai",
                                   nowrap=False)
        body = _pyg_highlight(text, lexer, formatter)
    except ImportError:
        body = f"<pre><code>{_html.escape(text)}</code></pre>"
    page = _render_page_html(title, body, mono=True)
    asset_id = _new_asset_id(f"code:{language or ''}:{text}")
    url = _stage_rendered_html(asset_id, page)
    return _render_response("code", asset_id, url, title)


def tool_render_json(value, title="json"):
    """Pretty-print JSON in a new tab. `value` may be a JSON-encoded
    string or any JSON-serialisable Python value (Claude commonly sends
    dicts/lists directly via the tool-use API)."""
    if isinstance(value, str):
        try:
            parsed = _json.loads(value)
        except _json.JSONDecodeError as e:
            return {"error": f"render_json: invalid JSON: {e}"}
    else:
        parsed = value
    try:
        pretty = _json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        return {"error": f"render_json: not serialisable: {e}"}
    body = f"<pre><code>{_html.escape(pretty)}</code></pre>"
    page = _render_page_html(title or "json", body, mono=True)
    asset_id = _new_asset_id("json:" + pretty)
    url = _stage_rendered_html(asset_id, page)
    return _render_response("json", asset_id, url, title or "json")


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
    {
        "name": "render_markdown",
        "description": (
            "Render Markdown text as a styled page in a new tab. Use this "
            "for long-form answers, formatted notes, summaries, or anything "
            "with tables / headers / fenced code blocks that would be ugly "
            "as chat text. Tab persists across page refreshes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "render_code",
        "description": (
            "Render a code snippet with syntax highlighting in a new tab. "
            "Pass `language` (e.g. 'python', 'rust', 'json', 'bash') so "
            "Pygments picks the right lexer; if omitted it'll guess. Use "
            "for longer snippets you want the user to read/copy — short "
            "in-line code is fine as chat markdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "language": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "render_json",
        "description": (
            "Pretty-print JSON in a new tab. `value` can be a JSON string "
            "or a JSON-serialisable object/array (the API will pass dicts "
            "and lists through directly). Use for API responses, config "
            "dumps, anything structured."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {},
                "title": {"type": "string"},
            },
            "required": ["value"],
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
    "render_markdown": tool_render_markdown,
    "render_code": tool_render_code,
    "render_json": tool_render_json,
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
