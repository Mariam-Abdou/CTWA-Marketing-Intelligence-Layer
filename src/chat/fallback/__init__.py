"""Things that work without an LLM: a classifier and a reply writer."""

from .deterministic_reply import NO_ANSWER, deterministic_reply
from .keyword_gate import KEYWORDS, KeywordGate
