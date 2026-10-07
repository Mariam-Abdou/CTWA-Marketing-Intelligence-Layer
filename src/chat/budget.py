"""
Tokens-per-minute bookkeeping for the chat. 
Groq's free tier allows 8,000 tokens/minute per model.
A question costs ~2,000 on the gate model and ~3,500 on the answer model.
Knowing that BEFORE calling the LLM, preventing bot failing mid-sentence,
responding with "busy, try again in 20 s".
"""

import threading
import time

from ..config import load_config

_cfg = load_config()["chat"]


class TokenBudget:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.events: list[tuple[float, int]] = []
        self.blocked_until = 0.0          # set from a provider 429 retry-after
        self.lock = threading.Lock()

    def _trim(self, now):
        self.events = [(t, n) for t, n in self.events if now - t < 60]

    def record(self, tokens: int):
        with self.lock:
            self.events.append((time.monotonic(), int(tokens or 0)))

    def block_for(self, seconds: float):
        with self.lock:
            self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)

    def wait_seconds(self, need: int) -> float:
        """0 if `need` tokens fit in the current minute, else seconds to wait."""
        with self.lock:
            now = time.monotonic()
            self._trim(now)
            wait = max(0.0, self.blocked_until - now)
            used = sum(n for _, n in self.events)
            if used + need > self.per_minute:
                # drop the oldest calls until the new one would fit
                freed = 0
                for t, n in self.events:
                    freed += n
                    if used - freed + need <= self.per_minute:
                        wait = max(wait, 60 - (now - t))
                        break
                else:
                    wait = max(wait, 60.0)
            return round(wait, 1)


class Budgets:
    """One TokenBudget per model -- the provider limits each model separately."""

    def __init__(self, per_minute):
        self.per_minute, self.by_model, self.lock = per_minute, {}, threading.Lock()

    def __getitem__(self, model) -> TokenBudget:
        with self.lock:
            return self.by_model.setdefault(model, TokenBudget(self.per_minute))


BUDGETS = Budgets(_cfg.get("tokens_per_minute", 8000))
