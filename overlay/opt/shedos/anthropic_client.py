import json
import sys

import httpx

import config


class AnthropicError(Exception):
    pass


class Client:
    def __init__(self, token):
        self.token = token
        self.http = httpx.Client(timeout=config.HTTP_TIMEOUT_S)

    def close(self):
        self.http.close()

    def messages(self, messages, tools, persona):
        system_blocks = [
            {"type": "text", "text": config.CLAUDE_CODE_IDENTITY},
            {"type": "text", "text": persona},
        ]
        body = {
            "model": config.MODEL,
            "max_tokens": config.MAX_TOKENS,
            "system": system_blocks,
            "tools": tools,
            "messages": messages,
        }
        headers = {
            "authorization": f"Bearer {self.token}",
            "anthropic-beta": config.BETA_HEADER,
            "anthropic-version": config.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            r = self.http.post(config.API_URL, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise AnthropicError(f"transport error: {e}") from e

        if r.status_code >= 400:
            sys.stderr.write(
                f"\n[anthropic {r.status_code}] {r.text}\n"
            )
            raise AnthropicError(
                f"HTTP {r.status_code}: {r.text[:500]}"
            )
        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise AnthropicError(f"bad json: {e}: {r.text[:500]}") from e
