#!/usr/bin/env python3
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tools
import ui
from anthropic_client import AnthropicError, Client


def get_token():
    tok = config.load_token()
    if tok:
        return tok
    return ui.bootstrap_token()


def _tty_name():
    try:
        return os.path.basename(os.ttyname(0))
    except OSError:
        return "default"


def _history_path():
    return os.path.join(config.HISTORY_DIR, f"brain-{_tty_name()}.jsonl")


def _ensure_history_dir():
    try:
        os.makedirs(config.HISTORY_DIR, mode=config.HISTORY_DIR_MODE, exist_ok=True)
        os.chmod(config.HISTORY_DIR, config.HISTORY_DIR_MODE)
    except OSError:
        pass


def load_history():
    """Return up to MAX_HISTORY_MESSAGES of recent persisted messages.

    Streams the JSONL file into a deque(maxlen=cap) so memory + startup
    cost stays O(cap), not O(file_size) — the on-disk log is unbounded.

    On any I/O error, returns []. Conversation history may contain
    sensitive content, so the directory + files are 0700/0600.
    """
    path = _history_path()
    cap = config.MAX_HISTORY_MESSAGES
    try:
        buf = collections.deque(maxlen=cap)
        total = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    buf.append(json.loads(line))
                    total += 1
                except json.JSONDecodeError:
                    continue
        if total > cap:
            sys.stdout.write(
                f"[brain] history has {total} messages; replaying only the "
                f"last {cap} (cap via SHEDOS_MAX_HISTORY)\n"
            )
            sys.stdout.flush()
        return list(buf)
    except FileNotFoundError:
        return []
    except OSError as e:
        sys.stdout.write(f"[brain] history load failed ({e}); starting fresh\n")
        sys.stdout.flush()
        return []


def append_history(msg):
    try:
        _ensure_history_dir()
        path = _history_path()
        with open(path, "a") as f:
            f.write(json.dumps(msg) + "\n")
        # Best-effort: enforce 0600 on every append, in case a previous
        # build (or manual creation) left the file world-readable.
        try:
            os.chmod(path, config.HISTORY_FILE_MODE)
        except OSError:
            pass
    except OSError:
        pass


def turn(client, persona, messages):
    for _ in range(config.MAX_ITERATIONS):
        try:
            resp = client.messages(messages, tools.SCHEMAS, persona)
        except AnthropicError as e:
            sys.stdout.write(f"\n[brain] anthropic error: {e}\n")
            sys.stdout.flush()
            return

        content = resp.get("content", [])
        assistant_msg = {"role": "assistant", "content": content}
        messages.append(assistant_msg)
        append_history(assistant_msg)
        ui.render(content)

        stop = resp.get("stop_reason")
        if stop in ("end_turn", "stop_sequence", "max_tokens"):
            return
        if stop != "tool_use":
            return

        results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            out = tools.dispatch(block.get("name"), block.get("input") or {})
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(out),
                }
            )
        if not results:
            return
        tool_msg = {"role": "user", "content": results}
        messages.append(tool_msg)
        append_history(tool_msg)

    sys.stdout.write("\n[brain] hit max iterations\n")
    sys.stdout.flush()


def main():
    token = get_token()
    persona = config.load_persona()
    client = Client(token)
    ui.banner()

    messages = load_history()
    if messages:
        sys.stdout.write(
            f"[brain] resumed with {len(messages)} prior messages "
            f"from {_history_path()}\n"
        )
        sys.stdout.flush()

    while True:
        try:
            user = ui.prompt("> ")
        except Exception as e:
            sys.stdout.write(f"\n[brain] prompt error: {e}\n")
            time.sleep(1)
            continue
        if not user:
            continue
        user_msg = {"role": "user", "content": user}
        messages.append(user_msg)
        append_history(user_msg)
        turn(client, persona, messages)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
