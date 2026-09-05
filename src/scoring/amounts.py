from dataclasses import dataclass

from ..ingestion.conversation_loader import Conversation


@dataclass
class OutcomeAmounts:
    net: float | None


def get_outcome_amounts(conversation: Conversation) -> OutcomeAmounts:
    outcome_type = conversation.outcome.type
    outcome = conversation.outcome.raw

    if outcome_type == "delivered":
        return OutcomeAmounts(net=outcome.get("total", 0))

    if outcome_type in ("ghosted", "cancelled"):
        return OutcomeAmounts(net=0.0)

    if outcome_type == "refunded":
        total = outcome.get("total", 0)
        refunded_amount = outcome.get("refunded_amount", 0)
        return OutcomeAmounts(net=total - refunded_amount)

    # stuck_pending, active, adversarial -> excluded
    return OutcomeAmounts(net=None)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations

    convs = load_conversations("data/train/train.json")
    for c in convs[:25]:
        amounts = get_outcome_amounts(c)
        print(f"{c.id} | outcome={c.outcome.type:12s} | net={amounts.net}")