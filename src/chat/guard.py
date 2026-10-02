"""
Step 4: check the written answer against the facts it was given. Code only.

Three checks:
  numbers   every number in the answer must appear in the facts (exactly,
            rounded, or as a percent of a fraction). Catches invented or
            computed figures.
  actions   a sentence naming an entity must not give it an action that is
            neither its stored final action nor its raw action. Catches
            "X was killed" when X is on hold.
  advice    no new decisions or predictions ("I recommend", "you should
            increase", "will boost sales").

A failed answer gets one rewrite; if that fails too, the bot falls back to a
deterministic reply built from the stored decision (src/chat/bot.py).
"""

import re
from dataclasses import dataclass, field

NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%)?")
ACTION_WORDS = {
    "scale": r"\bscal(?:e|ed|ing)\b",
    "kill": r"\b(?:kill(?:ed)?|stop(?:ped)? spending|turned off|shut down)\b",
    "hold": r"\b(?:hold|held|on hold)\b",
}
ADVICE = [
    r"\b(?:i|we) (?:would )?(?:recommend|suggest|advise)\b",
    r"\byou should (?:increase|raise|double|cut|reduce|lower|pause|stop|scale|kill|spend|move|shift|turn|try)\b",
    r"\b(?:will|would|should) (?:increase|boost|improve|grow|raise|double|lift)\b",
    r"\bif i were you\b", r"\bmy (?:advice|recommendation)\b",
    r"انصحك", r"أنصحك", r"المفروض تزود", r"هيزود", r"هتزيد",
    r"\banse7ak\b", r"\blazem tzawed\b",
]


@dataclass
class GuardResult:
    ok: bool
    issues: list = field(default_factory=list)


def _walk(x, out):
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        out.add(float(x))
    elif isinstance(x, str):
        for m in NUM.finditer(x):
            out.add(float(m.group(1).replace(",", "") + (f".{m.group(2)}" if m.group(2) else "")))
    elif isinstance(x, dict):
        for k, v in x.items():
            _walk(k, out); _walk(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _walk(v, out)


def _grounded(value: float, decimals: int, is_pct: bool, known: set) -> bool:
    for k in known:
        cands = [k, k * 100] if (is_pct or k <= 1) else [k]
        for c in cands:
            if abs(round(c, decimals) - value) < 1e-9 or abs(c - value) < 1e-9:
                return True
            # tolerate rounding to whole numbers / one decimal either way
            if decimals == 0 and abs(c - value) <= 0.5:
                return True
    return False


def check_numbers(text: str, facts: list, question: str = "") -> list:
    known = set()
    _walk([f["result"] for f in facts], known)
    _walk(question, known)
    issues = []
    for m in NUM.finditer(text):
        before = text[max(0, m.start() - 6):m.start()].lower()
        if "step" in before or "[" in before:          # citations like [step 6]
            continue
        raw = m.group(1).replace(",", "")
        dec = m.group(2) or ""
        value = float(raw + (f".{dec}" if dec else ""))
        if not dec and value in (0, 1) and not m.group(3):   # "one", "1 of", trivial
            continue
        if not _grounded(value, len(dec), bool(m.group(3)), known):
            issues.append(f"number not in facts: {m.group(0).strip()}")
    return issues


def _entities(facts):
    """name -> (final action, raw action) from get_entity results and SQL rows."""
    out = {}
    for f in facts:
        r = f["result"]
        if f["tool"] == "get_entity" and isinstance(r, dict) and "entity" in r:
            d = r.get("decision", {})
            out[r["entity"]["name"]] = (d.get("action"), d.get("raw_action"))
            for rel in [r.get("parent")] + list(r.get("children") or []):
                if rel:
                    out.setdefault(rel["name"], (rel.get("action"), None))
        if f["tool"] == "run_sql" and isinstance(r, dict) and r.get("columns"):
            cols = r["columns"]
            if "name" in cols and "action" in cols:
                for row in r["rows"]:
                    out.setdefault(row[cols.index("name")], (row[cols.index("action")], None))
    return out


def check_actions(text: str, facts: list) -> list:
    issues = []
    ents = _entities(facts)
    sentences = re.split(r"(?<=[.!?؟\n])\s+", text)
    for name, (final, raw) in ents.items():
        if not final:
            continue
        others = [n for n in ents if n != name and name not in n]
        for s in sentences:
            if name.lower() not in s.lower():
                continue
            if any(o.lower() in s.lower() for o in others):
                continue  # sentence talks about several entities: too ambiguous to check
            for action, pat in ACTION_WORDS.items():
                if action not in (final, raw) and re.search(pat, s, re.I):
                    issues.append(f"'{name}' described as {action}, stored action is {final}")
    return list(dict.fromkeys(issues))


def check_advice(text: str) -> list:
    return [f"new decision / prediction: '{m.group(0)}'"
            for p in ADVICE for m in [re.search(p, text, re.I)] if m]


def check(text: str, facts: list, question: str = "") -> GuardResult:
    if not text.strip():
        return GuardResult(False, ["empty answer"])
    issues = check_numbers(text, facts, question) + check_actions(text, facts) + check_advice(text)
    return GuardResult(not issues, issues)
