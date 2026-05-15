"""ShedOS TUI — Textual-based modern interface.

Replaces the prompt_toolkit + rich REPL with a full-screen Textual App.
Cards (User / Claude / Tool) have backgrounds, rounded borders, padding,
and proper card visuals. Header + Footer + TabbedContent + Input. Themes
via Textual's Theme system. RPC backend (rpc_client.py) is unchanged.
"""
import asyncio
import os
import sys
import threading

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import (
    Footer, Header, Input, Static, TabbedContent, TabPane,
)

from tui import themes
from tui.rpc_client import RpcClient, RpcError
from tui.widgets import ClaudeCard, ErrorCard, ToolCard, UserCard


HISTORY_PATH = "/var/lib/shedos/prompt-history.txt"


class ChatPane(VerticalScroll):
    """A tab-pane chat: scrollable column of cards bound to one session."""

    DEFAULT_CSS = """
    ChatPane {
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, session_id, title, **kw):
        super().__init__(**kw)
        self.session_id = session_id
        self.title = title

    def add_user(self, text):
        self.mount(UserCard(text))
        self.scroll_end(animate=False)

    def add_claude(self, text):
        self.mount(ClaudeCard(text))
        self.scroll_end(animate=False)

    def add_tool(self, name, summary):
        c = ToolCard(name, summary)
        self.mount(c)
        self.scroll_end(animate=False)
        return c

    def add_error(self, msg):
        self.mount(ErrorCard(msg))
        self.scroll_end(animate=False)


class ShedOSApp(App):
    """Top-level Textual app."""

    CSS = """
    Screen { background: $surface; }
    Header { background: $primary 30%; color: $foreground; height: 1; }
    Footer { background: $panel; color: $foreground; }
    TabbedContent { background: $surface; }
    Tabs Tab { padding: 0 2; }
    Tabs Tab.-active {
        background: $primary 40%;
        color: $foreground;
        text-style: bold;
    }
    Input {
        margin: 0 1 0 1;
        border: tall $accent;
        background: $panel;
        color: $foreground;
        height: 3;
    }
    Input:focus { border: tall $primary; }
    """

    BINDINGS = [
        Binding("ctrl+t", "new_tab", "New tab", show=True),
        Binding("ctrl+w", "close_tab", "Close tab", show=True),
        Binding("ctrl+n", "next_tab", "Next", show=True),
        Binding("ctrl+p", "prev_tab", "Prev", show=True),
        Binding("ctrl+k", "cycle_theme", "Theme", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rpc = RpcClient()
        self._tab_counter = 0
        self._theme_name = themes.load_saved()

    # --- App lifecycle -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield TabbedContent(id="tabs")
        yield Input(placeholder="ask anything…  (Ctrl-T new tab · Ctrl-K theme · /help)",
                    id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "ShedOS"
        self.sub_title = "Claude is your shell"
        # Register all themes and apply the saved one
        for n in themes.names():
            self.register_theme(themes.textual_theme(n))
        self.theme = self._theme_name

        # Connect to brain (in a thread; RpcClient is sync). Retry if not up.
        await self._connect_with_retry()

        # Open existing sessions or create one
        sessions = await self._call_rpc("list_sessions")
        tabs = self.query_one(TabbedContent)
        if not sessions:
            info = await self._call_rpc("create_session", title="New chat")
            await self._add_tab(info["id"], info["title"])
        else:
            for s in sessions:
                await self._add_tab(s["id"], s["title"], history=True)
            tabs.active = f"tab-{sessions[0]['id']}"

        self.query_one(Input).focus()

    async def _connect_with_retry(self):
        for _ in range(30):
            try:
                await asyncio.to_thread(self.rpc.connect)
                await self._call_rpc("ping")
                return
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(1)
        # Surface as a status message
        self.bell()

    async def _call_rpc(self, method_name, **kw):
        """Call a sync RpcClient method in a thread."""
        method = getattr(self.rpc, method_name)
        return await asyncio.to_thread(method, **kw) if kw else \
            await asyncio.to_thread(method)

    # --- Tabs ----------------------------------------------------------------

    async def _add_tab(self, session_id, title, history=False):
        tabs = self.query_one(TabbedContent)
        # Different IDs for TabPane vs ChatPane to avoid query_one ambiguity.
        tab_id = f"tab-{session_id}"
        chat_id = f"chat-{session_id}"
        pane = ChatPane(session_id, title, id=chat_id)
        await tabs.add_pane(TabPane(title, pane, id=tab_id))
        if history:
            await self._populate_history(pane)
        return pane

    async def _populate_history(self, pane):
        try:
            data = await self._call_rpc("history", sid=pane.session_id)
        except RpcError:
            return
        for m in data.get("messages", []):
            role = m.get("role")
            content = m.get("content")
            if role == "user" and isinstance(content, str):
                pane.add_user(content)
            elif role == "assistant" and isinstance(content, list):
                for block in content:
                    t = block.get("type")
                    if t == "text":
                        pane.add_claude(block.get("text", ""))
                    elif t == "tool_use":
                        c = pane.add_tool(
                            block.get("name", "?"),
                            str(block.get("input", ""))[:60],
                        )
                        c.set_result({"info": "(history)"}, True)

    def _current_pane(self):
        tabs = self.query_one(TabbedContent)
        active = tabs.active  # this is the TabPane id like "tab-<sid>"
        if not active or not active.startswith("tab-"):
            return None
        chat_id = "chat-" + active[len("tab-"):]
        try:
            return self.query_one(f"#{chat_id}", ChatPane)
        except NoMatches:
            return None

    async def action_new_tab(self):
        info = await self._call_rpc("create_session", title="New chat")
        await self._add_tab(info["id"], info["title"])
        self.query_one(TabbedContent).active = f"tab-{info['id']}"
        self.query_one(Input).focus()

    async def action_close_tab(self):
        pane = self._current_pane()
        if pane is None:
            return
        tabs = self.query_one(TabbedContent)
        if tabs.tab_count <= 1:
            return  # don't close the last tab
        sid = pane.session_id
        await self._call_rpc("delete_session", sid=sid)
        await tabs.remove_pane(f"tab-{sid}")

    def action_next_tab(self):
        self.query_one(TabbedContent).action_next_tab()

    def action_prev_tab(self):
        self.query_one(TabbedContent).action_previous_tab()

    def action_cycle_theme(self):
        names = themes.names()
        i = names.index(self.theme) if self.theme in names else 0
        nxt = names[(i + 1) % len(names)]
        self.theme = nxt
        self._theme_name = nxt
        themes.save(nxt)
        self.notify(f"Theme: {nxt}", severity="information", timeout=2)

    # --- Input handling ------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            await self._handle_slash(text)
            return
        await self._send_to_brain(text)

    async def _handle_slash(self, text):
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("help", "?"):
            self.notify(
                "/new /close /next /prev /theme /title /quit",
                title="commands", timeout=4,
            )
        elif cmd == "new":
            await self.action_new_tab()
        elif cmd == "close":
            await self.action_close_tab()
        elif cmd == "next":
            self.action_next_tab()
        elif cmd == "prev":
            self.action_prev_tab()
        elif cmd == "theme":
            if arg in themes.names():
                self.theme = arg
                self._theme_name = arg
                themes.save(arg)
                self.notify(f"Theme: {arg}")
            else:
                self.notify(f"available: {', '.join(themes.names())}")
        elif cmd == "title":
            pane = self._current_pane()
            if pane and arg:
                await self._call_rpc("set_title", sid=pane.session_id, title=arg)
                tabs = self.query_one(TabbedContent)
                tabs.get_tab(f"tab-{pane.session_id}").label = arg
        elif cmd in ("quit", "exit"):
            self.exit()
        else:
            self.notify(f"unknown: /{cmd}", severity="warning", timeout=2)

    async def _send_to_brain(self, text):
        pane = self._current_pane()
        if pane is None:
            return
        pane.add_user(text)
        # Stream events from a thread because rpc.send is a blocking generator
        sid = pane.session_id
        active_tools = {}

        def producer(loop):
            try:
                for ev in self.rpc.send(sid, text):
                    asyncio.run_coroutine_threadsafe(
                        self._handle_event(pane, ev, active_tools), loop
                    )
            except RpcError as e:
                asyncio.run_coroutine_threadsafe(
                    self._handle_event(pane, {"event": "error", "msg": str(e)},
                                        active_tools), loop
                )

        loop = asyncio.get_running_loop()
        threading.Thread(target=producer, args=(loop,), daemon=True).start()

    async def _handle_event(self, pane, ev, active_tools):
        et = ev.get("event")
        if et == "assistant_text":
            pane.add_claude(ev.get("chunk", ""))
        elif et == "tool_use":
            tid = ev.get("id")
            card = pane.add_tool(ev.get("name", "?"),
                                 ev.get("input_summary", ""))
            active_tools[tid] = card
        elif et == "tool_result":
            tid = ev.get("id")
            card = active_tools.pop(tid, None)
            out = ev.get("output", {})
            ok = isinstance(out, dict) and "error" not in out
            if card is not None:
                card.set_result(out, ok)
            else:
                pane.add_tool(ev.get("name", "?"), "?").set_result(out, ok)
        elif et == "error":
            pane.add_error(ev.get("msg", "unknown error"))
        elif et in ("user_msg", "end_turn"):
            pass


def run():
    # Make config / sessions / etc. importable when launched as a script
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))
    ShedOSApp().run()
