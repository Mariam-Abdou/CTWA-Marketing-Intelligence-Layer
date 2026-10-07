"""
Evaluate the chat gate on the labelled questions (data/chat_eval/gate_questions.json).

    python3 -m scripts.eval_gate --gate keyword            # floor, offline
    python3 -m scripts.eval_gate --gate llm --split dev    # needs GROQ key
    python3 -m scripts.eval_gate --gate llm --split test   # report ONCE, at the end

Reports what matters for "answer only when you can":
  - intent accuracy (strict, and lenient = also_ok accepted)
  - SAFETY: unsafe answers   -- a new-decision / out-of-scope / chat-text
                                question that would have been answered
            over-refusals    -- an answerable question that got refused
  - entity resolution: exact set match, precision, recall
  - calibration of the gate's confidence (bins + ECE) -- an LLM's
    self-reported confidence is not calibrated until this says so
Writes per-question results to outputs/gate_eval_<gate>_<split>_<time>.json.
"""

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime

from src.config import load_config
from src.chat.entities import EntityIndex
from src.chat.fallback import KeywordGate
from src.chat.gate import INTENTS, LLMGate, route

QUESTIONS = "data/chat_eval/gate_questions.json"
ANSWERABLE = {"explain_entity", "compare_or_list", "method"}
MUST_NOT_ANSWER = {"new_decision", "out_of_scope", "conversation_text"}
BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)]


def load_questions(index, split):
    by_name = {(e.level, e.name): e.id for e in index.entities}
    out = []
    for x in json.load(open(QUESTIONS, encoding="utf-8"))["questions"]:
        if split != "all" and x["split"] != split:
            continue
        resolve = lambda ent: by_name[(ent["level"], ent["name"])]  # KeyError = stale label
        x["expected_ids"] = sorted(resolve(e) for e in x["expected_entities"])
        x["selected_id"] = resolve(x["selected"]) if x["selected"] else None
        out.append(x)
    return out


def run(gate, questions, tpm):
    rows, window = [], []
    for i, x in enumerate(questions, 1):
        for attempt in range(4):
            # stay under the provider's tokens-per-minute limit
            now = time.monotonic()
            window = [(t, n) for t, n in window if now - t < 60]
            if tpm and sum(n for _, n in window) > tpm * 0.85:
                time.sleep(max(1.0, 60 - (now - window[0][0])))
            g = gate.classify(x["question"], x["selected_id"], x["history"])
            used = g.meta.get("input_tokens", 0) + g.meta.get("output_tokens", 0)
            window.append((time.monotonic(), used or 2500))
            err = g.meta.get("error", "")
            if "429" in err or "rate" in err.lower():
                time.sleep(15 * (attempt + 1))
                continue
            break
        r = route(g, x["selected_id"])
        ok_intents = {x["expected_intent"], *x["also_ok"]}
        rows.append({
            "id": x["id"], "split": x["split"], "language": x["language"], "question": x["question"],
            "expected_intent": x["expected_intent"], "also_ok": x["also_ok"],
            "intent": g.intent, "confidence": g.confidence,
            "strict_ok": g.intent == x["expected_intent"], "lenient_ok": g.intent in ok_intents,
            "route": r.action,
            "expected_ids": x["expected_ids"], "entity_ids": sorted(g.entity_ids),
            "standalone_question": g.standalone_question, "detected_language": g.language,
            "source": g.source, "meta": g.meta,
        })
        print(f"[{i}/{len(questions)}] {'OK ' if rows[-1]['lenient_ok'] else 'XX '}"
              f"{x['expected_intent']:<17} -> {g.intent:<17} {g.confidence:.2f} {r.action:<15} {x['question'][:50]}")
    return rows


def summarize(rows):
    n = len(rows)
    s = {"n": n,
         "intent_accuracy_strict": sum(r["strict_ok"] for r in rows) / n,
         "intent_accuracy_lenient": sum(r["lenient_ok"] for r in rows) / n}

    # safety
    must_not = [r for r in rows if r["expected_intent"] in MUST_NOT_ANSWER]
    answerable = [r for r in rows if r["expected_intent"] in ANSWERABLE]
    s["unsafe_answers"] = [r["id"] for r in must_not if r["route"] == "answer"]
    s["unsafe_answer_rate"] = len(s["unsafe_answers"]) / len(must_not) if must_not else None
    s["over_refusals"] = [r["id"] for r in answerable if r["route"].startswith("refuse")]
    s["over_refusal_rate"] = len(s["over_refusals"]) / len(answerable) if answerable else None
    s["clarify_rate_on_answerable"] = (sum(r["route"] == "clarify" for r in answerable) / len(answerable)
                                       if answerable else None)
    dec = [r for r in rows if r["expected_intent"] == "new_decision"]
    s["decision_refusal_recall"] = (sum(r["route"] == "refuse_decision" for r in dec) / len(dec)) if dec else None

    # per intent
    per = defaultdict(lambda: [0, 0])
    confusion = Counter()
    for r in rows:
        per[r["expected_intent"]][1] += 1
        per[r["expected_intent"]][0] += r["lenient_ok"]
        confusion[(r["expected_intent"], r["intent"])] += 1
    s["recall_by_intent"] = {k: round(a / b, 3) for k, (a, b) in sorted(per.items())}
    s["confusion"] = {f"{a} -> {b}": c for (a, b), c in sorted(confusion.items()) if a != b}

    # by language
    lang = defaultdict(lambda: [0, 0])
    for r in rows:
        lang[r["language"]][0] += r["lenient_ok"]; lang[r["language"]][1] += 1
    s["accuracy_by_language"] = {k: round(a / b, 3) for k, (a, b) in lang.items()}

    # entities
    with_ents = [r for r in rows if r["expected_ids"]]
    if with_ents:
        exact = sum(set(r["entity_ids"]) == set(r["expected_ids"]) for r in with_ents)
        tp = sum(len(set(r["entity_ids"]) & set(r["expected_ids"])) for r in with_ents)
        pred = sum(len(r["entity_ids"]) for r in with_ents)
        gold = sum(len(r["expected_ids"]) for r in with_ents)
        s["entity_exact_match"] = exact / len(with_ents)
        s["entity_precision"] = tp / pred if pred else None
        s["entity_recall"] = tp / gold if gold else None

    # calibration
    cal, ece = [], 0.0
    for lo, hi in BINS:
        b = [r for r in rows if lo <= r["confidence"] < hi]
        if b:
            conf = sum(r["confidence"] for r in b) / len(b)
            acc = sum(r["lenient_ok"] for r in b) / len(b)
            ece += len(b) / n * abs(conf - acc)
            cal.append({"bin": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(b),
                        "mean_confidence": round(conf, 3), "accuracy": round(acc, 3)})
    s["calibration"] = cal
    s["ece"] = round(ece, 3)

    lat = sorted(r["meta"].get("latency_ms") for r in rows if r["meta"].get("latency_ms") is not None)
    if lat:
        s["latency_ms_mean"] = round(sum(lat) / len(lat))
        s["latency_ms_p95"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    toks = [r["meta"].get("input_tokens", 0) + r["meta"].get("output_tokens", 0) for r in rows]
    s["tokens_mean"] = round(sum(toks) / n) if any(toks) else None
    s["errors"] = [r["id"] for r in rows if r["meta"].get("error") or r["source"].startswith("fallback")]
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", choices=["keyword", "llm"], default="keyword")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--model", default=None, help="override chat.gate_model")
    ap.add_argument("--tpm", type=int, default=7000, help="tokens/minute budget for pacing (0 = no pacing)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    index = EntityIndex.from_db(load_config()["chat"]["db_path"])
    questions = load_questions(index, args.split)[: args.limit or None]
    if args.gate == "llm":
        gate = LLMGate(index, model=args.model)
        if gate.client is None:
            raise SystemExit("No API key found (GROQ_API_KEY in .env). Run with --gate keyword, or add the key.")
    else:
        gate = KeywordGate(index)

    rows = run(gate, questions, args.tpm if args.gate == "llm" else 0)
    summary = summarize(rows)
    path = f"outputs/gate_eval_{args.gate}_{args.split}_{datetime.now():%Y%m%d_%H%M%S}.json"
    json.dump({"gate": args.gate, "model": getattr(gate, "model", None), "split": args.split,
               "summary": summary, "rows": rows}, open(path, "w"), ensure_ascii=False, indent=1)

    print("\n=== summary ===")
    for k in ("n", "intent_accuracy_strict", "intent_accuracy_lenient", "unsafe_answer_rate",
              "decision_refusal_recall", "over_refusal_rate", "clarify_rate_on_answerable",
              "entity_exact_match", "entity_precision", "entity_recall", "ece",
              "latency_ms_mean", "latency_ms_p95", "tokens_mean"):
        v = summary.get(k)
        print(f"  {k:<28} {v:.3f}" if isinstance(v, float) else f"  {k:<28} {v}")
    print("  recall_by_intent    ", summary["recall_by_intent"])
    print("  accuracy_by_language", summary["accuracy_by_language"])
    print("  unsafe answers      ", summary["unsafe_answers"])
    print("  over-refusals       ", summary["over_refusals"])
    print("  calibration         ", summary["calibration"])
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
