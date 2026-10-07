"""
Keyword gate: regex rules + the entity matcher. No LLM, no network.

Two jobs:
  - runtime fallback: used by bot.py when there is no API key or the LLM gate fails
  - baseline: the floor any classifier has to beat (scripts/eval_gate.py --gate keyword)

It returns the same GateResult as LLMGate, so the two are interchangeable.
"""

import re

from ..entities import EntityIndex
from ..gate import GateResult, _detect_language


KEYWORDS = {
    "new_decision": [
        r"\bshould i\b", r"\bshall i\b", r"\bdouble\b", r"\b(raise|increase|cut|reduce|lower)\b.*\bbudget\b",
        r"\bwhat if\b", r"\bwill (it|sales|they)\b", r"\bpredict", r"\bforecast", r"\bhow much should\b",
        r"\bcreate (a )?new\b", r"\bchange\b.*\bto (scale|kill|hold)\b", r"\bwould you\b", r"\bignore the system\b",
        r"\bif i (kill|scale|raise|stop|pause)\b",
        r"ازود", r"أزود", r"لو زودت", r"اعمل ايه", r"المفروض", r"هل لازم", r"توقع",
        r"\bzawed", r"\blw zawedt", r"\ba3mel eh\b",
    ],
    "conversation_text": [
        r"\bcomplain", r"\bcustomers? (say|said|ask|asked|want)", r"\bpeople ask", r"\bwhy do (people|customers)\b",
        r"\bghost", r"\bchats?\b", r"\bmessages?\b", r"\brefund reason", r"\bask(ed)? for a refund",
        r"العملاء بيقولوا", r"بيشتكوا", r"بيسألوا", r"العملاء بيسألوا", r"\b3omala\b",
    ],
    "method": [
        r"\bwhat (is|does|are) (a |an |the )?(score|p_better|interval|explore|exploit|fatigue|baseline|70/30|calibrat)",
        r"\bhow (do|does) (you|the system|it) (calculate|compute|decide|work)", r"\bwhat does .* mean\b",
        r"\bdefine\b", r"\bcalibrated\b", r"\bnot know\b", r"\bhow is .* calculated\b", r"\bwhy do you use\b",
        r"يعني ايه", r"معنى", r"ازاي بتحسب", r"بتحسب", r"\bezay\b.*\b(bne7seb|bt7seb)\b", r"\bya3ni eh\b",
    ],
    "compare_or_list": [
        r"\bwhich\b", r"\btop\b", r"\bbest\b", r"\bworst\b", r"\blist\b", r"\btotal\b", r"\bcompare\b",
        r"\bhow many\b", r"\brank", r"\ball (the )?(ads|campaigns|audiences|tests)\b", r"\bvs\.?\b",
        r"انهي", r"أنهي", r"اكتر", r"أكتر", r"افضل", r"أفضل", r"كام", r"مجموع", r"\bkam\b", r"\banhy\b",
    ],
}


class KeywordGate:
    """Regex rules + the entity matcher. No LLM, no network. It exists so the
    LLM gate has a number to beat"""

    def __init__(self, index: EntityIndex):
        self.index = index

    def classify(self, question, selected_id=None, history=None) -> GateResult:
        q = question.lower()
        candidates = self.index.match(question)
        # Its own pick needs a confident match; weaker ones only become clarify options.
        ids = [c.id for c in candidates if c.score >= 0.9] or (
            [candidates[0].id] if candidates and candidates[0].score >= 0.5 else [])
        if selected_id and not ids:
            ids = [selected_id]
        hits = {k: sum(bool(re.search(p, q)) for p in pats) for k, pats in KEYWORDS.items()}
        best = max(hits, key=hits.get)
        # Fixed confidences, it be judged on intent alone.
        if hits[best] > 0:
            intent, conf = best, 0.7
        elif ids:
            intent, conf = "explain_entity", 0.65
        elif len(q.split()) <= 2:
            intent, conf = "unclear", 0.65
        else:
            intent, conf = "out_of_scope", 0.65
        return GateResult(intent, conf, ids, question, _detect_language(question), "keyword",
                          {"candidates": [c.id for c in candidates]})
