#!/usr/bin/env python3
"""ShedOS brain — daemon entry point.

Runs as a long-lived asyncio service that:
  * Listens on /run/shedos-brain.sock for JSON-RPC clients (the TUI)
  * Also accepts raw-text clients on the same socket for `nc -U` legacy
  * Holds N concurrent conversation sessions in memory, persisted to
    /var/lib/shedos/sessions/<id>.jsonl
  * For each user message: streams events back (text deltas, tool_use,
    tool_result, end_turn).

This used to be an interactive loop driven by a single tty. The TUI
(prompt_toolkit + rich) is the new front-end and lives in tui/.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import tools
from anthropic_client import AnthropicError, Client
from rpc_server import RpcServer


def _summarize_args(args, limit=80):
    try:
        s = json.dumps(args, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        s = str(args)
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


async def turn(client, persona, session, user_text):
    """Run one conversational turn. Yields events for the RPC layer.

    Each event is a dict with at least {event: "..."}; the RPC layer
    adds {req_id} and writes JSON-line to the client.
    """
    # Append + persist user message
    user_msg = {"role": "user", "content": user_text}
    session.append(user_msg)
    yield {"event": "user_msg", "msg": user_msg}

    messages = session.messages

    for _ in range(config.MAX_ITERATIONS):
        try:
            # client.messages is sync (httpx.Client), run in a thread so we
            # don't block the event loop while Anthropic is processing.
            resp = await asyncio.to_thread(
                client.messages, messages, tools.SCHEMAS, persona
            )
        except AnthropicError as e:
            yield {"event": "error", "msg": f"anthropic: {e}"}
            return

        content = resp.get("content", [])
        assistant_msg = {"role": "assistant", "content": content}
        session.append(assistant_msg)

        # Emit text + tool_use blocks as discrete events so the TUI can
        # render markdown text vs animated tool spinners differently.
        for block in content:
            t = block.get("type")
            if t == "text":
                yield {"event": "assistant_text", "chunk": block.get("text", "")}
            elif t == "tool_use":
                yield {
                    "event": "tool_use",
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input") or {},
                    "input_summary": _summarize_args(block.get("input") or {}),
                }

        stop = resp.get("stop_reason")
        if stop in ("end_turn", "stop_sequence", "max_tokens"):
            yield {"event": "end_turn", "stop_reason": stop}
            return
        if stop != "tool_use":
            yield {"event": "end_turn", "stop_reason": stop or "unknown"}
            return

        # Dispatch tools
        results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            out = await asyncio.to_thread(
                tools.dispatch, block.get("name"), block.get("input") or {}
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(out),
                }
            )
            yield {
                "event": "tool_result",
                "id": block["id"],
                "name": block.get("name"),
                "output": out,
            }
        if not results:
            yield {"event": "end_turn", "stop_reason": "no_tools"}
            return
        tool_msg = {"role": "user", "content": results}
        session.append(tool_msg)

    yield {"event": "error", "msg": f"hit max iterations ({config.MAX_ITERATIONS})"}


async def amain():
    token = config.load_token()
    if not token:
        sys.stderr.write(
            "[brain] no token at /etc/shedos/token — set one before starting daemon\n"
        )
        sys.exit(1)
    persona = config.load_persona()
    client = Client(token)

    async def handler(session, text):
        async for ev in turn(client, persona, session, text):
            yield ev

    server = RpcServer(brain_handler=handler)
    await server.start()


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
