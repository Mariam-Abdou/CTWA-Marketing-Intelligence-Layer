"""
Step 1: find which campaign / adset / ad a question is about.

Pure code, no LLM. Reads the current run's entities from trace.db and scores
each against the question by:
  - id: a full id, or a 4+ digit suffix ("ad 0005")
  - name: weighted token overlap, typo-tolerant, generic words count less
  - level words: "campaign", "audience"/"ad set", "creative"/"ad" break ties

Returns ranked candidates, not a single answer: the gate (step 2) sees them
and makes the final call, which is how a transliterated Arabic name the
matcher cannot read still gets resolved.
"""

import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from difflib import SequenceMatcher

LEVEL_WORDS = {
    "campaign": {"campaign", "campaigns", "goal", "حملة", "الحملة", "حملات"},
    "adset": {"adset", "adsets", "audience", "audiences", "set", "جمهور", "الجمهور"},
    "ad": {"ad", "ads", "creative", "creatives", "اعلان", "الاعلان", "إعلان", "الإعلان"},
}
ALL_LEVEL_WORDS = set().union(*LEVEL_WORDS.values())
# Same thing spelled two ways in names vs how people type them.
ALIASES = {"lal": "lookalike", "lookalikes": "lookalike", "alexandria": "alex",
           "iftaar": "iftar", "suhour": "suhoor", "sohoor": "suhoor"}
MIN_SCORE = 0.4
MIN_SCORE_WITH_LEVEL = 0.35


def normalize(text: str) -> list[str]:
    text = text.lower().replace("+", " ").replace("-", " ")
    tokens = re.findall(r"[0-9]+%?|[a-z]+|[؀-ۿ]+", text)
    return [ALIASES.get(t, t) for t in tokens]


@dataclass
class Entity:
    id: str
    level: str
    name: str
    detail: str
    tokens: list[str]


@dataclass
class Candidate:
    id: str
    level: str
    name: str
    score: float
    matched_by: str


class EntityIndex:
    def __init__(self, entities: list[Entity]):
        self.entities = entities
        self.by_id = {e.id: e for e in entities}
        df = {}
        for e in entities:
            for t in set(e.tokens):
                df[t] = df.get(t, 0) + 1
        n = len(entities)
        # rarer token = more distinctive ("suhoor" beats "creative")
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

    @classmethod
    def from_db(cls, db_path: str, run_id: str | None = None) -> "EntityIndex":
        with closing(sqlite3.connect(db_path)) as db:
            run_id = run_id or db.execute("SELECT run_id FROM runs WHERE is_current = 1").fetchone()[0]
            rows = db.execute("SELECT entity_id, level, name, detail FROM entities WHERE run_id = ?",
                              (run_id,)).fetchall()
        return cls([Entity(i, lvl, name, detail, normalize(name)) for i, lvl, name, detail in rows])

    def _token_hit(self, token: str, query_tokens: set[str]) -> float:
        if token in query_tokens:
            return 1.0
        if len(token) >= 5:  # typo tolerance only for longer words
            best = max((SequenceMatcher(None, token, q).ratio() for q in query_tokens), default=0)
            if best >= 0.85:
                return best
        return 0.0

    def match(self, text: str, top_k: int = 5) -> list[Candidate]:
        q_tokens = normalize(text)
        q_set = set(q_tokens)
        levels_named = {lvl for lvl, words in LEVEL_WORDS.items() if q_set & words}
        out: dict[str, Candidate] = {}

        # ids: full, or a 4+ digit suffix
        for num in re.findall(r"\d{4,}", text):
            hits = [e for e in self.entities if e.id == num or e.id.endswith(num)]
            if levels_named and len(hits) > 1:
                hits = [e for e in hits if e.level in levels_named] or hits
            for e in hits:
                score = 1.0 if (e.id == num or len(hits) == 1) else 0.7
                out[e.id] = Candidate(e.id, e.level, e.name, score, "id")

        # names
        for e in self.entities:
            if e.id in out:
                continue
            # "audience" inside a name must not count as matching the name when
            # the user only said "audience" -- level words are scored separately.
            toks = [t for t in e.tokens if t not in ALL_LEVEL_WORDS] or e.tokens
            weights = [self.idf.get(t, 1.0) for t in toks]
            total = sum(weights)
            if not total:
                continue
            hit = sum(w * self._token_hit(t, q_set - ALL_LEVEL_WORDS) for t, w in zip(toks, weights))
            score = hit / total
            level_hit = e.level in levels_named
            if levels_named:
                score += 0.2 if level_hit else -0.1
            # A named level ("the Iftar campaign") lets a weaker partial name through.
            if score >= (MIN_SCORE_WITH_LEVEL if level_hit else MIN_SCORE):
                out[e.id] = Candidate(e.id, e.level, e.name, round(min(score, 1.0), 3), "name")

        return sorted(out.values(), key=lambda c: -c.score)[:top_k]

    def is_ambiguous(self, candidates: list[Candidate], margin: float = 0.05) -> bool:
        return len(candidates) > 1 and candidates[0].score - candidates[1].score < margin

    def compact_list(self) -> str:
        """All entities in a few tokens each -- what the gate sees."""
        order = {"campaign": 0, "adset": 1, "ad": 2}
        return "\n".join(f"{e.id} | {e.level} | {e.name} | {e.detail}"
                         for e in sorted(self.entities, key=lambda e: (order[e.level], e.name)))
