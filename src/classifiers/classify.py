from dataclasses import dataclass

from ..ingestion.conversation_loader import Conversation


@dataclass
class ClassificationResult:
    conversation_id: str
    value: float | None  # None = excluded from scoring; else 0.0-1.0, raw (not thresholded)


def classify(conversation: Conversation) -> ClassificationResult:
    outcome_type = conversation.outcome.type
    outcome = conversation.outcome.raw

    if outcome_type == "delivered":
        value = 1.0
    elif outcome_type in ("ghosted", "cancelled"):
        value = 0.0
    elif outcome_type == "refunded":
        total = outcome.get("total")
        refunded_amount = outcome.get("refunded_amount", 0)
        value = 1 - (refunded_amount / total)
    else:
        value = None

    return ClassificationResult(conversation_id=conversation.id, value=value)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations

    convs = load_conversations("data/train.json")
    results = [classify(c) for c in convs]

    scoreable_values = [r.value for r in results if r.value is not None]
    excluded = len(results) - len(scoreable_values)
    raw_rate = sum(scoreable_values) / len(scoreable_values)

    print(f"Total: {len(results)}, excluded: {excluded}, scoreable: {len(scoreable_values)}")
    print(f"Raw score (sum/n): {raw_rate:.4f}")