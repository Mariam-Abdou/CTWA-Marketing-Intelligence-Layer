"""Thin JSON-mode client for the chat. Same key and endpoint as the
pipeline's reasoning layer (config.yaml: reasoning.base_url / api_key_env)."""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from ..config import load_config

_cfg = load_config()


def get_client():
    load_dotenv()
    key = os.environ.get(_cfg["reasoning"]["api_key_env"])
    return OpenAI(api_key=key, base_url=_cfg["reasoning"]["base_url"]) if key else None


def chat_json(client, model: str, system: str, user: str) -> tuple[dict | None, dict]:
    """Returns (parsed JSON or None, meta). meta always has latency_ms and,
    on failure, an error string -- callers fall back, never crash."""
    c = _cfg["chat"]
    meta = {"model": model}
    start = time.monotonic()
    kwargs = {"reasoning_effort": c["reasoning_effort"]} if c.get("reasoning_effort") else {}
    try:
        resp = client.chat.completions.create(
            model=model, temperature=c["temperature"], max_tokens=c["max_tokens"],
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        meta["latency_ms"] = round((time.monotonic() - start) * 1000)
        if resp.usage:
            meta["input_tokens"] = resp.usage.prompt_tokens
            meta["output_tokens"] = resp.usage.completion_tokens
        return json.loads(resp.choices[0].message.content or ""), meta
    except Exception as exc:  # network, rate limit, bad JSON
        meta["latency_ms"] = round((time.monotonic() - start) * 1000)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return None, meta
