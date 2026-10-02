"""
Chat playground -- a developer page for trying the chatbot by hand.
Not the merchant app. "Full chat" runs steps 1-4 and shows the reply plus
everything behind it (gate, tools called, facts, guard). "Gate only" modes
show steps 1-2 alone, LLM vs keyword.

    streamlit run gate_playground.py
"""

import json
import time

import streamlit as st

from src.config import load_config
from src.chat.entities import EntityIndex
from src.chat.gate import KeywordGate, LLMGate, route
from src.chat.store import decision_summary, stored_decisions
from src.chat.bot import ChatBot

st.set_page_config(page_title="Gate playground", layout="wide")
CFG = load_config()["chat"]
ROUTE_LABEL = {
    "answer": "✅ answer (goes to step 3)",
    "refuse_decision": "⛔ refuse: new decision",
    "refuse_scope": "⛔ refuse: out of scope",
    "not_yet": "⏳ not yet: needs conversation text",
    "clarify": "❓ clarify",
}


@st.cache_resource
def load_index():
    return EntityIndex.from_db(CFG["db_path"])


@st.cache_data
def load_labelled():
    return json.load(open("data/chat_eval/gate_questions.json", encoding="utf-8"))["questions"]


index = load_index()
llm_gate = LLMGate(index)
keyword_gate = KeywordGate(index)
names = {e.id: f"{e.name} ({e.level})" for e in index.entities}

st.session_state.setdefault("history", [])
st.session_state.setdefault("turns", [])

# --- sidebar ---------------------------------------------------------------
with st.sidebar:
    st.header("Setup")
    if llm_gate.client is None:
        st.warning("No GROQ_API_KEY found (.env) -- only the keyword gate will work.")
    mode = st.radio("Mode", ["Full chat", "Gate: LLM", "Gate: Keyword", "Gate: both"], index=0)
    order = {"campaign": 0, "adset": 1, "ad": 2}
    options = [None] + [e.id for e in sorted(index.entities, key=lambda e: (order[e.level], e.name))]
    selected = st.selectbox("Selected entity (like opening a report)", options,
                            format_func=lambda i: "— none —" if i is None else names[i])
    st.caption(f"Model: `{CFG['gate_model']}` · min confidence {CFG['min_confidence']}")
    if st.button("Clear chat"):
        st.session_state.history, st.session_state.turns = [], []
        st.rerun()

    st.divider()
    st.subheader("Try a labelled question")
    labelled = load_labelled()
    pick = st.selectbox("Question", range(len(labelled)),
                        format_func=lambda i: f"{labelled[i]['id']} · {labelled[i]['expected_intent']} · {labelled[i]['question'][:40]}")
    if st.button("Use it"):
        x = labelled[pick]
        st.session_state.pending = x
        st.rerun()

st.title("Chat playground")
st.caption("Full chat = entity match → gate → answer (tools) → guard. Gate modes = steps 1–2 only.")


@st.cache_resource
def load_bot():
    return ChatBot()


FALLBACK_LABEL = {"guard_failed": "⚠️ answer failed the guard twice — showing the stored decision instead",
                  "llm_error": "⚠️ LLM error — showing the stored decision instead",
                  "no_key": "ℹ️ no API key — showing the stored decision instead"}


def show_reply(rep, expected=None):
    st.markdown(f"{ROUTE_LABEL[rep.route]}  ·  {rep.latency_ms} ms")
    if rep.fallback:
        st.warning(FALLBACK_LABEL.get(rep.fallback, rep.fallback))
    st.markdown(rep.text)
    g = rep.gate
    ok = "" if expected is None else (" ✓" if g["intent"] in expected else " ✗")
    with st.expander(f"gate: {g['intent']}{ok} · {g['confidence']:.2f} · {g['language']}"):
        st.markdown("**Entities:** " + (", ".join(names.get(i, i) for i in g["entity_ids"]) or "—"))
        st.markdown(f"**Rewritten:** {g['standalone_question']}")
        st.json({"source": g["source"], "meta": g["meta"]})
    if rep.facts:
        with st.expander(f"tools / facts ({len(rep.facts)})"):
            for f in rep.facts:
                st.markdown(f"**{f['tool']}** `{json.dumps(f['args'], ensure_ascii=False)}`")
                st.json(f["result"], expanded=False)
    if rep.guard:
        label = "guard: passed" if rep.guard["ok"] else f"guard: {len(rep.guard['issues'])} issue(s)"
        with st.expander(label):
            st.json({**rep.guard, "first_attempt_issues": rep.answer_meta.get("first_issues")})
    if rep.answer_meta:
        with st.expander("tokens / timing"):
            st.json(rep.answer_meta)


def run_gate(gate, question, sel, history):
    t = time.monotonic()
    g = gate.classify(question, sel, history)
    r = route(g, sel)
    return g, r, round((time.monotonic() - t) * 1000)


def show_result(label, g, r, ms, expected=None):
    st.markdown(f"**{label}** — {ROUTE_LABEL[r.action]}")
    c1, c2, c3, c4 = st.columns(4)
    ok = "" if expected is None else (" ✓" if g.intent in expected else " ✗")
    c1.metric("Intent", g.intent + ok)
    c2.metric("Confidence", f"{g.confidence:.2f}")
    c3.metric("Language", g.language)
    c4.metric("Time", f"{ms} ms")
    st.markdown("**Entities:** " + (", ".join(names.get(i, i) for i in g.entity_ids) or "—"))
    st.markdown(f"**Rewritten:** {g.standalone_question}")
    if r.reply:
        st.info(r.reply)
        for d in stored_decisions(r.show_stored_decision_for):
            st.markdown("→ " + decision_summary(d))
    with st.expander("details"):
        st.json({"source": g.source, "meta": g.meta,
                 "show_stored_decision_for": [names.get(i, i) for i in r.show_stored_decision_for]})


# --- past turns ---------------------------------------------------------------
for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["question"])
        if turn.get("expected"):
            st.caption(f"labelled: {turn['expected_label']}")
    with st.chat_message("assistant"):
        if turn.get("reply") is not None:
            show_reply(turn["reply"], expected=turn.get("expected"))
            continue
        for res in turn["results"]:
            show_result(*res, expected=turn.get("expected"))
        with st.expander("step 1 — matcher candidates"):
            st.table([{"name": c.name, "level": c.level, "score": c.score, "by": c.matched_by}
                      for c in turn["candidates"]] or [{"name": "none"}])

# --- new question ---------------------------------------------------------------
pending = st.session_state.pop("pending", None)
question = st.chat_input("Ask something a merchant would ask…")
expected = expected_label = None
sel = selected
if pending:
    question = pending["question"]
    expected = {pending["expected_intent"], *pending["also_ok"]}
    expected_label = pending["expected_intent"] + (f" (also ok: {', '.join(pending['also_ok'])})" if pending["also_ok"] else "")
    by_name = {(e.level, e.name): e.id for e in index.entities}
    if pending["selected"]:
        sel = by_name[(pending["selected"]["level"], pending["selected"]["name"])]
    if pending["history"]:
        st.session_state.history = list(pending["history"])

if question and mode == "Full chat":
    history = list(st.session_state.history)
    with st.spinner("Thinking…"):
        rep = load_bot().ask(question, sel, history)
    st.session_state.turns.append({
        "question": question + (f"  \n_(selected: {names[sel]})_" if sel else ""),
        "reply": rep, "expected": expected, "expected_label": expected_label,
    })
    st.session_state.history = history + [{"role": "user", "content": question},
                                          {"role": "assistant", "content": rep.text}]
    st.rerun()

if question:
    history = list(st.session_state.history)
    gates = {"Gate: LLM": [("LLM", llm_gate)], "Gate: Keyword": [("Keyword", keyword_gate)],
             "Gate: both": [("LLM", llm_gate), ("Keyword", keyword_gate)]}[mode]
    results = []
    with st.spinner("Classifying…"):
        for label, gate in gates:
            g, r, ms = run_gate(gate, question, sel, history)
            results.append((label, g, r, ms))
    first_g, first_r = results[0][1], results[0][2]
    st.session_state.turns.append({
        "question": question + (f"  \n_(selected: {names[sel]})_" if sel else ""),
        "results": results, "candidates": index.match(question),
        "expected": expected, "expected_label": expected_label,
    })
    # what the bot would have said, so follow-ups ("why that one?") have history
    reply = first_r.reply or f"[would answer: {first_g.standalone_question}]"
    st.session_state.history = history + [{"role": "user", "content": question},
                                          {"role": "assistant", "content": reply}]
    st.rerun()
