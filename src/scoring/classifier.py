from dataclasses import dataclass

from ..ingestion.conversation_loader import Conversation
from ..ingestion.io_utils import load_json

PRODUCTS_PATH = "data/products.json"


def _min_product_price(path: str = PRODUCTS_PATH) -> float:
    products = load_json(path)
    return min(p["price"] for p in products)


# Smallest real price in the catalog
MIN_SUCCESS_AMOUNT = _min_product_price()


@dataclass
class ClassificationResult:
    conversation_id: str
    value: float | None
    success: bool | None


def classify(conversation: Conversation, min_success_amount: float = MIN_SUCCESS_AMOUNT) -> ClassificationResult:
    outcome_type = conversation.outcome.type
    outcome = conversation.outcome.raw

    if outcome_type == "delivered":
        value = 1.0
        net = outcome.get("total", 0)
    elif outcome_type in ("ghosted", "cancelled"):
        value = 0.0
        net = 0
    elif outcome_type == "refunded":
        total = outcome.get("total")
        refunded_amount = outcome.get("refunded_amount", 0)
        value = 1 - (refunded_amount / total)
        net = total - refunded_amount
    else:
        value = None
        net = None

    success = net >= min_success_amount if net is not None else None
    return ClassificationResult(conversation_id=conversation.id, value=value, success=success)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations

    convs = load_conversations("data/train.json")
    results = [classify(c) for c in convs]

    scoreable = [r for r in results if r.success is not None]
    print(f"Total: {len(results)}, excluded: {len(results) - len(scoreable)}, scoreable: {len(scoreable)}")