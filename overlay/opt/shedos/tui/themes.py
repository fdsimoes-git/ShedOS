"""Color themes for the TUI. Each theme maps semantic roles to colors.

Colors are written as truecolor hex strings; rich + prompt_toolkit both
support them on terminals that advertise truecolor (most do today).
"""

THEMES = {
    "nord": {
        "background": "#2e3440",
        "foreground": "#eceff4",
        "primary": "#88c0d0",       # cyan-blue
        "secondary": "#81a1c1",     # blue
        "accent": "#5e81ac",        # darker blue
        "user": "#a3be8c",          # green
        "assistant": "#88c0d0",     # cyan-blue
        "tool_running": "#ebcb8b",  # yellow
        "tool_ok": "#a3be8c",       # green
        "tool_err": "#bf616a",      # red
        "muted": "#4c566a",         # gray
        "code_bg": "#3b4252",
    },
    "dracula": {
        "background": "#282a36",
        "foreground": "#f8f8f2",
        "primary": "#bd93f9",       # purple
        "secondary": "#ff79c6",     # pink
        "accent": "#8be9fd",        # cyan
        "user": "#50fa7b",          # green
        "assistant": "#bd93f9",     # purple
        "tool_running": "#f1fa8c",  # yellow
        "tool_ok": "#50fa7b",
        "tool_err": "#ff5555",
        "muted": "#6272a4",
        "code_bg": "#44475a",
    },
    "tokyo-night": {
        "background": "#1a1b26",
        "foreground": "#c0caf5",
        "primary": "#7aa2f7",
        "secondary": "#bb9af7",
        "accent": "#7dcfff",
        "user": "#9ece6a",
        "assistant": "#7aa2f7",
        "tool_running": "#e0af68",
        "tool_ok": "#9ece6a",
        "tool_err": "#f7768e",
        "muted": "#565f89",
        "code_bg": "#24283b",
    },
    "gruvbox": {
        "background": "#282828",
        "foreground": "#ebdbb2",
        "primary": "#fabd2f",       # yellow
        "secondary": "#8ec07c",     # aqua
        "accent": "#d3869b",        # purple
        "user": "#b8bb26",          # green
        "assistant": "#fabd2f",
        "tool_running": "#fe8019",  # orange
        "tool_ok": "#b8bb26",
        "tool_err": "#fb4934",
        "muted": "#928374",
        "code_bg": "#3c3836",
    },
    "solarized-dark": {
        "background": "#002b36",
        "foreground": "#839496",
        "primary": "#268bd2",
        "secondary": "#2aa198",
        "accent": "#d33682",
        "user": "#859900",
        "assistant": "#268bd2",
        "tool_running": "#b58900",
        "tool_ok": "#859900",
        "tool_err": "#dc322f",
        "muted": "#586e75",
        "code_bg": "#073642",
    },
    "monokai": {
        "background": "#272822",
        "foreground": "#f8f8f2",
        "primary": "#a6e22e",
        "secondary": "#66d9ef",
        "accent": "#f92672",
        "user": "#a6e22e",
        "assistant": "#66d9ef",
        "tool_running": "#fd971f",
        "tool_ok": "#a6e22e",
        "tool_err": "#f92672",
        "muted": "#75715e",
        "code_bg": "#3e3d32",
    },
}

DEFAULT_THEME = "nord"


def load_saved_theme(path="/var/lib/shedos/theme"):
    try:
        with open(path) as f:
            name = f.read().strip()
        if name in THEMES:
            return name
    except OSError:
        pass
    return DEFAULT_THEME


def save_theme(name, path="/var/lib/shedos/theme"):
    if name not in THEMES:
        return False
    try:
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(name + "\n")
        return True
    except OSError:
        return False


def get(name):
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def names():
    return list(THEMES.keys())
