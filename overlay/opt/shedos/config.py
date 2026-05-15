import json
import os
import tempfile

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("SHEDOS_MODEL", "claude-opus-4-6")
BETA_HEADER = "oauth-2025-04-20"
ANTHROPIC_VERSION = "2023-06-01"

TOKEN_PATH = "/etc/shedos/token"
PERSONA_PATH = "/etc/shedos/persona.txt"
PERSONA_CHOICE_PATH = "/etc/shedos/persona-choice"
PERSONAS_DIR = "/etc/shedos/personas"
STYLE_PATH = "/etc/shedos/style.json"
HISTORY_DIR = "/var/lib/shedos"
HISTORY_DIR_MODE = 0o700
HISTORY_FILE_MODE = 0o600
# Cap on how many prior messages from the persisted history we replay
# into a fresh brain process. Without a cap the request grows unbounded
# and eventually exceeds the model context. Override via $SHEDOS_MAX_HISTORY.
def _parse_max_history():
    raw = os.environ.get("SHEDOS_MAX_HISTORY", "200")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        import sys
        sys.stderr.write(
            f"[shedos] SHEDOS_MAX_HISTORY={raw!r} is not an int — defaulting to 200\n"
        )
        return 200
    if n < 0:
        import sys
        sys.stderr.write(
            f"[shedos] SHEDOS_MAX_HISTORY={raw!r} is negative — defaulting to 200 "
            "(deque(maxlen=...) rejects negatives)\n"
        )
        return 200
    return n

MAX_HISTORY_MESSAGES = _parse_max_history()

MAX_ITERATIONS = 30
MAX_TOKENS = 8192
HTTP_TIMEOUT_S = 120

CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

DEFAULT_PERSONA = (
    "You are ShedOS — a minimal Linux appliance where you ARE the shell.\n"
    "The user has no other interface. They type natural language; you accomplish\n"
    "tasks by calling tools. You run as root on Alpine Linux 3.23 aarch64.\n"
    "Use the bash tool for arbitrary commands, apk for package management,\n"
    "and the file/process/net tools when they fit better than shelling out.\n"
    "Be terse. Show your work briefly. When a task is done, say so in one line."
)


def load_token():
    try:
        with open(TOKEN_PATH, "r") as f:
            tok = f.read().strip()
        return tok or None
    except FileNotFoundError:
        return None


def save_token(token):
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(TOKEN_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(token.strip())
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


PERSONA_PRESETS = ("default", "coding", "sysadmin", "researcher")
DEFAULT_STYLE = {"terse": True, "formal": False, "emojis": False}


def _read_text(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def load_persona_choice():
    """Name of the active preset, or 'custom' when /etc/shedos/persona.txt
    exists and overrides everything. Falls back to 'default'."""
    if os.path.exists(PERSONA_PATH):
        return "custom"
    name = _read_text(PERSONA_CHOICE_PATH)
    if name in PERSONA_PRESETS:
        return name
    return "default"


def load_persona():
    """Return the active persona text. Resolution order:
      1. /etc/shedos/persona.txt (custom — overrides everything)
      2. /etc/shedos/personas/<choice>.txt
      3. /etc/shedos/personas/default.txt
      4. hardcoded DEFAULT_PERSONA
    """
    custom = _read_text(PERSONA_PATH)
    if custom:
        return custom
    choice = load_persona_choice()
    name = choice if choice in PERSONA_PRESETS else "default"
    preset = _read_text(os.path.join(PERSONAS_DIR, f"{name}.txt"))
    if preset:
        return preset
    fallback = _read_text(os.path.join(PERSONAS_DIR, "default.txt"))
    return fallback or DEFAULT_PERSONA


def load_style():
    try:
        with open(STYLE_PATH, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_STYLE)
        out = dict(DEFAULT_STYLE)
        for k in DEFAULT_STYLE:
            if k in data:
                out[k] = bool(data[k])
        return out
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(DEFAULT_STYLE)


def save_style(style):
    merged = dict(DEFAULT_STYLE)
    for k in DEFAULT_STYLE:
        if k in style:
            merged[k] = bool(style[k])
    os.makedirs(os.path.dirname(STYLE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STYLE_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(merged, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STYLE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return merged


def save_persona_choice(name):
    """Switch to a preset. Removes /etc/shedos/persona.txt so the preset
    is what shows up. Pass 'custom' + write persona.txt yourself if you
    want a one-off override.
    """
    if name not in PERSONA_PRESETS:
        raise ValueError(f"unknown persona preset: {name!r}")
    os.makedirs(os.path.dirname(PERSONA_CHOICE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PERSONA_CHOICE_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(name + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, PERSONA_CHOICE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:
        os.unlink(PERSONA_PATH)
    except FileNotFoundError:
        pass


def compose_system_prompt():
    """Build the system prompt the brain sends to Anthropic: persona +
    optional style modifier suffix.
    """
    persona = load_persona()
    style = load_style()
    modifiers = []
    if style.get("terse"):
        modifiers.append(
            "Be terse. One line of acknowledgement, then tool calls, "
            "then a one-line result. Don't pad responses with restatements "
            "or trailing summaries."
        )
    else:
        modifiers.append(
            "Be thorough. Explain context and tradeoffs alongside answers."
        )
    if style.get("formal"):
        modifiers.append("Use a formal, professional tone.")
    else:
        modifiers.append("Use a relaxed, conversational tone.")
    if not style.get("emojis"):
        modifiers.append("Do not use emojis in responses.")
    return persona + "\n\n" + "\n".join(modifiers)
