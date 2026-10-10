"""
Evaluate the answer guard (src/chat/guard.py) -- offline, no API key, deterministic.

    python3 -m scripts.eval_guard

The guard is the only thing standing between the LLM's text and the merchant, so we
measure it like a classifier. For every entity in the current run we take its REAL
stored facts and build:

  clean answers   correct sentences written from the facts   -> guard must PASS them
  broken answers  the same sentences with ONE error injected -> guard must FLAG them

Error types: invented number, invented score, wrong action, advice (3 phrasings + Arabic),
prediction, invented quote, quote with a word changed, guardrail verdict on an item whose
guardrails never ran, wrong P(better), and "number swapped with another real fact".

Reports per error type: caught / total, plus the false-positive rate on clean answers.
"number_swapped_with_other_fact" is a KNOWN BLIND SPOT: the number exists in the facts, so a
value check cannot see it is the wrong one. It is in the table on purpose.
Writes outputs/guard_eval_<time>.json.
"""

import json
import re
from collections import defaultdict
from datetime import datetime

from src.config import load_config
from src.chat.store import entity_context, get_conversations
from src.chat.entities import EntityIndex
from src.chat.guard import _walk, check

EXPECTED_BLIND = {"number_swapped_with_other_fact"}
FP_KINDS = ("clean", "clean_name_has_action_word", "clean_uses_word_scale_innocently", "clean_unicode_typography",
           "clean_guardrail_wording")


def known_numbers(facts):
    k = set()
    _walk([f["result"] for f in facts], k)
    return k


def fresh(value, known, step, fmt=None):
    """First value value+step*i whose written form is not already a number in the facts."""
    v = value
    for i in range(1, 30):
        v = value + step * i
        shown = float(fmt(v)) if fmt else v
        if not any(abs(shown - x) < 1e-9 or abs(shown - x * 100) < 1e-9 for x in known):
            return v
    return None


def build_cases(index):
    cases = []   # (entity, kind, expect_flag, text, facts)
    for e in index.entities:
        ctx = entity_context(e.id) or {"error": "unknown"}
        if "error" in ctx:
            continue
        facts = [{"tool": "get_entity", "args": {"entity_id": e.id}, "result": ctx}]
        known = known_numbers(facts)
        d, path = ctx["decision"], ctx["decision_path"]
        name, action = ctx["entity"]["name"], d["action"]
        m = re.search(r"(\d+) sales out of (\d+) resolved", path[0])
        sales, resolved = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        score, lo, hi = d["score"] * 100, d["interval_low"] * 100, d["interval_high"] * 100
        if resolved is None:
            continue
        base = (f"{name} is set to {action} [decision]. Its score is {score:.1f}% "
                f"(90% interval {lo:.1f}% to {hi:.1f}%) from {resolved} resolved conversations [step 1].")
        kind0 = "clean_name_has_action_word" if re.search(r"\b(scale|kill|hold)\b", name, re.I) else "clean"
        cases.append((e, kind0, False, base, facts))

        # same correct sentence, written the way gpt-oss writes it: narrow space before %, non-breaking hyphens
        typo = base.replace("%", "\u202f%").replace(name, name.replace("-", "\u2011").replace(" ", "\u202f", 1))
        cases.append((e, "clean_unicode_typography", False, typo, facts))
        quoted = name.replace("-", "\u2011").replace(" ", "\u202f")      # the QUOTE check is where this bit us
        cases.append((e, "clean_unicode_typography", False,
                      f'The test "{quoted}" has the stored action {action} [decision].', facts))
        money = re.search(r"Meta spend ([\d,]+) EGP.*?net revenue ([\d,]+) EGP", " ".join(path))
        if money:
            cases.append((e, "clean", False, f"For {name}, Meta spend was {money.group(1)} EGP and net revenue "
                          f"was {money.group(2)} EGP [step 4].", facts))
        not_checked = any("NOT checked" in p for p in path)
        if not_checked:
            cases.append((e, "clean", False, f"For {name}, the guardrails were not checked because the numbers "
                          f"did not call for a change [step 6].", facts))
            cases.append((e, "clean_uses_word_scale_innocently", False,
                          f"For {name}, the guardrails were not checked because the numbers did not say scale "
                          f"[step 6].", facts))
            for txt in (f"Fatigue and cost-per-sale guardrails were not even checked for {name} [step 6].",
                        f"{name}: حراس التعب (fatigue) وتكلفة البيع ما اتفحصوش [step 6].",
                        f"The stop rule keeps the test on {name} running until the judging line is reached [step 11]."):
                cases.append((e, "clean_guardrail_wording", False, txt, facts))
            cases.append((e, "guardrail_verdict_when_not_run", True,
                          f"{name} is above the stop line, so the cost per sale guardrail would have flagged it "
                          f"[step 6].", facts))

        cases.append((e, "clean_uses_word_scale_innocently", False,
                      f"{name} stays on {action} [decision]; the rule needed to trigger a scale action was not met "
                      f"[step 4].", facts))
        if action != "scale":
            cases.append((e, "clean_uses_word_scale_innocently", False, f"{name} was not scaled [decision].", facts))
        n2 = fresh(resolved, known, 7)
        if n2:
            cases.append((e, "invented_count", True, base.replace(f"{resolved} resolved", f"{n2} resolved"), facts))
        s2 = fresh(score, known, 3.7, lambda v: round(v, 1))
        if s2:
            cases.append((e, "invented_score", True, base.replace(f"{score:.1f}%", f"{s2:.1f}%", 1), facts))
        others = [v for v in (ctx.get("conversations_summary", {}).get("outcome_types") or {}).values()
                  if v != resolved and v > 1]
        if others:
            cases.append((e, "number_swapped_with_other_fact", True,
                          base.replace(f"{resolved} resolved", f"{others[0]} resolved"), facts))
        wrong = next(a for a in ("kill", "scale", "hold") if a not in (d["action"], d.get("raw_action")))
        verb = {"kill": "killed", "scale": "scaled", "hold": "put on hold"}[wrong]
        cases.append((e, "wrong_action", True, f"{name} was {verb} [decision].", facts))
        for kind, extra in (("advice_recommend", f" I recommend increasing the budget on {name}."),
                            ("advice_you_should", f" You should increase spend on {name}."),
                            ("advice_arabic", " أنصحك بزيادة الميزانية."),
                            ("prediction", " This will boost sales next month.")):
            cases.append((e, kind, True, base + extra, facts))
        p = next((s.get("out", {}).get("p_better") for s in ctx.get("steps", []) if s.get("key") == "decision"), None)
        if p is not None:
            x = fresh(p * 100, known, 6.0, lambda v: round(v, 1))
            if x:
                cases.append((e, "wrong_probability", True,
                              f"{name} has a {x:.1f}% chance of being better than its baseline [decision].", facts))

        conv = get_conversations(e.id, "no_sale", None, 6)
        words = next((s["customer_words"] for s in conv.get("samples", [])
                      if len(s.get("customer_words", "").split()) >= 3), None)
        if words:
            cf = facts + [{"tool": "get_conversations", "args": {"entity_id": e.id}, "result": conv}]
            cases.append((e, "clean", False, f'One customer said "{words}" and did not buy [get_conversations].', cf))
            cases.append((e, "invented_quote", True,
                          'One customer said "the delivery was great and very fast" and did not buy '
                          '[get_conversations].', cf))
            parts = words.split()
            parts[-1] = "wonderful"
            cases.append((e, "quote_one_word_changed", True,
                          f'One customer said "{" ".join(parts)}" and did not buy [get_conversations].', cf))

    # the 75% decision bar: 74.6% written as 75% must be flagged
    for p in (0.746, 0.749, 0.7451):
        f = [{"tool": "get_entity", "args": {}, "result": {
            "entity": {"name": "Test ad"}, "decision": {"action": "hold", "raw_action": "hold"},
            "decision_path": [f"P(better than baseline) = {p*100:.1f}%; the bar to act is 75.0%"],
            "steps": [{"key": "decision", "out": {"p_better": p, "p_worse": 1 - p}}]}}]
        cases.append((None, "clean", False, f"Test ad has a {p*100:.1f}% chance of being better [decision].", f))
        cases.append((None, "rounded_across_bar__word_then_number", True,
                      "Test ad: chance of being better than its baseline is 75% [decision].", f))
        cases.append((None, "rounded_across_bar__number_then_word", True,
                      "Test ad has a 75% chance of being better [decision].", f))
    return cases


def main():
    index = EntityIndex.from_db(load_config()["chat"]["db_path"])
    cases = build_cases(index)
    rows, by = [], defaultdict(lambda: [0, 0])
    for e, kind, expect, text, facts in cases:
        r = check(text, facts)
        flagged = not r.ok
        by[kind][0] += flagged
        by[kind][1] += 1
        rows.append({"entity": e.name if e else "synthetic", "kind": kind, "flagged": flagged,
                     "issues": r.issues, "text": text})
    fp = {k: by.pop(k) for k in FP_KINDS if k in by}
    print(f"{'error type':<34}{'caught':>8}{'total':>7}{'rate':>8}")
    for k, (a, b) in sorted(by.items()):
        print(f"{k:<34}{a:>8}{b:>7}{a/b:>8.0%}" + ("   <- known blind spot" if k in EXPECTED_BLIND else ""))
    errs = [(a, b) for k, (a, b) in by.items() if k not in EXPECTED_BLIND]
    ca, cb = sum(a for a, _ in errs), sum(b for _, b in errs)
    print(f"\ncatch rate, excluding the blind spot : {ca}/{cb} = {ca/cb:.1%}")
    print("\nfalse positives (a correct answer the guard rejects):")
    for k, (a, b) in fp.items():
        print(f"  {k:<36}{a:>4}/{b:<4}= {a/b:.0%}")
    for r in [r for r in rows if r["kind"] in FP_KINDS and r["flagged"]][:5]:
        print("  FALSE POSITIVE:", r["issues"], "|", r["text"][:90])
    for r in [r for r in rows if r["kind"] not in FP_KINDS and not r["flagged"] and r["kind"] not in EXPECTED_BLIND][:8]:
        print("  MISSED:", r["kind"], "|", r["entity"], "|", r["text"][:90])
    path = f"outputs/guard_eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    json.dump({"per_kind": {k: v for k, v in by.items()}, "false_positives": fp, "rows": rows},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
