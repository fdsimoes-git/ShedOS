import json
import sys

import config

BANNER = """
╔══════════════════════════════════════════════════════════╗
║  ShedOS 0.1 — Claude is your shell                       ║
║  Model: {model:<48s} ║
║  Backend: api.anthropic.com (OAuth)                      ║
╚══════════════════════════════════════════════════════════╝
"""

FIRSTBOOT = """
ShedOS 0.1 — first boot
─────────────────────────────────────────────────────────────
No Claude Code OAuth token found.

  On your laptop run:  claude setup-token
  Paste the sk-ant-oat01-... value below.
"""


def banner():
    sys.stdout.write(BANNER.format(model=config.MODEL))
    sys.stdout.write("Ready.\n")
    sys.stdout.flush()


def bootstrap_token():
    sys.stdout.write(FIRSTBOOT)
    sys.stdout.flush()
    while True:
        try:
            tok = input("Token: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            continue
        if not tok:
            sys.stdout.write("Empty input — try again.\n")
            continue
        if not tok.startswith("sk-ant-oat01-"):
            sys.stdout.write(
                "Doesn't look like a Claude Code OAuth token "
                "(should start with sk-ant-oat01-). Try again.\n"
            )
            continue
        config.save_token(tok)
        sys.stdout.write("Token saved.\n\n")
        sys.stdout.flush()
        return tok


def _summarize_args(args, limit=80):
    try:
        s = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        s = str(args)
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def render(content):
    for block in content:
        t = block.get("type")
        if t == "text":
            sys.stdout.write(block.get("text", ""))
            sys.stdout.write("\n")
        elif t == "tool_use":
            sys.stdout.write(
                f"[tool: {block.get('name')}({_summarize_args(block.get('input'))})]\n"
            )
    sys.stdout.flush()


def prompt(label="> "):
    try:
        return input(label).strip()
    except EOFError:
        return ""
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return ""
