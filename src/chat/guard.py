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

import math
import re
from dataclasses import dataclass, field

from ..config import load_config

# Decision bars a rounded number must never appear to cross (in percent).
THRESHOLDS = [load_config()["decision"]["probability_threshold"] * 100]

NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%)?")
_PRED = (r"(?:\b(?:is|was|are|were|be|been|set to|marked(?: as)?|labell?ed(?: as)?|action|decision|verdict|"
         r"stays?|stayed|remains?|remained|got|gets|becomes?|became)\b[\s:*\"'“”(→=-]*)")
ACTION_WORDS = {
    "scale": r"\bscaled\b|" + _PRED + r"scale\b",
    "kill": r"\b(?:killed|turned off|shut down|stopped spending)\b|" + _PRED + r"kill\b",
    "hold": r"\b(?:put on hold|on hold|held)\b|" + _PRED + r"hold\b",
}
_NEG = re.compile(r"\b(?:not|never|no|nor|without|neither)\b|n't", re.I)


def _negated(sentence: str, pos: int) -> bool:
    """'was not scaled', 'never killed': the sentence says the entity did NOT get that action."""
    return bool(_NEG.search(sentence[max(0, pos - 25):pos]))
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


def _half_up(x: float, d: int) -> float:
    """Round the way people do (42.5 -> 43); Python's round() would give 42."""
    f = 10 ** d
    return math.floor(x * f + 0.5 + 1e-9) / f


def _grounded(value: float, decimals: int, is_pct: bool, known: set, money: bool = False):
    """Returns (grounded, crossed): crossed = the only way this number matches
    a fact is by rounding it over a decision bar (74.6% written as 75%).
    A plain whole number (a count, days...) must equal a fact EXACTLY: 12 is not 11.83 rounded.
    Rounding is allowed for percents, decimals and money ("round money to whole EGP")."""
    crossed = None
    plain_int = decimals == 0 and not is_pct and not money
    for k in known:
        cands = [k] if plain_int else ([k, k * 100] if (is_pct or k <= 1) else [k])
        for c in cands:
            if abs(c - value) < 1e-9:
                return True, None
            if plain_int:
                continue
            if abs(round(c, decimals) - value) < 1e-9 or abs(_half_up(c, decimals) - value) < 1e-9:
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
        money = bool(re.match(r"\s*(?:EGP|LE\b|جنيه|ج\.م)", text[m.end():m.end() + 10], re.I))
        ok, crossed = _grounded(value, len(dec), bool(m.group(3)), known, money)
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
            # entity names can contain action words ("3% LAL scale"): take every name out of the
            # sentence before looking for an action word, or a correct answer is rejected
            bare = s
            for n in sorted(ents, key=len, reverse=True):
                bare = re.sub(re.escape(n), " ", bare, flags=re.I)
            for action, pat in ACTION_WORDS.items():
                if action in (final, raw):
                    continue
                if any(not _negated(bare, m.start()) for m in re.finditer(pat, bare, re.I)):
                    issues.append(f"'{name}' described as {action}, stored action is {final}")
    return list(dict.fromkeys(issues))


GUARDRAIL_TALK = re.compile(r"stop line|fatigu|over the line|above the line|cost.per.sale guardrail|cpa guardrail",
                            re.I)
NOT_RUN = re.compile(r"not (?:been |even |yet |actually |really )*(?:checked|applied|run|evaluated|tested)|only runs?|"
                     r"did ?n[o']t (?:even )?(?:run|apply|check)|wasn'?t checked|were not checked|never checked|"
                     r"didn'?t apply|did not apply|"
                     r"ما\s*اتفحص|مااتفحص|ما\s*اتطبق|ماتطبق|ما\s*اتشيك|مش\s*(?:متفحص|متطبق|اتفحص|اتطبق)|"
                     r"لم\s*(?:يتم|تُفحص|تفحص|تُطبق)|لا\s*(?:تُ?طبق|تفحص)|etfa7asoo?sh|mat7sebsh", re.I)
# the explore TEST has its own "stop rule" / "judging line" -- that is not the cost-per-sale guardrail
TEST_STOP_RULE = re.compile(r"stop rule|judging line|stop_rule|قاعدة الإيقاف", re.I)


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
            if TEST_STOP_RULE.search(s):
                continue
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


BAR_WORDS = re.compile(r"\bbar\b|threshold|\brule\b|require|at least|\bneeds?\b|to act|limit|\bline\b", re.I)


def check_bar_rounding(text: str, facts: list) -> list:
    """'75%' written for a probability that is really 74.6%: 75 is also the decision bar, so it is 'in the facts'
    and the number check passes it. Flag a bare bar value when the entity's P(better)/P(worse) is just under it."""
    ents = [f["result"] for f in facts if f["tool"] == "get_entity"
            and isinstance(f["result"], dict) and "steps" in f["result"]]
    if len(ents) != 1:
        return []
    dec = next((st.get("out", {}) for st in ents[0]["steps"] if st.get("key") == "decision"), {})
    ps = [dec.get("p_better"), dec.get("p_worse")]
    issues = []
    for sent in re.split(r"(?<=[.!?؟\n])\s+", text):
        if BAR_WORDS.search(sent):
            continue
        for m in NUM.finditer(sent):
            if not m.group(3):
                continue
            v = float(m.group(1).replace(",", "") + (f".{m.group(2)}" if m.group(2) else ""))
            for t in THRESHOLDS:
                if abs(v - t) < 1e-9 and any(p is not None and t - 0.5 < p * 100 < t for p in ps):
                    actual = next(p * 100 for p in ps if p is not None and t - 0.5 < p * 100 < t)
                    issues.append(f"{m.group(0).strip()} written but the probability is {actual:.1f}% "
                                  f"(just under the {t:.0f}% bar)")
    return issues


QUOTE = re.compile(r'["“”«»]([^"“”«»\n]{4,400})["“”«»]')


# Models write typographic characters (non-breaking hyphen in "multi‑city", narrow space in "70 %", curly
# quotes, **bold**). They are formatting, not changed facts: fold them before comparing text.
_FOLD = {**{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"},
         **{c: " " for c in "\u00a0\u2007\u2009\u200a\u202f"},
         **{c: "'" for c in "\u2018\u2019\u02bc"}, **{c: '"' for c in "\u201c\u201d"},
         "*": ""}
_FOLD_TABLE = str.maketrans(_FOLD)


def _norm(s: str) -> str:
    return " ".join(s.translate(_FOLD_TABLE).lower().split()).replace(" %", "%")


def _fact_strings(x, out):
    if isinstance(x, str):
        out.append(_norm(x))
    elif isinstance(x, dict):
        for v in x.values():
            _fact_strings(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _fact_strings(v, out)


def check_quotes(text: str, facts: list, question: str = "") -> list:
    """Anything inside quotes must appear word for word in what the tools
    returned -- a customer's message, a name, a label. A quote the model
    made up, or 'quoted' in translation, is caught here."""
    strings = []
    _fact_strings([f["result"] for f in facts], strings)
    strings.append(_norm(question))          # quoting the owner's own words back is not a made-up quote
    haystack = "\n".join(strings)
    issues = []
    for m in QUOTE.finditer(text):
        for part in re.split(r"\.\.\.|…", m.group(1)):
            frag = _norm(part).strip(" .,،!?؟:;-")
            if len(frag) >= 4 and frag not in haystack:
                issues.append(f'quote not found in the chats or facts: "{part.strip()[:60]}"')
    return list(dict.fromkeys(issues))


def check_advice(text: str) -> list:
    return [f"new decision / prediction: '{m.group(0)}'"
            for p in ADVICE for m in [re.search(p, text, re.I)] if m]


def check(text: str, facts: list, question: str = "") -> GuardResult:
    if not text.strip():
        return GuardResult(False, ["empty answer"])
    issues = (check_numbers(text, facts, question) + check_actions(text, facts) + check_advice(text)
              + check_guardrail_reasoning(text, facts) + check_probabilities(text, facts) + check_bar_rounding(text, facts)
              + check_quotes(text, facts, question))
    return GuardResult(not issues, issues)
