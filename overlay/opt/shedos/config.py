import os
import tempfile

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("SHEDOS_MODEL", "claude-opus-4-6")
BETA_HEADER = "oauth-2025-04-20"
ANTHROPIC_VERSION = "2023-06-01"

TOKEN_PATH = "/etc/shedos/token"
PERSONA_PATH = "/etc/shedos/persona.txt"
DATA_DIR = "/data/shedos"
HISTORY_WAIT_S = 15

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


def load_persona():
    try:
        with open(PERSONA_PATH, "r") as f:
            text = f.read().strip()
        return text or DEFAULT_PERSONA
    except FileNotFoundError:
        return DEFAULT_PERSONA
