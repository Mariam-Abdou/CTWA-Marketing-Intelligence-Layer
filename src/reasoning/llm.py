"""
Shared plumbing for every LLM task in this project.

The boundary this whole package exists to enforce: the LLM writes, it never
decides. score, action, bucket and the 70/30 split are computed upstream and
arrive here finished; nothing in this package can change them.

What keeps a generated sentence honest is the prompt and the facts it is
given: the model only ever sees values already computed upstream, written out
in the words a merchant would use.

There was also a check that matched every number in the output against those
facts. It was removed. Over ~50 generations it rejected four correct sentences
and caught no invented one, and each fix to it was another exception (digits
inside a name, inside the stop rule, a hyphen read as a minus sign). It also
guarded the wrong surface: every wrong claim actually observed -- calling
unsettled evidence "solid", predicting that scaling "should boost sales",
inventing a budget cut -- contained no number at all and passed it untouched.
Those were fixed in the prompt, which is what was doing the work throughout.

KNOWN LIMITATION, and it must be stated in the write-up: nothing now stops a
fabricated figure from reaching the output. The numbers in the "why" column are
computed and safe; the sentence beside them is not independently verified.

Any failure -- no key, no network, rate limit, truncation -- still falls back
to a deterministic template, so the pipeline runs fully offline and always
produces its column.

Generations are cached by a hash of (task, facts, model): unchanged data
re-uses its sentence instead of re-generating it. That is what makes this
layer reproducible -- not the temperature.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from ..config import load_config

_conf = load_config()
_cfg = _conf["reasoning"]
_MIN_N = _conf["decision"]["min_n_for_action"]

BASE_URL = _cfg["base_url"]
MODEL = _cfg["model"]
API_KEY_ENV = _cfg["api_key_env"]
MAX_TOKENS = _cfg["max_tokens"]
TEMPERATURE = _cfg["temperature"]
MIN_INTERVAL = _cfg["min_interval_seconds"]
# gpt-oss models only; None for non-reasoning models.
REASONING_EFFORT = _cfg.get("reasoning_effort")
CACHE_PATH = Path(_cfg["cache_path"])

LEVEL_NOUN = {"campaign": "campaign", "adset": "audience", "ad": "creative"}

# "exploit"/"explore" are our internal bucket names. Translated here so they
# never reach a prompt -- a merchant does not know what an exploit is, and the
# model repeated the word back verbatim when it saw it.
BUCKET_PLAIN = {
    "exploit": "gets the main share of the next budget",
    "explore": "gets a small budget to keep testing",
    "kill": "stop spending on it",
    "none": "no budget change either way",
}

REJECT_LOG = CACHE_PATH.parent / "reasoning_rejected.log"


def pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "n/a"


@dataclass
class DecisionFacts:
    """The only thing any prompt in this package is allowed to see. No message
    text, no ids-as-features, no raw data -- just what the pipeline computed."""
    level: str
    id: str
    name: str
    detail: str
    action: str
    bucket: str
    successes: int
    n: int
    excluded: int
    raw_rate: float | None
    score: float | None
    interval: tuple[float, float] | None
    baseline: float | None
    roas: float | None
    budget_share: float
    roas_baseline: float | None = None
    is_fatigued: bool = False
    is_underperforming: bool = False
    is_warned: bool = False
    confidence: float | None = None
    stop_rule: str | None = None

    @property
    def is_thin(self) -> bool:
        """Whether the evidence is actually thin. Decided here because the model
        cannot judge it: told only "say so if evidence is thin", it called 74
        conversations -- the second-largest sample in the account -- a small
        data set."""
        return self.n < _MIN_N

    def fingerprint(self, task: str) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(f"{task}|{MODEL}|{payload}".encode()).hexdigest()[:16]


# -- cache -------------------------------------------------------------------

def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


# -- generation --------------------------------------------------------------

def client():
    """None means no LLM this run; every caller then uses its template."""
    load_dotenv()
    key = os.environ.get(API_KEY_ENV)
    return OpenAI(api_key=key, base_url=BASE_URL) if key else None


_last_call = 0.0


def generate(*, key: str, system: str, user: str, fallback: str,
             client, cache: dict, name: str = "") -> tuple[str, str]:
    """Returns (text, source). source is llm | cache | template:<reason>.
    The reason matters: without it a run of all-templates looks identical to a
    run where the LLM worked."""
    global _last_call

    # The prompt is part of what produced the text, so it has to be part of the
    # key -- otherwise editing a prompt silently serves the old wording forever.
    key = hashlib.sha256(f"{key}|{system}".encode()).hexdigest()[:16]

    if key in cache:
        return cache[key], "cache"
    if client is None:
        return fallback, "template:no_key"

    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    kwargs = {}
    if REASONING_EFFORT:
        # gpt-oss spends hidden reasoning tokens out of max_tokens before it
        # emits anything visible. This task re-phrases given facts, so there is
        # nothing to reason about -- keep that spend to a minimum.
        kwargs["reasoning_effort"] = REASONING_EFFORT

    try:
        response = client.chat.completions.create(
            model=MODEL, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            **kwargs,
        )
        choice = response.choices[0]
        text = " ".join((choice.message.content or "").split())
    except Exception as exc:
        return fallback, f"template:api_error({type(exc).__name__})"

    if not text:
        return fallback, "template:empty"
    if choice.finish_reason == "length" or not text.rstrip().endswith((".", "!", "?")):
        _log_rejected("truncated", name, text)
        return fallback, "template:truncated"
    cache[key] = text
    return text, "llm"


def _log_rejected(reason: str, f_name: str, text: str) -> None:
    """A discarded generation is invisible otherwise -- the row just shows a
    template and nothing says why."""
    REJECT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(REJECT_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"[{reason}] {f_name}\n  {text}\n")


def report(label: str, counts: dict[str, int]) -> None:
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    print(f"{label}: {summary}")
