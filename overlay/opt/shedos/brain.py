#!/usr/bin/env python3
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tools
import ui
from anthropic_client import AnthropicError, Client


def _tty_name():
    try:
        return os.path.basename(os.ttyname(0))
    except OSError:
        return "default"


def _history_path():
    return os.path.join(config.DATA_DIR, f"brain-{_tty_name()}.jsonl")


def _wait_for_data():
    """If /data should be there (disk attached) give it a moment to mount."""
    for _ in range(config.HISTORY_WAIT_S):
        if os.path.ismount("/data"):
            return True
        if not os.path.exists("/dev/sda"):
            return False
        time.sleep(1)
    return os.path.ismount("/data")


def load_history():
    if not _wait_for_data():
        return []
    path = _history_path()
    try:
        msgs = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return msgs
    except FileNotFoundError:
        return []


def append_history(msg):
    if not os.path.ismount("/data"):
        return
    os.makedirs(config.DATA_DIR, exist_ok=True)
    try:
        with open(_history_path(), "a") as f:
            f.write(json.dumps(msg) + "\n")
    except OSError:
        pass


def get_token():
    tok = config.load_token()
    if tok:
        return tok
    return ui.bootstrap_token()


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
