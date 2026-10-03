"""
Quick health check for the chat's LLM calls. Run it when the chat is slow
or falls back:   python3 -m scripts.check_llm

Sends one tiny request to the gate model and the answer model and prints
status, latency and the exact error (bad key, unknown model, rate limit,
no network...).
"""

import time

from src.config import load_config
from src.chat.llm import get_client

cfg = load_config()
client = get_client()
if client is None:
    raise SystemExit(f"No key: put {cfg['reasoning']['api_key_env']}=... in .env in this folder.")
print(f"endpoint: {cfg['reasoning']['base_url']}")
for role in ("gate_model", "answer_model"):
    model = cfg["chat"][role]
    t = time.monotonic()
    try:
        r = client.chat.completions.create(model=model, max_tokens=50,
                                           messages=[{"role": "user", "content": "Reply with: ok"}])
        print(f"OK    {role:<13} {model:<24} {round((time.monotonic()-t)*1000):>6} ms  "
              f"-> {(r.choices[0].message.content or '').strip()[:30]!r}")
    except Exception as exc:
        print(f"FAIL  {role:<13} {model:<24} {round((time.monotonic()-t)*1000):>6} ms  "
              f"-> {type(exc).__name__}: {str(exc)[:200]}")
