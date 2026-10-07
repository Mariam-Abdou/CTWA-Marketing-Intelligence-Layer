"""
Step 4: check the written answer against the facts it was given. Code only.

Three checks:
  numbers   every number in the answer must appear in the facts (exactly, rounded, 
            or as a percent of a fraction). Catches invented or computed figures.
  actions   a sentence naming an entity must not give it an action that is neither
            its stored final action nor its raw action.
  advice    no new decisions or predictions ("I recommend", "you should increase",
            "will boost sales").

A failed answer gets one rewrite; if fails, then falls back to a deterministic 
reply built from the stored decision (src/chat/bot.py).
"""

import re
from dataclasses import dataclass, field

from ..config import load_config

# Decision bars a rounded number must never appear to cross (in percent).
THRESHOLDS = [load_config()["decision"]["probability_threshold"] * 100]

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


def _grounded(value: float, decimals: int, is_pct: bool, known: set):
    """Returns (grounded, crossed): crossed = the only way this number matches
    a fact is by rounding it over a decision bar (74.6% written as 75%)."""
    crossed = None
    for k in known:
        cands = [k, k * 100] if (is_pct or k <= 1) else [k]
        for c in cands:
            if abs(c - value) < 1e-9:
                return True, None
            if abs(round(c, decimals) - value) < 1e-9 or (decimals == 0 and abs(c - value) <= 0.5):
                if is_pct and any(min(c, value) < t <= max(c, value) for t in THRESHOLDS):
                    crossed = c
                    continue
                return True, None
    return (crossed is not None), crossed


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
        ok, crossed = _grounded(value, len(dec), bool(m.group(3)), known)
        if crossed is not None:
            issues.append(f"{m.group(0).strip()} is {crossed:.1f}% rounded across the {THRESHOLDS[0]:.0f}% "
                          f"decision bar -- write {crossed:.1f}%")
        elif not ok:
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


GUARDRAIL_TALK = re.compile(r"stop line|fatigu|over the line|above the line|cost.per.sale guardrail|cpa guardrail",
                            re.I)
NOT_RUN = re.compile(r"not (?:been )?(?:checked|applied|run)|only runs?|did ?n[o']t run|wasn'?t checked|"
                     r"were not checked|never checked|didn'?t apply|did not apply", re.I)


VERDICT = re.compile(r"\b(?:above|over|exceed\w*|cross\w*|past|beyond)\b[^.]{0,25}\bline\b|\bfatigued\b|"
                     r"\bflagged\b|\bignored\b|\bwould have\b[^.]{0,30}\b(?:fired|triggered|vetoed|stopped)", re.I)


def check_guardrail_reasoning(text: str, facts: list) -> list:
    """If an entity's guardrails did not run, a sentence that talks about fatigue / the stop line must
    say they were not checked. Catches "cost per sale is above the stop line, but it was ignored" for a held item."""
    issues = []
    for f in facts:
        r = f["result"]
        if f["tool"] != "get_entity" or not isinstance(r, dict) or "decision_path" not in r:
            continue
        if not any("NOT checked" in line for line in r["decision_path"]):
            continue
        for s in re.split(r"(?<=[.!?؟\n])\s+", text):
            # a verdict is wrong even if the sentence also says "not applied"
            if VERDICT.search(s) or (GUARDRAIL_TALK.search(s) and not NOT_RUN.search(s)):
                issues.append(f"talks about a guardrail that did not run for '{r['entity']['name']}': "
                              f"'{s.strip()[:90]}'")
    return list(dict.fromkeys(issues))


PROB = re.compile(r"\b(better|worse)\b[^.\d%]{0,45}?(\d+(?:\.\d+)?)\s*%", re.I)


def check_probabilities(text: str, facts: list) -> list:
    """A percent written right after 'better'/'worse' must be THAT entity's P(better)/P(worse), 
    not transformed. Only checked when the answer is about one entity (unambiguous)."""
    ents = [f["result"] for f in facts if f["tool"] == "get_entity"
            and isinstance(f["result"], dict) and "steps" in f["result"]]
    if len(ents) != 1:
        return []
    dec = next((s.get("out", {}) for s in ents[0]["steps"] if s.get("key") == "decision"), {})
    actual = {"better": dec.get("p_better"), "worse": dec.get("p_worse")}
    issues = []
    for m in PROB.finditer(text):
        which, num = m.group(1).lower(), m.group(2)
        p = actual.get(which)
        if p is None:
            continue
        x, d = float(num), len(num.split(".")[1]) if "." in num else 0
        true_pct = p * 100
        crosses = any(min(true_pct, x) < t <= max(true_pct, x) for t in THRESHOLDS)
        if abs(round(true_pct, d) - x) > 1e-9 or crosses:
            issues.append(f"P({which}) written as {num}% but it is {true_pct:.1f}%"
                          + (f" (rounding crosses the {THRESHOLDS[0]:.0f}% bar)" if crosses else ""))
    return issues


def check_advice(text: str) -> list:
    return [f"new decision / prediction: '{m.group(0)}'"
            for p in ADVICE for m in [re.search(p, text, re.I)] if m]


def check(text: str, facts: list, question: str = "") -> GuardResult:
    if not text.strip():
        return GuardResult(False, ["empty answer"])
    issues = (check_numbers(text, facts, question) + check_actions(text, facts) + check_advice(text)
              + check_guardrail_reasoning(text, facts) + check_probabilities(text, facts))
    return GuardResult(not issues, issues)
