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

import re
import time
from dataclasses import dataclass, field

from ..config import load_config
from . import log as chat_log
from .answer import answer as write_answer
from .budget import BUDGETS
from .entities import EntityIndex
from .fallback import KeywordGate, deterministic_reply
from .gate import REPLIES, LLMGate, _detect_language, route
from .guard import check
from .llm import get_client
from .store import decision_summary, stored_decisions
from ..insights.money import CAVEAT

_cfg = load_config()["chat"]


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
    options: list = field(default_factory=list)     # clarify: [{id, name, level}]
    retry_after: float | None = None                # busy: seconds to wait


MONEY_WORDS = re.compile(r"revenue|roas|return on|\bspend|\bspent|order value|refund|\begp\b|"
                         r"إيراد|ايراد|عائد|صرف|مصاريف|فلوس|جنيه|e7na sarafna|el sarf|3a2ed", re.I)
HAS_CAVEAT = re.compile(r"full return|not the merchant|عائد التاجر|kol 3a2ed", re.I)


def with_money_caveat(text: str, lang: str) -> str:
    """The mentor's rule, enforced by code: any answer that talks about money
    says it covers only the chats in our data."""
    if MONEY_WORDS.search(text or "") and not HAS_CAVEAT.search(text):
        return f"{text}\n\n_{CAVEAT.get(lang, CAVEAT['en'])}_"
    return text


class ChatBot:
    def __init__(self, db_path: str | None = None, client=None, log_path: str | None = None):
        self.log_path = log_path
        self.index = EntityIndex.from_db(db_path or _cfg["db_path"])
        self.client = client if client is not None else get_client()
        self.keyword_gate = KeywordGate(self.index)
        self.gate = LLMGate(self.index, client=self.client) if self.client else self.keyword_gate

    def ask(self, question: str, selected_id: str | None = None, history: list | None = None,
            session_id: str | None = None, page: str | None = None) -> Reply:
        reply = self._ask(question, selected_id, history or [])
        chat_log.write(reply, question=question, session_id=session_id, page=page,
                                      selected_id=selected_id, history_turns=len(history or []) // 2,
                                      path=self.log_path)
        return reply

    def _busy(self, start, question, selected_id, seconds, gate=None, why="pre-check"):
        lang = (gate or {}).get("language") or _detect_language(question)
        secs = max(1, int(round(seconds)))
        text = REPLIES["busy"][lang].format(s=secs)
        ids = (gate or {}).get("entity_ids") or ([selected_id] if selected_id else [])
        stored = stored_decisions(ids)
        if stored:   # still useful while waiting: the stored decision, written by code
            text += "\n" + "\n".join("• " + decision_summary(d) for d in stored)
        r = Reply(text, "busy", gate or {"intent": None, "confidence": None, "entity_ids": ids,
                                         "standalone_question": question, "language": lang,
                                         "source": f"busy:{why}", "meta": {}},
                  stored=stored, retry_after=secs, fallback=f"rate_limit:{why}")
        r.latency_ms = round((time.monotonic() - start) * 1000)
        return r

    def _ask(self, question, selected_id, history) -> Reply:
        start = time.monotonic()
        if self.client is not None:
            # Will this question fit in the provider's per-minute limit?
            wait = max(BUDGETS[_cfg["gate_model"]].wait_seconds(_cfg.get("estimated_gate_tokens", 2100)),
                       BUDGETS[_cfg["answer_model"]].wait_seconds(_cfg.get("estimated_answer_tokens", 3500)))
            if wait > 0:
                return self._busy(start, question, selected_id, wait)
        g = self.gate.classify(question, selected_id, history)
        if g.meta.get("rate_limited"):
            return self._busy(start, question, selected_id, g.meta.get("retry_after", 30), why="gate_429")
        if g.source.startswith("fallback:llm_error"):
            # LLM unreachable: degrade to the keyword gate instead of asking
            # every question to be clarified. Still safe -- if the answer step
            # fails too, the reply is built from the stored decision by code.
            err = g.meta
            g = self.keyword_gate.classify(question, selected_id, history)
            g.source, g.meta = "keyword (llm gate failed)", err
        r = route(g, selected_id)
        reply = Reply("", r.action, g.to_dict())
        reply.options = [{"id": i, "name": self.index.by_id[i].name, "level": self.index.by_id[i].level}
                         for i in r.options if i in self.index.by_id]

        if r.action != "answer":
            reply.text = r.reply
            if r.show_stored_decision_for:
                reply.stored = stored_decisions(r.show_stored_decision_for)
                reply.text += "\n" + "\n".join("• " + decision_summary(d) for d in reply.stored)
            reply.latency_ms = round((time.monotonic() - start) * 1000)
            return reply

        if self.client is None:
            reply.text, reply.fallback = deterministic_reply(g.entity_ids, g.language), "no_key"
            reply.latency_ms = round((time.monotonic() - start) * 1000)
            return reply

        sel = self.index.by_id.get(selected_id)
        sel_name = f"{sel.name} ({sel.level})" if sel else None
        a = write_answer(self.client, question, g, history, sel_name)
        if a.meta.get("rate_limited"):
            busy = self._busy(start, question, selected_id, a.meta.get("retry_after", 30),
                              gate=g.to_dict(), why="answer_429")
            busy.answer_meta, busy.facts = a.meta, a.facts
            return busy
        guard = check(a.text, a.facts, question)
        reply.answer_meta, reply.facts = a.meta, a.facts

        if not guard.ok and not a.meta.get("error"):
            # one rewrite, told exactly what failed
            retry_history = history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": a.text},
                {"role": "user", "content": "Your answer failed these checks: " + "; ".join(guard.issues)
                 + ". Rewrite it using ONLY the facts, with no new numbers, no advice and no predictions."}]
            # The provider allows 8,000 tokens/minute per model and one answer costs ~4,400, so a rewrite
            # sent right after the first answer hits a 429 (this was the main cause of canned replies).
            # Wait for room in the minute; if that takes too long, skip the rewrite.
            wait = BUDGETS[_cfg["answer_model"]].wait_seconds(_cfg.get("estimated_rewrite_tokens", 4600))
            if wait > _cfg.get("max_rewrite_wait_s", 30):
                reply.guard = {"ok": False, "issues": guard.issues}
                reply.answer_meta = {**a.meta, "first_issues": guard.issues}
                reply.facts = a.facts
                reply.text = deterministic_reply(g.entity_ids, g.language)
                reply.fallback = "rate_limit:rewrite_skipped"
                reply.latency_ms = round((time.monotonic() - start) * 1000)
                return reply
            if wait > 0:
                time.sleep(wait + 0.5)
            a2 = write_answer(self.client, question, g, retry_history, sel_name, facts=a.facts)
            guard2 = check(a2.text, a2.facts, question)
            reply.answer_meta = {**a.meta, "retry": a2.meta, "first_issues": guard.issues}
            reply.facts = a2.facts
            a, guard = a2, guard2

        reply.guard = {"ok": guard.ok, "issues": guard.issues}
        if guard.ok:
            reply.text = with_money_caveat(a.text, g.language)
        else:
            reply.text = deterministic_reply(g.entity_ids, g.language)
            reply.fallback = ("rate_limit:rewrite" if a.meta.get("rate_limited")
                              else "llm_error" if a.meta.get("error") else "guard_failed")
        reply.latency_ms = round((time.monotonic() - start) * 1000)
        return reply
