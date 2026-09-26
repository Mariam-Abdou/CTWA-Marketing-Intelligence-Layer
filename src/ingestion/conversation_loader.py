from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion.io_utils import load_json, validate_enum

KNOWN_OUTCOME_TYPES = {
    "delivered", "ghosted", "cancelled", "refunded",
    "active", "stuck_pending", "adversarial",
}
PLATFORMS = {"meta_ctwa", "organic", "direct"}


@dataclass
class ConversationSource:
    platform: str
    ctwa_clid: str | None = None
    ad_id: str | None = None
    campaign_id: str | None = None
    creative_id: str | None = None
    headline: str | None = None


@dataclass
class ConversationOutcome:
    type: str
    order_id: str | None = None
    total: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Conversation:
    id: str
    started_at: str
    cycle: int
    customer_id: str
    source: ConversationSource
    outcome: ConversationOutcome
    messages: list
    status: str


def _parse_source(raw: dict, conv_id: str) -> ConversationSource:
    validate_enum(raw.get("platform"), PLATFORMS, "platform", conv_id)
    return ConversationSource(
        platform=raw["platform"],
        ctwa_clid=raw.get("ctwa_clid"),
        ad_id=raw.get("ad_id"),
        campaign_id=raw.get("campaign_id"),
        creative_id=raw.get("creative_id"),
        headline=raw.get("headline"),
    )


def _parse_outcome(raw: dict, conv_id: str) -> ConversationOutcome:
    validate_enum(raw.get("type"), KNOWN_OUTCOME_TYPES, "outcome.type", conv_id)
    return ConversationOutcome(
        type=raw["type"],
        order_id=raw.get("order_id"),
        total=raw.get("total"),
        raw=raw,
    )


def load_conversations(path: str | Path) -> list[Conversation]:
    raw_records = load_json(path)
    conversations = []
    for r in raw_records:
        conv_id = r["id"]
        conversations.append(
            Conversation(
                id=conv_id,
                started_at=r.get("started_at"),
                cycle=r.get("cycle"),
                customer_id=r.get("customer", {}).get("id"),
                source=_parse_source(r["source"], conv_id),
                outcome=_parse_outcome(r["outcome"], conv_id),
                messages=r.get("messages", []),
                status=r.get("status"),
            )
        )
    return conversations


if __name__ == "__main__":
    convs = load_conversations("data/train/conv_train.json")
    print(f"Loaded {len(convs)} conversations.")
    from collections import Counter
    print("Platforms:", Counter(c.source.platform for c in convs))
    print("Outcome types:", Counter(c.outcome.type for c in convs))