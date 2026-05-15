"""Theme definitions for the Textual TUI.

Each theme returns a dict that maps to Textual's CSS variables. The app
applies them via `app.theme = "<name>"` after registering Theme objects.

Color picker philosophy: pair a strong primary, a softer surface for
card backgrounds, and accent colors that pop for tool calls / errors.
"""
from dataclasses import dataclass


@dataclass
class Palette:
    name: str
    surface: str          # app background
    panel: str            # card background (slightly lighter than surface)
    panel_user: str       # background tint for user cards
    panel_assistant: str  # background tint for assistant cards
    panel_tool: str       # background tint for tool cards
    primary: str
    secondary: str
    accent: str
    success: str
    warning: str
    error: str
    foreground: str
    muted: str


PALETTES = {
    "nord": Palette(
        name="nord",
        surface="#2e3440",
        panel="#3b4252",
        panel_user="#3b5a4a",
        panel_assistant="#3a4d5e",
        panel_tool="#4a4530",
        primary="#88c0d0",
        secondary="#81a1c1",
        accent="#5e81ac",
        success="#a3be8c",
        warning="#ebcb8b",
        error="#bf616a",
        foreground="#eceff4",
        muted="#4c566a",
    ),
    "dracula": Palette(
        name="dracula",
        surface="#282a36",
        panel="#44475a",
        panel_user="#3a4a3a",
        panel_assistant="#3e3a52",
        panel_tool="#52483a",
        primary="#bd93f9",
        secondary="#ff79c6",
        accent="#8be9fd",
        success="#50fa7b",
        warning="#f1fa8c",
        error="#ff5555",
        foreground="#f8f8f2",
        muted="#6272a4",
    ),
    "tokyo-night": Palette(
        name="tokyo-night",
        surface="#1a1b26",
        panel="#24283b",
        panel_user="#2a3a2a",
        panel_assistant="#2a2f4a",
        panel_tool="#3a3525",
        primary="#7aa2f7",
        secondary="#bb9af7",
        accent="#7dcfff",
        success="#9ece6a",
        warning="#e0af68",
        error="#f7768e",
        foreground="#c0caf5",
        muted="#565f89",
    ),
    "gruvbox": Palette(
        name="gruvbox",
        surface="#282828",
        panel="#3c3836",
        panel_user="#3a4a2a",
        panel_assistant="#503a2a",
        panel_tool="#503e25",
        primary="#fabd2f",
        secondary="#8ec07c",
        accent="#d3869b",
        success="#b8bb26",
        warning="#fe8019",
        error="#fb4934",
        foreground="#ebdbb2",
        muted="#928374",
    ),
    "solarized-dark": Palette(
        name="solarized-dark",
        surface="#002b36",
        panel="#073642",
        panel_user="#1a3a2a",
        panel_assistant="#0a3a4a",
        panel_tool="#3a3025",
        primary="#268bd2",
        secondary="#2aa198",
        accent="#d33682",
        success="#859900",
        warning="#b58900",
        error="#dc322f",
        foreground="#839496",
        muted="#586e75",
    ),
    "monokai": Palette(
        name="monokai",
        surface="#272822",
        panel="#3e3d32",
        panel_user="#3a4a2a",
        panel_assistant="#2a3a4a",
        panel_tool="#4a3525",
        primary="#a6e22e",
        secondary="#66d9ef",
        accent="#f92672",
        success="#a6e22e",
        warning="#fd971f",
        error="#f92672",
        foreground="#f8f8f2",
        muted="#75715e",
    ),
}

DEFAULT_THEME = "nord"


def names():
    return list(PALETTES.keys())


def get(name) -> Palette:
    return PALETTES.get(name, PALETTES[DEFAULT_THEME])


def load_saved(path="/var/lib/shedos/theme"):
    try:
        with open(path) as f:
            n = f.read().strip()
        if n in PALETTES:
            return n
    except OSError:
        pass
    return DEFAULT_THEME


def save(name, path="/var/lib/shedos/theme"):
    if name not in PALETTES:
        return False
    try:
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(name + "\n")
        return True
    except OSError:
        return False


def textual_theme(name):
    """Build a textual.theme.Theme from a palette."""
    from textual.theme import Theme
    p = get(name)
    return Theme(
        name=name,
        primary=p.primary,
        secondary=p.secondary,
        accent=p.accent,
        success=p.success,
        warning=p.warning,
        error=p.error,
        background=p.surface,
        surface=p.panel,
        panel=p.panel,
        foreground=p.foreground,
        dark=True,
        variables={
            "panel-user": p.panel_user,
            "panel-assistant": p.panel_assistant,
            "panel-tool": p.panel_tool,
            "muted": p.muted,
        },
    )
