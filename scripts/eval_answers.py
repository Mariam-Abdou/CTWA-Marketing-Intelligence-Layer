"""
End-to-end evaluation of the chat's WRITTEN ANSWERS (the gate has scripts/eval_gate.py).

    python3 -m scripts.eval_answers --split dev            # needs the Groq key in .env
    python3 -m scripts.eval_answers --split dev --no-llm   # offline smoke test (keyword gate, canned replies)
    python3 -m scripts.eval_answers --split test           # report ONCE, at the end

Runs the labelled questions (data/chat_eval/gate_questions.json) through the full bot
(gate -> answer -> guard -> rewrite -> fallback) and reports:

  routing     answerable questions answered / refused / clarified; "new decision" and
              out-of-scope questions that were answered anyway (must be 0)
  guard       first-try pass rate, rewrite rate, deterministic-fallback rate, LLM error / busy rate
  audit       a STRICTER second check on the text the merchant actually saw (no +-0.5 rounding
              tolerance on whole numbers): ungrounded numbers, advice/prediction, missing citation
  cost        latency, tokens

Also writes outputs/answer_review_<time>.csv: a sample of answers with the facts next to them
and empty columns (faithful / answers_question / language_ok) to grade BY HAND.
The automatic audit only checks what code can check; the manual column is the real faithfulness number.
"""

import argparse
import csv
import json
import random
import re
import time
from collections import Counter
from datetime import datetime

from src.config import load_config
import src.chat.bot as botmod
from src.chat.bot import ChatBot
from src.chat.entities import EntityIndex
from src.chat.guard import NUM, _walk, check, check_advice

QUESTIONS = "data/chat_eval/gate_questions.json"
ATTEMPTS = []          # every call to the answer step for the current question (filled by the spy in main)
ANSWERABLE = {"explain_entity", "compare_or_list", "method", "conversation_text"}
MUST_NOT_ANSWER = {"new_decision", "out_of_scope"}


def load_questions(index, split):
    by_name = {(e.level, e.name): e.id for e in index.entities}
    out = []
    for x in json.load(open(QUESTIONS, encoding="utf-8"))["questions"]:
        if split != "all" and x["split"] != split:
            continue
        x["selected_id"] = by_name[(x["selected"]["level"], x["selected"]["name"])] if x["selected"] else None
        out.append(x)
    return out


def strict_number_issues(text, facts, question):
    """Every number must equal a fact EXACTLY (as written: same decimals; a fraction may be shown as a percent).
    No 'within 0.5' tolerance -- that is what the runtime guard allows and what we want to measure."""
    known = set()
    _walk([f["result"] for f in facts], known)
    _walk(question, known)
    bad = []
    for m in NUM.finditer(text):
        before = text[max(0, m.start() - 6):m.start()].lower()
        if "step" in before or "[" in before:
            continue
        dec = m.group(2) or ""
        value = float(m.group(1).replace(",", "") + (f".{dec}" if dec else ""))
        if not dec and value in (0, 1) and not m.group(3):
            continue
        d = len(dec)
        plain_int = d == 0 and not m.group(3) and not re.match(r"\s*(?:EGP|LE\b|جنيه|ج\.م)", text[m.end():m.end() + 10], re.I)
        ok = any((abs(k - value) < 1e-9) if plain_int else abs(round(c, d) - value) < 1e-9
                 for k in known for c in ((k,) if plain_int else (k, k * 100)) if c is not None)
        if not ok:
            bad.append(m.group(0).strip())
    return bad


def ask_with_wait(bot, x, max_wait=6):
    """The bot answers 'busy' when the provider's per-minute limit is near; wait and retry like the UI does.
    A dropped connection or a 429 is NOT a result about the guard, so those are retried too (and counted)."""
    r = None
    for attempt in range(max_wait):
        ATTEMPTS.clear()
        r = bot.ask(x["question"], x["selected_id"], x["history"], session_id="eval", page="answer_eval")
        errs = " ".join(t["error"] or "" for t in ATTEMPTS)
        if r.route == "busy" and r.retry_after is not None:
            time.sleep(min(r.retry_after + 1, 65))
        elif r.fallback == "llm_error" and ("RateLimit" in errs or "Connection" in errs or "Timeout" in errs):
            time.sleep(20 * (attempt + 1))
        else:
            return r
    return r


def summarize(rows):
    s = {"n": len(rows)}
    ans = [r for r in rows if r["expected_intent"] in ANSWERABLE]
    nope = [r for r in rows if r["expected_intent"] in MUST_NOT_ANSWER]
    pct = lambda a, b: round(a / b, 3) if b else None
    s["answerable_n"] = len(ans)
    s["answerable_answered"] = pct(sum(r["route"] == "answer" for r in ans), len(ans))
    s["answerable_clarified"] = pct(sum(r["route"] == "clarify" for r in ans), len(ans))
    s["answerable_refused"] = pct(sum(r["route"].startswith("refuse") for r in ans), len(ans))
    s["must_not_answer_n"] = len(nope)
    s["unsafe_answered"] = [r["id"] for r in nope if r["route"] == "answer"]
    s["busy_or_error"] = pct(sum(r["route"] == "busy" or bool(r["error"]) for r in rows), len(rows))

    ar = [r for r in rows if r["route"] == "answer"]          # went through the answer step
    s["answer_step_n"] = len(ar)
    s["guard_first_try_pass"] = pct(sum((not r["first_issues"]) and bool(r["guard_ok"]) for r in ar), len(ar))
    s["rewrite_rate"] = pct(sum(bool(r["first_issues"]) for r in ar), len(ar))
    s["rewrite_fixed_it"] = pct(sum(bool(r["first_issues"]) and bool(r["guard_ok"]) for r in ar),
                                sum(bool(r["first_issues"]) for r in ar))
    s["fallback_rate"] = pct(sum(bool(r["fallback"]) for r in ar), len(ar))
    s["fallback_reasons"] = dict(Counter(r["fallback"] for r in ar if r["fallback"]))
    s["first_try_issue_types"] = dict(Counter(re.split(r"[:'(]", i)[0].strip()[:40]
                                              for r in ar for i in r["first_issues"]).most_common(8))

    llm = [r for r in ar if not r["fallback"] and r["text"]]  # text written by the model and shown
    s["llm_text_shown_n"] = len(llm)
    s["strict_ungrounded_number_rate"] = pct(sum(bool(r["audit_numbers"]) for r in llm), len(llm))
    s["advice_in_shown_text_rate"] = pct(sum(bool(r["audit_advice"]) for r in llm), len(llm))
    s["citation_rate"] = pct(sum(r["has_citation"] for r in llm), len(llm))
    s["words_mean"] = round(sum(r["words"] for r in llm) / len(llm)) if llm else None
    # advice must never reach the merchant, whatever route produced the text
    s["advice_in_any_reply"] = [r["id"] for r in rows if r["audit_advice"]]
    s["answer_step_llm_errors"] = sum(r["fallback"] == "llm_error" for r in ar)
    bad = sum(r["route"] == "busy" or bool(r["error"]) or (r["fallback"] or "").startswith(("llm_error", "rate_limit"))
              for r in rows)
    s["rows_lost_to_busy_or_errors"] = bad
    s["run_valid"] = bad <= 0.1 * len(rows)
    lat = sorted(r["latency_ms"] for r in rows)
    s["latency_ms_mean"], s["latency_ms_p95"] = (round(sum(lat) / len(lat)), lat[min(len(lat) - 1, int(len(lat) * .95))]) if lat else (None, None)
    toks = [r["tokens"] for r in rows if r["tokens"]]
    s["tokens_mean"] = round(sum(toks) / len(toks)) if toks else None
    return s


def digest(facts):
    out = []
    for f in facts:
        r = f["result"]
        if f["tool"] == "get_entity" and isinstance(r, dict) and "decision_path" in r:
            out.append(f"[{r['entity']['name']}] " + " | ".join(r["decision_path"]))
        else:
            out.append(f"[{f['tool']}] " + json.dumps(r, ensure_ascii=False)[:500])
    return "\n".join(out)[:2500]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-llm", action="store_true", help="keyword gate + canned replies; checks the harness only")
    ap.add_argument("--tpm", type=int, default=5500,
                    help="pace the run to this many tokens/minute (Groq free tier allows 8000 per model; 0 = no pacing)")
    ap.add_argument("--review", type=int, default=25, help="answers to put in the manual-grading sheet")
    args = ap.parse_args()

    cfg = load_config()["chat"]
    index = EntityIndex.from_db(cfg["db_path"])
    questions = load_questions(index, args.split)[: args.limit or None]
    bot = ChatBot(log_path="outputs/chat_log_eval.db")
    if args.no_llm:
        bot.client, bot.gate = None, bot.keyword_gate
    elif bot.client is None:
        raise SystemExit("No API key (GROQ_API_KEY in .env). Use --no-llm for a harness smoke test.")

    # spy on the answer step: the bot only keeps the FINAL text, but we need every attempt
    real_write = botmod.write_answer

    def spy(*a, **kw):
        res = real_write(*a, **kw)
        ATTEMPTS.append({"text": res.text, "error": res.meta.get("error"), "facts": res.facts,
                         "question": a[1]})
        return res
    botmod.write_answer = spy

    if bot.client is not None:
        try:
            bot.client.chat.completions.create(model=cfg["gate_model"], max_tokens=5,
                                               messages=[{"role": "user", "content": "ok"}])
        except Exception as exc:
            raise SystemExit(f"LLM not reachable ({type(exc).__name__}: {str(exc)[:120]}). "
                             "Fix the connection first: python3 -m scripts.check_llm")

    rows, replies, replay = [], [], {}
    for i, x in enumerate(questions, 1):
        r = ask_with_wait(bot, x)
        replay[x["id"]] = [{"text": t["text"], "error": t["error"], "facts": t["facts"],
                            "issues": check(t["text"], t["facts"], x["question"]).issues if t["text"] else []}
                           for t in ATTEMPTS]
        facts = r.facts or []
        a = r.answer_meta or {}
        tok = lambda m: (m.get("input_tokens") or 0) + (m.get("output_tokens") or 0)
        row = {
            "id": x["id"], "split": x["split"], "language": x["language"], "question": x["question"],
            "expected_intent": x["expected_intent"], "intent": r.gate.get("intent"), "route": r.route,
            "text": r.text, "fallback": r.fallback, "guard_ok": (r.guard or {}).get("ok"),
            "guard_issues": (r.guard or {}).get("issues", []), "first_issues": a.get("first_issues", []),
            "tool_calls": len(facts), "error": a.get("error") or (r.gate.get("meta") or {}).get("error"),
            "latency_ms": r.latency_ms, "tokens": tok(a) + tok(a.get("retry", {})) + tok(r.gate.get("meta") or {}),
            "attempt_errors": [t["error"] for t in replay[x["id"]]],
            "words": len(r.text.split()), "has_citation": bool(re.search(r"\[[^\]]+\]", r.text)),
            "audit_numbers": strict_number_issues(r.text, facts, x["question"]) if r.route == "answer" and not r.fallback else [],
            "audit_advice": check_advice(r.text),
        }
        rows.append(row); replies.append((row, facts))
        if args.tpm and row["tokens"]:
            # a question costs ~5-9k tokens with a rewrite: spread them over the minute instead of hitting 429s
            pause = row["tokens"] / args.tpm * 60 - row["latency_ms"] / 1000
            if pause > 0:
                time.sleep(pause)
        flag = "ok " if not (row["audit_numbers"] or row["audit_advice"] or row["fallback"]) else "!! "
        print(f"[{i}/{len(questions)}] {flag}{x['expected_intent']:<17} -> {row['route']:<15} "
              f"{(row['fallback'] or '')[:12]:<12} {x['question'][:48]}")

    s = summarize(rows)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    tag = f"{'nollm' if args.no_llm else 'llm'}_{args.split}_{stamp}"
    json.dump({"split": args.split, "no_llm": args.no_llm, "model": cfg["answer_model"], "summary": s, "rows": rows},
              open(f"outputs/answer_eval_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    json.dump(replay, open(f"outputs/answer_attempts_{tag}.json", "w", encoding="utf-8"), ensure_ascii=False)

    # manual grading sheet: LLM-written answers first (that is what needs a human), then the rest
    pool = [(r, f) for r, f in replies if r["route"] == "answer" and not r["fallback"]]
    random.Random(7).shuffle(pool)
    sample = pool[: args.review]
    with open(f"outputs/answer_review_{tag}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "question", "answer_shown", "facts_the_model_had", "faithful_0_1", "answers_question_0_1",
                    "language_ok_0_1", "notes"])
        for r, f in sample:
            w.writerow([r["id"], r["question"], r["text"], digest(f), "", "", "", ""])

    print("\n=== summary ===")
    for k, v in s.items():
        print(f"  {k:<30} {v}")
    if s.get("run_valid") is False:
        print("\n!!! RUN NOT VALID: more than 10% of questions were lost to busy / connection / rate-limit errors. "
              "The guard numbers above say nothing about the guard. Fix the connection and re-run.")
    print(f"\nWrote outputs/answer_eval_{tag}.json and outputs/answer_review_{tag}.csv ({len(sample)} answers to grade)")


if __name__ == "__main__":
    main()
