"""
The chatbot: steps 1-4 wired together.

    ask(question, selected_id, history) -> Reply

    1 entity match      (inside the gate)
    2 gate + route      refuse / clarify / not yet  -> fixed reply, done
    3 answer            gpt-oss-120b with read-only tools
    4 guard             failed -> one rewrite -> failed again -> deterministic
                        reply built from the stored decision

Without an API key the bot still runs: keyword gate, and deterministic
replies instead of written answers.
"""

import time
from dataclasses import dataclass, field

from ..config import load_config
from .answer import answer as write_answer
from .entities import EntityIndex
from .gate import KeywordGate, LLMGate, route
from .guard import check
from .llm import get_client
from .store import decision_summary, stored_decisions

_cfg = load_config()["chat"]

NO_ANSWER = {
    "en": "I couldn't produce an answer I can verify from the stored data. Try asking about a specific "
          "campaign, audience or ad.",
    "ar": "مقدرتش أطلع إجابة أقدر أتأكد منها من البيانات المحفوظة. جرب تسأل عن حملة أو جمهور أو إعلان معين.",
    "franco": "Ma2dertsh atala3 egaba a2dar at2aked menha men el data. Garrab tes2al 3an campaign aw "
              "audience aw ad mo3ayan.",
}


@dataclass
class Reply:
    text: str
    route: str
    gate: dict
    answer_meta: dict = field(default_factory=dict)
    facts: list = field(default_factory=list)
    guard: dict = field(default_factory=dict)
    fallback: str | None = None          # why a deterministic reply was used
    stored: list = field(default_factory=list)
    latency_ms: int = 0


def _deterministic(ids, lang):
    rows = stored_decisions(ids)
    if not rows:
        return NO_ANSWER[lang]
    lines = []
    for d in rows:
        lines.append("• " + decision_summary(d))
        lines += [f"   – {w}" for w in d["why"]]
        if d.get("stop_rule"):
            lines.append(f"   – stop rule: {d['stop_rule']}")
    return "\n".join(lines)


class ChatBot:
    def __init__(self, db_path: str | None = None, client=None):
        self.index = EntityIndex.from_db(db_path or _cfg["db_path"])
        self.client = client if client is not None else get_client()
        self.keyword_gate = KeywordGate(self.index)
        self.gate = LLMGate(self.index, client=self.client) if self.client else self.keyword_gate

    def ask(self, question: str, selected_id: str | None = None, history: list | None = None) -> Reply:
        start = time.monotonic()
        history = history or []
        g = self.gate.classify(question, selected_id, history)
        if g.source.startswith("fallback:llm_error"):
            # LLM unreachable: degrade to the keyword gate instead of asking
            # every question to be clarified. Still safe -- if the answer step
            # fails too, the reply is built from the stored decision by code.
            err = g.meta
            g = self.keyword_gate.classify(question, selected_id, history)
            g.source, g.meta = "keyword (llm gate failed)", err
        r = route(g, selected_id)
        reply = Reply("", r.action, g.to_dict())

        if r.action != "answer":
            reply.text = r.reply
            if r.show_stored_decision_for:
                reply.stored = stored_decisions(r.show_stored_decision_for)
                reply.text += "\n" + "\n".join("• " + decision_summary(d) for d in reply.stored)
            reply.latency_ms = round((time.monotonic() - start) * 1000)
            return reply

        if self.client is None:
            reply.text, reply.fallback = _deterministic(g.entity_ids, g.language), "no_key"
            reply.latency_ms = round((time.monotonic() - start) * 1000)
            return reply

        sel = self.index.by_id.get(selected_id)
        sel_name = f"{sel.name} ({sel.level})" if sel else None
        a = write_answer(self.client, question, g, history, sel_name)
        guard = check(a.text, a.facts, question)
        reply.answer_meta, reply.facts = a.meta, a.facts

        if not guard.ok and not a.meta.get("error"):
            # one rewrite, told exactly what failed
            retry_history = history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": a.text},
                {"role": "user", "content": "Your answer failed these checks: " + "; ".join(guard.issues)
                 + ". Rewrite it using ONLY the facts, with no new numbers, no advice and no predictions."}]
            a2 = write_answer(self.client, question, g, retry_history, sel_name, facts=a.facts)
            guard2 = check(a2.text, a2.facts, question)
            reply.answer_meta = {**a.meta, "retry": a2.meta, "first_issues": guard.issues}
            reply.facts = a2.facts
            a, guard = a2, guard2

        reply.guard = {"ok": guard.ok, "issues": guard.issues}
        if guard.ok:
            reply.text = a.text
        else:
            reply.text = _deterministic(g.entity_ids, g.language)
            reply.fallback = "llm_error" if a.meta.get("error") else "guard_failed"
        reply.latency_ms = round((time.monotonic() - start) * 1000)
        return reply
