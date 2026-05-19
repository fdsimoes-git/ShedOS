#!/usr/bin/env python3
"""ShedOS minimal chat client.

Replaces the Textual TUI (v0.3.0 - v0.6.0). Talks to the brain daemon
over /run/shedos-brain.sock via brain_client.py and runs a plain
stdin/stdout chat loop with rich-coloured output. Launched by getty
on ttyS0 (via run-chat.sh) and available over SSH.

Why drop Textual: the Chromium GUI on tty1 is the primary UX since
v0.4.0. The TUI was a heavy second frontend (~800 LOC) that drifted
behind the GUI's features (render tabs, persona dropdown, settings
modal aren't there) and rarely got tested end-to-end. A small text
client covers the SSH/serial use case without the maintenance load.

Slash commands:
  /help              show this
  /new [title]       create + switch to a new session
  /list              list sessions, most-recent first
  /switch <n|id>     switch to session by index from /list or by id
  /title <text>      rename current session
  /delete <n|id>     delete a session (if it was active, switches away)
  /clear             clear screen
  /quit              (or Ctrl-D)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brain_client import RpcClient, RpcError

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    _console = Console()
except ImportError:
    _console = None  # ultra-minimal fallback below


def out(msg="", style=None):
    if _console is not None:
        _console.print(msg, style=style)
    else:
        sys.stdout.write(str(msg) + "\n")
        sys.stdout.flush()


def header(title):
    if _console is not None:
        _console.print(Panel.fit(title, border_style="cyan"))
    else:
        bar = "=" * min(70, len(title) + 4)
        out(bar)
        out(f"  {title}")
        out(bar)


def render_assistant_text(text):
    """Render the full assistant message buffered for one turn. Streaming-
    markdown is awkward (the per-chunk heuristic fires on prose like
    'in-flight - test' and mixes block-renders mid-stream), so we buffer
    all `assistant_text` chunks for the turn and render once at
    `end_turn` — either as rendered Markdown when there's any
    block-level structure, or plain text otherwise."""
    if not text:
        return
    if _console is None:
        out(text)
        return
    # Block-level markers only. Inline emphasis like a stray `*foo*`
    # in prose stays plain — avoids surprise rendering of casual chat.
    has_block_md = (
        "```" in text
        or any(text.startswith(p) or f"\n{p}" in text
               for p in ("# ", "## ", "### ", "- ", "* ", "> ", "1. "))
    )
    if has_block_md:
        _console.print(Markdown(text))
    else:
        _console.print(text)


def render_tool_use(name, summary):
    if _console is not None:
        t = Text()
        t.append("  ⚙ ", style="yellow")
        t.append(f"{name}", style="bold yellow")
        if summary:
            t.append(f"  {summary}", style="dim")
        _console.print(t)
    else:
        out(f"  [tool: {name}({summary})]")


def render_tool_result(output, ok):
    if output is None:
        # Some tools (process_kill on a successful signal, etc.) return
        # `None` or an empty result. Print a quiet placeholder rather
        # than the literal "→ None".
        out("  → ok" if ok else "  ✗ (no output)", style="dim")
        return
    if _console is None:
        out(f"  → {output}" if ok else f"  ✗ {output}")
        return
    if isinstance(output, dict):
        if "render" in output:
            r = output["render"]
            _console.print(
                f"  → opened {r.get('type', '?')} tab: {r.get('title', '')}",
                style="green")
            return
        if "stdout" in output:
            stdout = output.get("stdout", "")
            stderr = output.get("stderr", "")
            if stdout.strip():
                _console.print(stdout.rstrip(), style="dim")
            if stderr.strip():
                _console.print(stderr.rstrip(), style="dim red")
            return
        if "error" in output:
            _console.print(f"  ✗ {output['error']}", style="red")
            return
    _console.print(f"  → {output}", style="dim")


def render_error(msg):
    out(f"[error] {msg}", style="red")


# ---- session bookkeeping ---------------------------------------------------

class ChatState:
    def __init__(self, rpc):
        self.rpc = rpc
        self.session_id = None
        self.session_title = None
        # Snapshot from the most recent /list so /switch <n> works.
        self.last_listing = []

    def ensure_session(self):
        if self.session_id:
            return
        sessions = self.rpc.list_sessions()
        if sessions:
            s = sessions[0]
            self.session_id = s["id"]
            self.session_title = s.get("title", "(untitled)")
        else:
            info = self.rpc.create_session(title="New chat")
            self.session_id = info["id"]
            self.session_title = info.get("title", "New chat")

    def prompt_prefix(self):
        return f"[{self.session_title or 'chat'}] > "


# ---- slash commands --------------------------------------------------------

def _print_help():
    out("\nCommands:", style="bold cyan")
    out("  /help              this message")
    out("  /new [title]       create + switch to a new session")
    out("  /list              list sessions, most-recent first")
    out("  /switch <n|id>     switch by index from /list or by id")
    out("  /title <text>      rename current session")
    out("  /delete <n|id>     delete a session (switches away if active)")
    out("  /clear             clear screen")
    out("  /quit              exit (Ctrl-D also works)")
    out("")


def handle_slash(state, line):
    """Returns True to keep looping, False to quit."""
    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""
    if cmd in ("help", "?"):
        _print_help()
    elif cmd in ("quit", "exit", "q"):
        return False
    elif cmd == "clear":
        os.system("clear")
    elif cmd == "new":
        info = state.rpc.create_session(title=arg or "New chat")
        state.session_id = info["id"]
        state.session_title = info.get("title", arg or "New chat")
        out(f"  → switched to new session: {state.session_title} ({state.session_id})",
            style="green")
    elif cmd == "list":
        sessions = state.rpc.list_sessions()
        state.last_listing = sessions
        if not sessions:
            out("  (no sessions)")
        else:
            for i, s in enumerate(sessions, 1):
                marker = "* " if s["id"] == state.session_id else "  "
                out(f"  {marker}{i:>2}. {s['id']}  {s.get('title','(untitled)')}"
                    f"  ({s.get('message_count', 0)} msgs)")
    elif cmd == "switch":
        if not arg:
            out("  usage: /switch <index from /list, or session id>",
                style="red")
        else:
            target = None
            if arg.isdigit() and state.last_listing:
                idx = int(arg) - 1
                if 0 <= idx < len(state.last_listing):
                    target = state.last_listing[idx]
            if target is None:
                target = next(
                    (s for s in state.rpc.list_sessions() if s["id"] == arg),
                    None)
            if target is None:
                out(f"  no such session: {arg!r}  (try /list first)",
                    style="red")
            else:
                state.session_id = target["id"]
                state.session_title = target.get("title", "(untitled)")
                out(f"  → switched: {state.session_title} ({state.session_id})",
                    style="green")
    elif cmd == "title":
        if not arg:
            out("  usage: /title <new title>", style="red")
        else:
            state.rpc.set_title(state.session_id, arg)
            state.session_title = arg
            out(f"  → renamed to {arg!r}", style="green")
    elif cmd == "delete":
        if not arg:
            out("  usage: /delete <index from /list, or session id>",
                style="red")
        else:
            target = None
            if arg.isdigit() and state.last_listing:
                idx = int(arg) - 1
                if 0 <= idx < len(state.last_listing):
                    target = state.last_listing[idx]
            if target is None:
                target = next(
                    (s for s in state.rpc.list_sessions() if s["id"] == arg),
                    None)
            if target is None:
                out(f"  no such session: {arg!r}  (try /list first)",
                    style="red")
            else:
                tid = target["id"]
                ttitle = target.get("title", "(untitled)")
                state.rpc.delete_session(tid)
                state.last_listing = [s for s in state.last_listing
                                       if s["id"] != tid]
                out(f"  → deleted {ttitle} ({tid})", style="green")
                # If we just deleted the active session, fall back to
                # the most-recent remaining one or create a fresh chat.
                if tid == state.session_id:
                    state.session_id = None
                    state.session_title = None
                    state.ensure_session()
                    out(f"  → switched to: {state.session_title} "
                        f"({state.session_id})", style="dim")
    else:
        out(f"  unknown command: /{cmd}  (try /help)", style="red")
    return True


# ---- main loop -------------------------------------------------------------

def run():
    header("ShedOS chat  ·  /help for commands  ·  Ctrl-D to quit")
    rpc = RpcClient()
    try:
        rpc.connect()
    except OSError as e:
        render_error(f"could not connect to brain socket: {e}")
        sys.exit(1)
    state = ChatState(rpc)
    state.ensure_session()
    out(f"  session: {state.session_title} ({state.session_id})",
        style="dim")
    out("")

    while True:
        try:
            line = input(state.prompt_prefix())
        except EOFError:
            out("\n(eof — bye)")
            return
        except KeyboardInterrupt:
            out("^C  (use /quit to exit)")
            continue
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            if not handle_slash(state, line):
                return
            continue
        # Buffer assistant_text chunks across the turn so we render
        # the full message once (with consistent markdown styling)
        # instead of mid-stream per-chunk renders that mix block- and
        # inline-level styles awkwardly.
        text_buffer = []
        try:
            for event in rpc.send(state.session_id, line):
                ev = event.get("event")
                if ev == "assistant_text":
                    text_buffer.append(event.get("chunk", ""))
                elif ev == "tool_use":
                    # Flush any pending assistant text before the tool
                    # call so the visual order matches the brain's flow.
                    if text_buffer:
                        render_assistant_text("".join(text_buffer))
                        text_buffer = []
                    render_tool_use(event.get("name", "?"),
                                     event.get("input_summary", ""))
                elif ev == "tool_result":
                    o = event.get("output")
                    ok = not (isinstance(o, dict) and "error" in o)
                    render_tool_result(o, ok)
                elif ev == "error":
                    if text_buffer:
                        render_assistant_text("".join(text_buffer))
                        text_buffer = []
                    render_error(event.get("msg", "unknown"))
                elif ev == "end_turn":
                    if text_buffer:
                        render_assistant_text("".join(text_buffer))
                        text_buffer = []
                    out("")  # trailing newline after the turn
        except RpcError as e:
            if text_buffer:
                render_assistant_text("".join(text_buffer))
            render_error(str(e))
        except KeyboardInterrupt:
            if text_buffer:
                render_assistant_text("".join(text_buffer))
            out("\n^C  (turn interrupted; daemon may still finish)")


def main():
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
