"""
Step 3: write the answer, grounded in tool results only.

The model gets three read-only tools over the stored run:
    get_entity(entity_id)  one id's decision, its 12-step trail, parent/children
    run_sql(query)         SELECT over cur_decisions / cur_level_totals / cur_proposed_tests
    glossary(term)         what a term means, with the real thresholds from config

Entities the gate already resolved are fetched BEFORE the first call (saves a round trip and tokens). 
The model may then call tools itself, at most chat.max_tool_calls times; then must answer with what it has.
"""

import json
import time
from dataclasses import dataclass, field

from ..config import load_config
from .budget import BUDGETS
from .glossary import glossary
from .llm import is_rate_limit, retry_after
from .store import entity_context, run_sql, sql_schema

_cfg = load_config()["chat"]

LANG_NAME = {"en": "English", 
             "ar": "Egyptian Arabic (Arabic script)",
             "franco": "Franco-Arabic (Egyptian Arabic in Latin letters)"}

SYSTEM = """You explain the advertising decisions a statistical system ALREADY made for a premium-food \
shop in Cairo (Meta ads that open WhatsApp chats). You talk to the shop owner.

Hard rules:
1. Use ONLY facts from the tool results / facts in this conversation. If they do not contain the \
answer, say plainly that you don't have that information. Never guess or fill gaps.
2. Never make, change or suggest a decision. Do not say what the owner should do beyond the stored \
action. No "I recommend", no "you should increase/cut/pause". The stored action is final.
3. Never predict future results ("will increase sales", "should improve").
4. Numbers: copy them from the facts. You may show a fraction as a percent (0.7 -> 70%). Round money \
to whole EGP. Keep ONE decimal for probabilities and rates (74.6%, not 75%) -- rounding must never \
make a value look like it crossed a threshold. Never compute new numbers (no sums, differences or \
ratios) -- if a total is needed, get it with run_sql.
5. When you state a score or rate, also say how much evidence is behind it (number of conversations \
or the interval).
6b. To explain WHY an entity got its decision, follow its "decision_path" in order. When it says the \
guardrails were NOT checked, say exactly that -- do not say the item is fatigued, flagged, above or \
below a stop line, or that something was "ignored". Do not compare cost per sale with any line yourself.
6c. Show scores, rates and probabilities as percents with one decimal (55.4%), never as 0.554.
6. After each claim, cite where it came from in square brackets: [step 6 fatigue guardrail], \
[decision], [why], [run_sql], [glossary]. Keep citations short.
7. Plain shop language; explain a technical term in a few words the first time.
8. Answer in {language}. Keep it under 150 words unless you are listing items.

Levels: campaign = the goal, adset = the audience, ad = the creative. budget_share is the fraction \
of the NEXT budget. action: scale / hold / kill. bucket: exploit (proven, 70% pool), explore (funded \
test, 30% pool), kill, none (no change)."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_entity",
        "description": "Full stored decision trail for ONE campaign/adset/ad: decision, why, 12 steps "
                       "(inputs/outputs), parent and children, conversation outcome summary.",
        "parameters": {"type": "object", "properties": {"entity_id": {"type": "string"}},
                       "required": ["entity_id"]}}},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Read-only SQLite SELECT over the current run. Use for lists, rankings, totals, "
                       "counts and comparisons across many entities. Max 50 rows. Tables:\n{schema}\n"
                       "level is 'campaign' | 'adset' | 'ad'. action is 'scale' | 'hold' | 'kill'. "
                       "Match names with LIKE '%...%'.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "glossary",
        "description": "Meaning of a term or rule (score, interval, baseline, p_better, scale, kill, hold, "
                       "70/30, explore, fatigue, cost per sale, roas, stop rule, steps, limits, ...).",
        "parameters": {"type": "object", "properties": {"term": {"type": "string"}},
                       "required": ["term"]}}},
]


def _tools():
    t = json.loads(json.dumps(TOOLS))
    t[1]["function"]["description"] = t[1]["function"]["description"].format(schema=sql_schema())
    return t


def call_tool(name: str, args: dict) -> dict:
    try:
        if name == "get_entity":
            return entity_context(str(args.get("entity_id", ""))) or {"error": "unknown entity_id"}
        if name == "run_sql":
            return run_sql(str(args.get("query", "")))
        if name == "glossary":
            return glossary(str(args.get("term", "")))
    except Exception as exc:  # a tool must never crash the chat
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"error": f"unknown tool {name}"}


def _dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


@dataclass
class AnswerResult:
    text: str
    facts: list = field(default_factory=list)       # [{"tool", "args", "result"}]
    tool_calls: int = 0
    meta: dict = field(default_factory=dict)        # tokens, latency, error, rounds


def prefetch(gate) -> list[dict]:
    facts = []
    for eid in gate.entity_ids[:3]:
        facts.append({"tool": "get_entity", "args": {"entity_id": eid}, "result": call_tool("get_entity", {"entity_id": eid})})
    if gate.intent == "method":
        facts.append({"tool": "glossary", "args": {"term": gate.standalone_question},
                      "result": call_tool("glossary", {"term": gate.standalone_question})})
    return facts


def build_messages(question, gate, history, facts, selected_name=None):
    system = SYSTEM.format(language=LANG_NAME.get(gate.language, "English"))
    msgs = [{"role": "system", "content": system}]
    for h in history[-_cfg["history_turns"]:]:
        msgs.append({"role": h["role"], "content": h["content"][:600]})
    parts = [f"Question: {question}"]
    if gate.standalone_question and gate.standalone_question != question:
        parts.append(f"(Rewritten: {gate.standalone_question})")
    if selected_name:
        parts.append(f"The owner has this open: {selected_name}")
    if facts:
        parts.append("Facts already fetched:\n" + "\n".join(
            f"{f['tool']}({_dump(f['args'])}) -> {_dump(f['result'])}" for f in facts))
    msgs.append({"role": "user", "content": "\n\n".join(parts)})
    return msgs


def answer(client, question, gate, history=None, selected_name=None, model=None,
           max_tool_calls=None, facts=None) -> AnswerResult:
    model = model or _cfg["answer_model"]
    max_tool_calls = _cfg["max_tool_calls"] if max_tool_calls is None else max_tool_calls
    facts = list(facts) if facts is not None else prefetch(gate)
    msgs = build_messages(question, gate, history or [], facts, selected_name)
    meta = {"model": model, "input_tokens": 0, "output_tokens": 0, "rounds": 0}
    start = time.monotonic()
    kwargs = {"reasoning_effort": _cfg["reasoning_effort"]} if _cfg.get("reasoning_effort") else {}
    calls = 0
    try:
        while True:
            meta["rounds"] += 1
            allow_tools = calls < max_tool_calls
            resp = client.chat.completions.create(
                model=model, temperature=_cfg["temperature"], max_tokens=_cfg["max_tokens"] * 2,
                messages=msgs, tools=_tools(), tool_choice="auto" if allow_tools else "none", **kwargs)
            if getattr(resp, "usage", None):
                meta["input_tokens"] += resp.usage.prompt_tokens or 0
                meta["output_tokens"] += resp.usage.completion_tokens or 0
                BUDGETS[model].record((resp.usage.prompt_tokens or 0) + (resp.usage.completion_tokens or 0))
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls or not allow_tools:
                meta["latency_ms"] = round((time.monotonic() - start) * 1000)
                text = (msg.content or "").strip().replace("【", "[").replace("】", "]")
                return AnswerResult(text, facts, calls, meta)
            msgs.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name,
                                                      "arguments": tc.function.arguments}}
                                        for tc in tool_calls]})
            for tc in tool_calls:
                calls += 1
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = call_tool(tc.function.name, args) if calls <= max_tool_calls else \
                    {"error": "tool limit reached -- answer with the facts you have"}
                facts.append({"tool": tc.function.name, "args": args, "result": result})
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": _dump(result)})
    except Exception as exc:
        meta["latency_ms"] = round((time.monotonic() - start) * 1000)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if is_rate_limit(exc):
            meta["rate_limited"] = True
            meta["retry_after"] = retry_after(exc)
            BUDGETS[model].block_for(meta["retry_after"])
        return AnswerResult("", facts, calls, meta)
