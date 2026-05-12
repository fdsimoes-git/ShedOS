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
        messages.append({"role": "assistant", "content": content})
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
        messages.append({"role": "user", "content": results})

    sys.stdout.write("\n[brain] hit max iterations\n")
    sys.stdout.flush()


def main():
    token = get_token()
    persona = config.load_persona()
    client = Client(token)
    ui.banner()

    messages = []
    while True:
        try:
            user = ui.prompt("> ")
        except Exception as e:
            sys.stdout.write(f"\n[brain] prompt error: {e}\n")
            time.sleep(1)
            continue
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        turn(client, persona, messages)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
