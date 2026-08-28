from dataclasses import dataclass

from ..ingestion.conversation_loader import Conversation

SUCCESS_THRESHOLD = 0.5


@dataclass
class ClassificationResult:
    conversation_id: str
    value: float | None
    success: bool | None


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

    success = value >= SUCCESS_THRESHOLD if value is not None else None
    return ClassificationResult(conversation_id=conversation.id, value=value, success=success)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations

    convs = load_conversations("data/train.json")
    results = [classify(c) for c in convs]

    scoreable = [r for r in results if r.success is not None]
    print(f"Total: {len(results)}, excluded: {len(results) - len(scoreable)}, scoreable: {len(scoreable)}")