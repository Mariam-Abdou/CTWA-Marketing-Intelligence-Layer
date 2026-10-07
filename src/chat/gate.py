"""
Step 2: the gate. Decides WHAT KIND of question this is before anything is answered.

Contract (the "socket"): every gate returns a GateResult with the same fields. 
LLMGate and KeywordGate (src/chat/fallback/keyword_gate.py) are interchangeable; a third classifier
(e.g. Jev by TypeSafe AI) only has to return the same shape.

    intent               one of INTENTS
    confidence           0..1 (self-reported for the LLM: NOT calibrated
                         until scripts/eval_gate.py shows it is)
    entity_ids           ids from the current run the question is about
    standalone_question  the question rewritten to stand alone
                         ("why that one?" -> "Why was <ad> killed?")
    language             en | ar | franco

route() turns a GateResult into what the chatbot does next.
"""

import json
import re
from dataclasses import dataclass, field, asdict

from ..config import load_config
from .entities import EntityIndex
from .llm import chat_json, get_client

_cfg = load_config()["chat"]

INTENTS = {
    "explain_entity": "about one or a few specific campaigns/adsets/ads: why a decision, its numbers, its test, its money",
    "compare_or_list": "across many entities or customers: lists, rankings, totals, counts, comparisons, 'which ... best/worst', customer segments, repeat vs one-time buyers, cities, products bought, a customer id",
    "method": "how the system works: definitions, terms, how a number is calculated, what the system cannot know",
    "conversation_text": "what customers said, asked, complained about, why they did not buy / ghosted / refunded / cancelled, quotes from the chats -- for one entity or the whole account",
    "new_decision": "asks for something the stored decision cannot answer: a specific budget amount or % change, a what-if or prediction, a new campaign/creative, changing or overriding a stored decision, the bot's OWN opinion instead of the system's",
    "out_of_scope": "nothing to do with this merchant's ads, scores or decisions",
    "unclear": "cannot tell what is being asked, or which entity, even with the history",
}


ENTITY_INTENTS = {"explain_entity", "new_decision", "conversation_text", "compare_or_list"}


@dataclass
class GateResult:
    intent: str
    confidence: float
    entity_ids: list[str] = field(default_factory=list)
    standalone_question: str = ""
    language: str = "en"
    source: str = ""           # llm:<model> | keyword | fallback:<why>
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# LLM gate
# ---------------------------------------------------------------------------

SYSTEM = """You are the routing step of a chatbot for a food merchant's ad decision system.
You do NOT answer the question. You only classify it and return JSON.

The system has ALREADY made a stored decision (scale / hold / kill, budget share, test plan)
for every campaign, adset (= audience) and ad (= creative) listed below. The chatbot can only
EXPLAIN those stored decisions and the data behind them. It never makes a new decision.

Intents:
{intents}

Rules:
- Asking WHAT the system decided or recommends for an entity, in any wording, is explain_entity:
  the stored decision answers it. Examples: "what is the decision for this campaign",
  "what should be done with X", "what should I do with this ad", "is X scaled or killed",
  "اعمل ايه في الحملة دي", "el qarar eh?".
- new_decision ONLY when the stored decision cannot answer it: "should I double / raise / cut
  X's budget", "how much should I spend", "what if I ...", "will sales go up", "predict",
  "make a new campaign", "change X to scale", "ignore the system", "what would YOU do".
- Questions about the meaning of a term in general = method. About that term FOR a specific
  entity (its interval, its fatigue) = explain_entity.
- If the user has selected an entity and the question does not name a different one, the
  question is about the selected entity: put its id in entity_ids.
- Use the chat history to resolve "that one", "the other", "and its spend?".
- entity_ids: copy exact ids from the list. Empty if none. Several if comparing.
- If a reference matches several entities and nothing narrows it down, intent = unclear.
- standalone_question: rewrite the question in English so it makes sense with no history,
  using entity NAMES. Keep the user's meaning; do not add anything.
- language: "en", "ar" (Arabic script) or "franco" (Arabic in Latin letters/numbers).
- confidence: your probability (0..1) that the intent is right.

Entities (id | level | name | detail):
{entities}

Return ONLY: {{"intent": "...", "confidence": 0.0, "entity_ids": [], "standalone_question": "...", "language": "en"}}"""


def _user_message(question, selected, history, candidates):
    parts = []
    if selected:
        parts.append(f"Selected entity: {selected.id} | {selected.level} | {selected.name}")
    else:
        parts.append("Selected entity: none")
    if history:
        parts.append("Chat history (oldest first):\n" + "\n".join(
            f"{h['role']}: {h['content'][:300]}" for h in history[-_cfg['history_turns']:]))
    if candidates:
        parts.append("Name matches found by code (may be wrong or incomplete): " + "; ".join(
            f"{c.id} {c.name} ({c.level}, {c.score})" for c in candidates))
    parts.append(f"Question: {question}")
    return "\n\n".join(parts)


class LLMGate:
    def __init__(self, index: EntityIndex, client=None, model: str | None = None):
        self.index = index
        self.client = client if client is not None else get_client()
        self.model = model or _cfg["gate_model"]
        self.system = SYSTEM.format(
            intents="\n".join(f"- {k}: {v}" for k, v in INTENTS.items()),
            entities=index.compact_list())

    def classify(self, question, selected_id=None, history=None) -> GateResult:
        selected = self.index.by_id.get(selected_id) if selected_id else None
        candidates = self.index.match(question)
        if self.client is None:
            return _fallback("no_key", question, selected_id)
        data, meta = chat_json(self.client, self.model, self.system,
                               _user_message(question, selected, history or [], candidates))
        if data is None:
            r = _fallback("llm_error", question, selected_id)
            r.meta = meta
            return r
        r = _validate(data, self.index, f"llm:{self.model}", meta, question)
        r.meta["candidates"] = [c.id for c in candidates]
        # The selection is a fact, not a guess: if the model named no entity,
        # an entity-level question is about the one the user has open.
        if selected_id and not r.entity_ids and r.intent in ENTITY_INTENTS:
            r.entity_ids = [selected_id]
            r.meta = {**r.meta, "entity_from_selection": True}
        return r


def _validate(data: dict, index: EntityIndex, source: str, meta: dict, question: str) -> GateResult:
    """Never trust the shape of an LLM reply: unknown intent -> unclear, unknown ids dropped, confidence clamped."""
    intent = data.get("intent") if data.get("intent") in INTENTS else "unclear"
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    ids = [i for i in (data.get("entity_ids") or []) if isinstance(i, str) and i in index.by_id]
    dropped = [i for i in (data.get("entity_ids") or []) if i not in ids]
    if dropped:
        meta = {**meta, "dropped_ids": dropped}
    lang = data.get("language") if data.get("language") in ("en", "ar", "franco") else "en"
    return GateResult(intent, conf, ids, str(data.get("standalone_question") or question),
                      lang, source, meta)


def _fallback(reason, question, selected_id):
    return GateResult("unclear", 0.0, [selected_id] if selected_id else [], question,
                      _detect_language(question), f"fallback:{reason}")


def _detect_language(text: str) -> str:
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    if re.search(r"\b[a-z]*[2375][a-z]+\b", text.lower()) or re.search(
            r"\b(eh|ezay|leh|bta3|3ayez|3ndna|mesh|ma3a|kam|lw)\b", text.lower()):
        return "franco"
    return "en"


# ---------------------------------------------------------------------------
# Routing policy
# ---------------------------------------------------------------------------

REPLIES = {
    "refuse_decision": {
        "en": "I can only explain the decisions the system already made, not make new ones. "
              "Here is what the system currently recommends:",
        "ar": "أنا بشرح القرارات اللي السيستم خدها بس، مش باخد قرارات جديدة. ده اللي السيستم موصي بيه دلوقتي:",
        "franco": "Ana bashra7 el qararat elly el system khadha bas, mesh bakhod qararat gdeda. "
                  "Da elly el system mewasy beeh delwa2ty:",
    },
    "refuse_scope": {
        "en": "That is outside what I can answer. I can explain your campaigns, audiences, ads, "
              "their numbers and the system's decisions.",
        "ar": "ده بره اللي أقدر أجاوب عليه. أقدر أشرح الحملات والجماهير والإعلانات وأرقامها وقرارات السيستم.",
        "franco": "Da barra elly a2dar agaweb 3aleh. A2dar ashra7 el campaigns wel audiences wel ads "
                  "w ar2amha w qararat el system.",
    },
    "not_yet": {
        "en": "I can't read the conversation text yet, so I can't answer questions about what customers "
              "said. That is coming with the conversation-insights feature.",
        "ar": "لسه مش بقدر أقرا نص المحادثات، فمش هقدر أجاوب على اللي العملاء قالوه. ده جاي مع خاصية تحليل المحادثات.",
        "franco": "Lessa mesh ba2dar a2ra el mo7adsat, fa mesh ha2dar agaweb 3ala elly el 3omala 2aloh. "
                  "Da gay ma3 feature tahlil el mo7adsat.",
    },
    "clarify_options": {
        "en": "Which one do you mean?",
        "ar": "تقصد انهي واحد؟",
        "franco": "2asdak anhy wa7ed?",
    },
    "busy": {
        "en": "I'm at my usage limit right now. Please ask again in about {s} seconds.",
        "ar": "وصلت للحد المسموح دلوقتي. اسأل تاني بعد حوالي {s} ثانية.",
        "franco": "Wasalt lel limit delwa2ty. Es2al tany ba3d 7awaly {s} sanya.",
    },
    "clarify": {
        "en": "I'm not sure what you mean. Which campaign, audience or ad are you asking about, "
              "and what would you like to know?",
        "ar": "مش متأكد قصدك ايه. بتسأل عن انهي حملة أو جمهور أو إعلان، وعايز تعرف ايه؟",
        "franco": "Mesh met2aked 2asdak eh. Bet2sal 3an anhy campaign aw audience aw ad, w 3ayez te3raf eh?",
    },
}


@dataclass
class Route:
    action: str          # answer | refuse_decision | refuse_scope | not_yet | clarify | busy
    reply: str | None    # fixed reply for every action except "answer"
    show_stored_decision_for: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)   # clarify: entity ids to pick from


MAX_OPTIONS = 4


def _clarify(g: GateResult, lang: str) -> Route:
    """Ask back with concrete choices when we have any."""
    opts = g.entity_ids if len(g.entity_ids) > 1 else g.meta.get("candidates", [])
    opts = list(dict.fromkeys(opts))[:MAX_OPTIONS]
    if opts:
        return Route("clarify", REPLIES["clarify_options"][lang], options=opts)
    return Route("clarify", REPLIES["clarify"][lang])


def route(g: GateResult, selected_id: str | None = None,
          min_confidence: float = _cfg["min_confidence"]) -> Route:
    lang = g.language
    ids = g.entity_ids or ([selected_id] if selected_id else [])
    if g.intent == "new_decision":
        # Refusing is not the end: point to what the system DID decide.
        return Route("refuse_decision", REPLIES["refuse_decision"][lang], ids)
    if g.intent == "out_of_scope" and g.confidence >= min_confidence:
        return Route("refuse_scope", REPLIES["refuse_scope"][lang])
    if g.intent == "unclear" or g.confidence < min_confidence:
        return _clarify(g, lang)
    if g.intent == "explain_entity" and not ids:
        return _clarify(g, lang)
    return Route("answer", None)
