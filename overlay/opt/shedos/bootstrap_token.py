#!/usr/bin/env python3
"""First-boot token prompt — replaces the old ui.bootstrap_token loop.

Invoked by run-tui.sh on tty1/ttyS0 when /etc/shedos/token is missing.
Loops until a valid-looking token is provided, then writes it (mode 0600).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config


BANNER = """
╔══════════════════════════════════════════════════════════════╗
║  ShedOS — first boot                                         ║
║                                                              ║
║  No Claude Code OAuth token found.                           ║
║  On your laptop run:  claude setup-token                     ║
║  Paste the sk-ant-oat01-... value below.                     ║
╚══════════════════════════════════════════════════════════════╝
"""


def main():
    if config.load_token():
        return 0
    sys.stdout.write(BANNER)
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
        sys.stdout.write("Token saved.\n")
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    sys.exit(main())
