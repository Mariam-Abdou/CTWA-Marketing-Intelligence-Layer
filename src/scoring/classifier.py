from dataclasses import dataclass

from ..ingestion.conversation_loader import Conversation
from ..ingestion.io_utils import load_json
from .amounts import get_outcome_amounts

PRODUCTS_PATH = "data/products.json"


def _min_product_price(path: str = PRODUCTS_PATH) -> float:
    products = load_json(path)
    return min(p["price"] for p in products)


# Smallest real price in the catalog
MIN_SUCCESS_AMOUNT = _min_product_price()


@dataclass
class ClassificationResult:
    conversation_id: str
    success: bool | None


def classify(conversation: Conversation, min_success_amount: float = MIN_SUCCESS_AMOUNT) -> ClassificationResult:
    net = get_outcome_amounts(conversation).net
    success = net >= min_success_amount if net is not None else None
    return ClassificationResult(conversation_id=conversation.id, success=success)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations

    convs = load_conversations("data/train.json")
    results = [classify(c) for c in convs]

    scoreable = [r for r in results if r.success is not None]
    success = sum(r.success for r in scoreable)
    print(f"Total: {len(results)}, excluded: {len(results) - len(scoreable)}, scoreable: {len(scoreable)}, success:{success}, failure: {len(scoreable)-success}")