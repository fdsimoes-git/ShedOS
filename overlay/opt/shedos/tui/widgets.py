"""Custom Textual widgets for ShedOS chat cards."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Label, Markdown, Static


class Card(Container):
    """Base styled card with a header label + body."""

    DEFAULT_CSS = """
    Card {
        height: auto;
        margin: 1 0;
        padding: 1 2;
        border: round $secondary;
        background: $panel;
    }
    Card .card-header {
        height: 1;
        color: $secondary;
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, header, body=None, **kw):
        super().__init__(**kw)
        self._header = header
        self._body = body

    def compose(self) -> ComposeResult:
        yield Label(self._header, classes="card-header")
        if isinstance(self._body, str):
            yield Static(self._body)
        elif self._body is not None:
            yield self._body


class UserCard(Card):
    DEFAULT_CSS = """
    UserCard {
        height: auto;
        margin: 1 2;
        padding: 1 2;
        border: round $success;
        background: $panel-user;
    }
    UserCard .card-header {
        color: $success;
        text-style: bold;
        margin-bottom: 1;
    }
    UserCard Static {
        color: $foreground;
    }
    """

    def __init__(self, text, **kw):
        super().__init__("◉  YOU", body=text, **kw)


class ClaudeCard(Card):
    DEFAULT_CSS = """
    ClaudeCard {
        height: auto;
        margin: 1 2;
        padding: 1 2;
        border: round $primary;
        background: $panel-assistant;
    }
    ClaudeCard .card-header {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    ClaudeCard Markdown {
        background: $panel-assistant;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self, text, **kw):
        # Body is a Markdown widget so headings/code-blocks/tables render
        super().__init__("✦  CLAUDE", body=Markdown(text), **kw)


class ToolCard(Card):
    """Yellow "running" -> green/red "done" with the tool output."""

    DEFAULT_CSS = """
    ToolCard {
        height: auto;
        margin: 1 4;
        padding: 0 2;
        border: round $warning;
        background: $panel-tool;
    }
    ToolCard.tool-ok { border: round $success; }
    ToolCard.tool-err { border: round $error; }
    ToolCard .card-header {
        color: $warning;
        text-style: bold;
        margin-bottom: 0;
    }
    ToolCard.tool-ok .card-header { color: $success; }
    ToolCard.tool-err .card-header { color: $error; }
    ToolCard Static {
        color: $foreground;
        text-style: dim;
    }
    """

    def __init__(self, name, summary, **kw):
        super().__init__(f"⚙  {name}({summary})", body=Static("running…"), **kw)
        self._name = name

    def set_result(self, output, ok):
        self.add_class("tool-ok" if ok else "tool-err")
        body = self.query_one(Static)
        if isinstance(output, dict):
            if "stdout" in output:
                txt = output["stdout"]
                if output.get("stderr"):
                    txt += f"\n[stderr]\n{output['stderr']}"
            elif "error" in output:
                txt = f"error: {output['error']}"
            else:
                import json
                txt = json.dumps(output, indent=2)[:1500]
        else:
            txt = str(output)[:1500]
        if len(txt) > 1500:
            txt = txt[:1500] + "\n... (truncated)"
        body.update(txt.rstrip() or "(no output)")


class ErrorCard(Card):
    DEFAULT_CSS = """
    ErrorCard {
        height: auto;
        margin: 1 2;
        padding: 1 2;
        border: heavy $error;
        background: $error 20%;
    }
    ErrorCard .card-header {
        color: $error;
        text-style: bold;
    }
    """

    def __init__(self, msg, **kw):
        super().__init__("✗  ERROR", body=msg, **kw)
