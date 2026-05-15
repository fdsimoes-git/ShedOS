#!/usr/bin/env python3
"""ShedOS installer wizard.

Runs on ttyS0 of the live installer ISO (launched by run-installer.sh
via getty). Collects: token override, persona preset, and conversation
style flags. Writes /tmp/shedos-wizard.env, then exec's installer.sh
which sources the env and applies the choices during apply_overlay.

The actual disk install (parted/mkfs/apk/chroot/grub) stays in
installer.sh — this wizard is purely the UX frontend.

Failure modes:
- Ctrl-C / EOF mid-wizard: exit with the existing baked-in defaults so
  installer.sh runs unchanged. The user can rerun the wizard from a
  rescue tty, or just live with default persona + terse style.
- rich not importable: fall back to plain prompts (still functional).

The wizard is designed for a serial terminal (no fancy curses), so it
sticks to line-by-line prompts and avoids cursor positioning.
"""
import getpass
import os
import re
import sys

WIZARD_ENV = "/tmp/shedos-wizard.env"
INSTALLER = "/opt/shedos-installer/installer.sh"

PERSONA_PRESETS = [
    ("default",    "Default",
     "All-purpose ShedOS shell. Terse, tool-driven."),
    ("coding",     "Coding Assistant",
     "Software-engineering focused. Defaults to git, tests, small diffs."),
    ("sysadmin",   "Sysadmin",
     "OpenRC + apk fluent. Confirms destructive ops."),
    ("researcher", "Researcher",
     "Pulls papers/data, renders PDFs and figures. Cites sources."),
]

# --- Output helpers (rich if available, plain otherwise) ---------------------

try:
    from rich.console import Console
    from rich.panel import Panel
    _console = Console(force_terminal=True, color_system="standard")

    def out(msg=""):
        _console.print(msg)

    def panel(title, body, style="cyan"):
        _console.print(Panel(body, title=title, border_style=style,
                              padding=(1, 2)))

    def heading(text):
        _console.rule(f"[bold cyan]{text}[/bold cyan]")
except ImportError:
    def out(msg=""):
        sys.stdout.write(str(msg) + "\n")
        sys.stdout.flush()

    def panel(title, body, style=None):
        bar = "=" * 60
        out(bar)
        out(f"  {title}")
        out(bar)
        out(body)
        out(bar)

    def heading(text):
        out("")
        out(f"--- {text} ---")
        out("")

# --- Prompts -----------------------------------------------------------------

def ask(prompt, default=None, validate=None):
    """Single-line prompt. validate(str) -> (ok: bool, msg: str|None)."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            out("\n[wizard] aborted; using defaults\n")
            return default
        if not raw and default is not None:
            raw = default
        if validate is None:
            return raw
        ok, msg = validate(raw)
        if ok:
            return raw
        out(f"  ! {msg}")


def ask_yn(prompt, default_yes=True):
    suffix = " [Y/n]" if default_yes else " [y/N]"
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return default_yes
        if not raw:
            return default_yes
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def ask_choice(prompt, options, default_idx=0):
    """options: list of (key, label, description) tuples. Returns key."""
    out("")
    for i, (_, label, desc) in enumerate(options, 1):
        marker = " *" if i - 1 == default_idx else "  "
        out(f"  {marker} {i}. {label} — {desc}")
    out("")
    while True:
        try:
            raw = input(f"{prompt} [{default_idx + 1}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return options[default_idx][0]
        if not raw:
            return options[default_idx][0]
        try:
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1][0]
        except ValueError:
            pass
        out(f"  ! enter a number between 1 and {len(options)}")


def ask_token(default_present):
    """Returns token string or None to keep the baked-in token."""
    out("")
    if default_present:
        out("  A Claude Code OAuth token was baked into the ISO at build time.")
        out("  Press Enter to keep it, or paste a new one to override.")
    else:
        out("  No token was baked into the ISO.")
        out("  Paste your Claude Code OAuth token now, or press Enter to skip")
        out("  (the brain will prompt on first boot if you skip).")
    out("")
    try:
        raw = getpass.getpass("token (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    if not re.match(r"^sk-ant-oat\d+-", raw):
        out("  ! that doesn't look like a Claude Code OAuth token "
            "(expected sk-ant-oat01-...).")
        if not ask_yn("  use it anyway?", default_yes=False):
            return ask_token(default_present)
    return raw

# --- Main flow ---------------------------------------------------------------

def render_summary(token_override, persona_name, style):
    rows = []
    if token_override is None:
        rows.append("  Token         (keep ISO default)")
    else:
        masked = token_override[:14] + "…" + token_override[-4:]
        rows.append(f"  Token         {masked}  (override)")
    rows.append(f"  Persona       {persona_name}")
    rows.append(f"  Style         terse={style['terse']}  "
                f"formal={style['formal']}  emojis={style['emojis']}")
    panel("Confirm install", "\n".join(rows), style="yellow")


def write_wizard_env(token_override, persona_name, style):
    lines = [f"# Generated by wizard.py at install time", ""]
    if token_override:
        # Single-quote and escape any single quotes in the token. apk-style
        # POSIX shell sourcing will read this safely.
        safe = token_override.replace("'", "'\\''")
        lines.append(f"TOKEN_OVERRIDE='{safe}'")
    lines.append(f"PERSONA_NAME='{persona_name}'")
    lines.append(f"STYLE_TERSE={1 if style['terse'] else 0}")
    lines.append(f"STYLE_FORMAL={1 if style['formal'] else 0}")
    lines.append(f"STYLE_EMOJIS={1 if style['emojis'] else 0}")
    with open(WIZARD_ENV, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(WIZARD_ENV, 0o600)


def main():
    panel("ShedOS installer",
          "Welcome. This will install Alpine Linux + ShedOS to /dev/sda.\n"
          "All data on /dev/sda will be erased.\n\n"
          "The wizard collects a few preferences, then hands off to the\n"
          "installer. The whole thing takes ~3-5 minutes.",
          style="cyan")

    if not ask_yn("Continue?", default_yes=True):
        out("[wizard] cancelled — sleeping forever (reset the VM to retry)")
        while True:
            try:
                import time
                time.sleep(3600)
            except KeyboardInterrupt:
                pass

    # Step 1: token
    heading("Step 1 of 3 — Claude Code OAuth token")
    token_present = os.path.exists("/etc/shedos/token") and \
                    os.path.getsize("/etc/shedos/token") > 0
    token_override = ask_token(token_present)

    # Step 2: persona preset
    heading("Step 2 of 3 — Persona")
    out("Choose how the brain should present itself. You can switch")
    out("personas later from the GUI settings panel.")
    persona = ask_choice("persona", PERSONA_PRESETS, default_idx=0)

    # Step 3: conversation style
    heading("Step 3 of 3 — Conversation style")
    style = {
        "terse":  ask_yn("Be terse (one-line replies, minimal narration)?",
                          default_yes=True),
        "formal": ask_yn("Use a formal tone?", default_yes=False),
        "emojis": ask_yn("Allow emojis in responses?", default_yes=False),
    }

    # Confirm
    out("")
    render_summary(token_override, persona, style)
    if not ask_yn("Proceed with these settings and install?", default_yes=True):
        out("[wizard] cancelled — restart the VM to retry")
        sys.exit(1)

    write_wizard_env(token_override, persona, style)
    out("")
    out("[wizard] choices saved to /tmp/shedos-wizard.env")
    out("[wizard] handing off to installer.sh ...")
    out("")
    os.execv(INSTALLER, [INSTALLER])


if __name__ == "__main__":
    main()
