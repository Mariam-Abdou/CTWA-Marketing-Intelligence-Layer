"""Thin JSON-mode client for the chat. Same key and endpoint as the
pipeline's reasoning layer (config.yaml: reasoning.base_url / api_key_env)."""

import json
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI

from ..config import load_config
from .budget import BUDGETS

_cfg = load_config()


def get_client():
    """Short timeout, one retry: a chat cannot wait minutes. The OpenAI SDK
    default (600 s timeout, 2 retries, honouring long retry-after on 429)
    made one failed question take 3 minutes."""
    load_dotenv()
    key = os.environ.get(_cfg["reasoning"]["api_key_env"])
    if not key:
        return None
    c = _cfg["chat"]
    return OpenAI(api_key=key, base_url=_cfg["reasoning"]["base_url"],
                  timeout=c.get("timeout_seconds", 20), max_retries=c.get("max_retries", 1))


def retry_after(exc) -> float:
    """Seconds the provider asks us to wait after a 429."""
    resp = getattr(exc, "response", None)
    try:
        header = resp.headers.get("retry-after") if resp is not None else None
        if header:
            return float(header)
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", str(exc))
    if m:
        secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        return secs + 60 * float(m.group(1) or 0)
    return 30.0


def is_rate_limit(exc) -> bool:
    return type(exc).__name__ == "RateLimitError" or getattr(exc, "status_code", None) == 429 \
        or "429" in str(exc)[:40]


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
            BUDGETS[model].record(resp.usage.prompt_tokens + resp.usage.completion_tokens)
        return json.loads(resp.choices[0].message.content or ""), meta
    except Exception as exc:  # network, rate limit, bad JSON
        meta["latency_ms"] = round((time.monotonic() - start) * 1000)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if is_rate_limit(exc):
            meta["rate_limited"] = True
            meta["retry_after"] = retry_after(exc)
            BUDGETS[model].block_for(meta["retry_after"])
        return None, meta
