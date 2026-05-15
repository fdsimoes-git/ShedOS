"""The TUI application: chat REPL with rich rendering + prompt_toolkit input.

Architecture: rich prints chat history to stdout above the prompt;
prompt_toolkit owns the bottom input line with arrow-key history,
custom key bindings, and slash-commands. tabs are virtual — switching
tabs reprints history of the new session.

Why not a full-screen Textual app? py3-textual isn't packaged for
Alpine 3.23. prompt_toolkit + rich is on Alpine and gives us the same
practical features (themes, history, syntax-highlighted markdown,
animated spinners) over serial without surprises.
"""
import os
import sys
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from tui import themes
from tui.rpc_client import RpcClient, RpcError

HISTORY_DIR = "/var/lib/shedos"


def _hist_path(sid):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    return os.path.join(HISTORY_DIR, f"prompt-{sid}.txt")


class ShedOSTui:
    def __init__(self):
        self.console = Console(force_terminal=True, color_system="truecolor")
        self.theme_name = themes.load_saved_theme()
        self.theme = themes.get(self.theme_name)
        self.rpc = RpcClient()
        self.sessions = []
        self.current_id = None
        self.current_title = "(none)"
        self.last_latency_ms = None

    # --- Startup --------------------------------------------------------------

    def connect_and_init(self):
        for attempt in range(20):
            try:
                self.rpc.connect()
                self.rpc.ping()
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if attempt == 0:
                    self.console.print(
                        "[yellow]Waiting for shedos-brain daemon...[/yellow]"
                    )
                time.sleep(1)
        else:
            self.console.print(
                "[red]Could not reach shedos-brain daemon at /run/shedos-brain.sock[/red]"
            )
            sys.exit(1)

        self.refresh_sessions()
        if not self.sessions:
            s = self.rpc.create_session(title="New chat")
            self.refresh_sessions()
            self.switch_to(s["id"])
        else:
            # Open the most-recent
            self.switch_to(self.sessions[0]["id"])

    def refresh_sessions(self):
        try:
            self.sessions = self.rpc.list_sessions()
        except RpcError as e:
            self.console.print(f"[red]rpc error: {e}[/red]")

    # --- Rendering ------------------------------------------------------------

    def banner(self):
        t = self.theme
        title = Text("ShedOS 0.3", style=f"bold {t['primary']}")
        sub = Text("  Claude is your shell. Try /help for commands.",
                   style=t["muted"])
        self.console.rule(title, style=t["accent"])
        self.console.print(sub, justify="center")

    def status_line(self):
        t = self.theme
        tabs = []
        for s in self.sessions:
            marker = "●" if s["id"] == self.current_id else " "
            color = t["primary"] if s["id"] == self.current_id else t["muted"]
            label = f"{marker} {s['title'][:18]}"
            tabs.append(f"[{color}]{label}[/{color}]")
        if tabs:
            self.console.print(
                Text("┊ ").join(self.console.render_str(x) for x in tabs)
            )

    def make_prompt(self):
        t = self.theme
        return HTML(
            f'<style fg="{t["accent"]}" bold="true">▸</style> '
        )

    def render_user(self, text):
        t = self.theme
        self.console.print(
            Panel(
                Text(text, style=t["foreground"]),
                title=Text("You", style=f"bold {t['user']}"),
                title_align="left",
                border_style=t["user"],
                padding=(0, 1),
            )
        )

    def render_assistant_text(self, text):
        # Markdown rendering with code highlighting
        try:
            md = Markdown(text, code_theme="monokai")
        except Exception:
            md = Text(text)
        t = self.theme
        self.console.print(
            Panel(
                md,
                title=Text("Claude", style=f"bold {t['assistant']}"),
                title_align="left",
                border_style=t["assistant"],
                padding=(0, 1),
            )
        )

    def render_tool_done(self, name, output, ok):
        t = self.theme
        color = t["tool_ok"] if ok else t["tool_err"]
        # Compact view: show the most useful field if present
        body = ""
        if isinstance(output, dict):
            if "stdout" in output:
                body = output["stdout"]
                if output.get("stderr"):
                    body += f"\n[stderr]\n{output['stderr']}"
            elif "error" in output:
                body = f"error: {output['error']}"
            elif "content" in output:
                body = str(output["content"])[:1000]
            else:
                body = str(output)[:1000]
        else:
            body = str(output)[:1000]
        body = body.rstrip()
        if not body:
            body = "(no output)"
        # Truncate if huge
        if len(body) > 1500:
            body = body[:1500] + "\n... (truncated)"
        self.console.print(
            Panel(
                Text(body, style=t["foreground"]),
                title=Text(f"⚙  {name}", style=f"bold {color}"),
                title_align="left",
                border_style=color,
                padding=(0, 1),
            )
        )

    def render_error(self, msg):
        t = self.theme
        self.console.print(
            Panel(
                Text(msg, style=t["tool_err"]),
                title=Text("error", style=f"bold {t['tool_err']}"),
                border_style=t["tool_err"],
                padding=(0, 1),
            )
        )

    # --- Tabs / sessions -----------------------------------------------------

    def switch_to(self, sid):
        self.current_id = sid
        for s in self.sessions:
            if s["id"] == sid:
                self.current_title = s["title"]
                break
        try:
            hist = self.rpc.history(sid)
        except RpcError as e:
            self.render_error(f"loading history: {e}")
            return
        info = hist.get("info", {})
        msgs = hist.get("messages", [])
        self.console.clear()
        self.banner()
        self.status_line()
        if msgs:
            self.console.print(
                Text(f"  Resumed: {info.get('title','(untitled)')} "
                     f"({len(msgs)} messages)", style=self.theme["muted"])
            )
            for m in msgs:
                role = m.get("role")
                content = m.get("content")
                if role == "user":
                    if isinstance(content, str):
                        self.render_user(content)
                    elif isinstance(content, list):
                        # tool_result message — skip, we don't replay
                        pass
                elif role == "assistant":
                    if isinstance(content, list):
                        for block in content:
                            t = block.get("type")
                            if t == "text":
                                self.render_assistant_text(block.get("text", ""))
                            elif t == "tool_use":
                                self.console.print(
                                    Text(
                                        f"  [tool: {block.get('name')} "
                                        f"({str(block.get('input',''))[:60]})]",
                                        style=self.theme["muted"],
                                    )
                                )
        else:
            self.console.print(
                Text("  Empty session. Type to start a conversation.",
                     style=self.theme["muted"])
            )

    def new_tab(self):
        s = self.rpc.create_session(title="New chat")
        self.refresh_sessions()
        self.switch_to(s["id"])

    def close_tab(self):
        if self.current_id is None:
            return
        if len(self.sessions) <= 1:
            self.console.print(
                Text("Cannot close the last tab.", style=self.theme["muted"])
            )
            return
        old = self.current_id
        self.rpc.delete_session(old)
        self.refresh_sessions()
        self.switch_to(self.sessions[0]["id"])

    def cycle_tab(self, direction=1):
        if not self.sessions:
            return
        ids = [s["id"] for s in self.sessions]
        try:
            i = ids.index(self.current_id)
        except ValueError:
            i = 0
        i = (i + direction) % len(ids)
        self.switch_to(ids[i])

    def set_theme(self, name):
        if name not in themes.THEMES:
            self.console.print(
                Text(f"Unknown theme: {name}. Available: {', '.join(themes.names())}",
                     style=self.theme["tool_err"])
            )
            return
        self.theme_name = name
        self.theme = themes.get(name)
        themes.save_theme(name)
        self.console.print(
            Text(f"Theme switched to {name}.", style=self.theme["tool_ok"])
        )

    # --- Slash commands ------------------------------------------------------

    def handle_command(self, text):
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0][1:]  # drop leading /
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("help", "?"):
            self.console.print(
                Panel(
                    Text(
                        "/new          new tab\n"
                        "/close        close current tab\n"
                        "/next         next tab (Ctrl-N)\n"
                        "/prev         previous tab (Ctrl-P)\n"
                        "/tabs         list tabs\n"
                        "/title <name> rename current tab\n"
                        "/theme <name> switch theme: " + ", ".join(themes.names()) + "\n"
                        "/clear        clear screen + replay session\n"
                        "/quit         exit (brain keeps running)\n",
                        style=self.theme["foreground"],
                    ),
                    title=Text("commands", style=f"bold {self.theme['primary']}"),
                    border_style=self.theme["primary"],
                )
            )
        elif cmd == "new":
            self.new_tab()
        elif cmd == "close":
            self.close_tab()
        elif cmd == "next":
            self.cycle_tab(1)
        elif cmd == "prev":
            self.cycle_tab(-1)
        elif cmd == "tabs":
            self.refresh_sessions()
            self.status_line()
        elif cmd == "title":
            if not arg:
                self.console.print(Text("usage: /title <name>", style=self.theme["muted"]))
            else:
                self.rpc.set_title(self.current_id, arg)
                self.refresh_sessions()
                self.status_line()
        elif cmd == "theme":
            if not arg:
                self.console.print(
                    Text(f"current: {self.theme_name}; available: {', '.join(themes.names())}",
                         style=self.theme["muted"])
                )
            else:
                self.set_theme(arg)
        elif cmd == "clear":
            self.switch_to(self.current_id)
        elif cmd in ("quit", "exit"):
            raise EOFError()
        else:
            self.console.print(
                Text(f"unknown command: /{cmd}. /help for list.",
                     style=self.theme["tool_err"])
            )

    # --- Send a message + render the streaming response ----------------------

    def send_message(self, text):
        self.render_user(text)
        t = self.theme
        start = time.monotonic()

        # Force-flush stdout after every print: prompt_toolkit's prompt()
        # can leave the terminal in a state where subsequent rich.print
        # output is queued in an internal buffer until the next user-
        # triggered redraw (which is why responses only appear on tab
        # cycle / clear). Explicit flush makes them visible immediately.
        self.console.print(Text("  ...", style=t["tool_running"]))
        sys.stdout.flush()

        try:
            for ev in self.rpc.send(self.current_id, text):
                self._render_event(ev)
                sys.stdout.flush()
                if ev.get("event") in ("end_turn", "error"):
                    break
        except RpcError as e:
            self.render_error(str(e))
            sys.stdout.flush()
            return

        self.last_latency_ms = int((time.monotonic() - start) * 1000)

    def _render_event(self, ev):
        t = self.theme
        et = ev.get("event")
        if et == "assistant_text":
            self.render_assistant_text(ev.get("chunk", ""))
        elif et == "tool_use":
            name = ev.get("name", "?")
            summary = ev.get("input_summary", "")
            self.console.print(
                Text(f"  ⟳ {name}({summary})", style=t["tool_running"])
            )
        elif et == "tool_result":
            out = ev.get("output", {})
            ok = isinstance(out, dict) and "error" not in out
            self.render_tool_done(ev.get("name", "?"), out, ok)
        elif et == "error":
            self.render_error(ev.get("msg", "unknown error"))
        elif et in ("user_msg", "end_turn"):
            pass

    def _render_event(self, ev):
        t = self.theme
        et = ev.get("event")
        if et == "assistant_text":
            self.render_assistant_text(ev.get("chunk", ""))
        elif et == "tool_use":
            name = ev.get("name", "?")
            summary = ev.get("input_summary", "")
            self.console.print(
                Text(f"  ⟳ {name}({summary})", style=t["tool_running"])
            )
        elif et == "tool_result":
            out = ev.get("output", {})
            ok = isinstance(out, dict) and "error" not in out
            self.render_tool_done(ev.get("name", "?"), out, ok)
        elif et == "error":
            self.render_error(ev.get("msg", "unknown error"))
        elif et in ("user_msg", "end_turn"):
            pass

    # --- Main loop ------------------------------------------------------------

    def run(self):
        self.connect_and_init()
        self.banner()
        self.status_line()
        self.console.print(
            Text(
                f"  Theme: {self.theme_name}    Type /help for commands.",
                style=self.theme["muted"],
            )
        )

        kb = KeyBindings()

        @kb.add("c-t")
        def _(event):
            event.app.exit(result="__cmd:new")

        @kb.add("c-w")
        def _(event):
            event.app.exit(result="__cmd:close")

        @kb.add("c-n")
        def _(event):
            event.app.exit(result="__cmd:next")

        @kb.add("c-p")
        def _(event):
            event.app.exit(result="__cmd:prev")

        @kb.add("c-l")
        def _(event):
            event.app.exit(result="__cmd:clear")

        while True:
            try:
                hist = FileHistory(_hist_path(self.current_id))
                session = PromptSession(
                    history=hist,
                    complete_style=CompleteStyle.READLINE_LIKE,
                    key_bindings=kb,
                )
                text = session.prompt(self.make_prompt())
            except EOFError:
                self.console.print(Text("bye.", style=self.theme["muted"]))
                return
            except KeyboardInterrupt:
                self.console.print()
                continue

            if text is None:
                continue
            text = text.strip()
            if not text:
                continue

            # Key bindings exit with __cmd:<name>; map to slash command
            if text.startswith("__cmd:"):
                self.handle_command("/" + text[len("__cmd:"):])
                continue
            if text.startswith("/"):
                self.handle_command(text)
                continue
            try:
                self.send_message(text)
            except Exception as e:
                self.render_error(f"{type(e).__name__}: {e}")


def run():
    ShedOSTui().run()
