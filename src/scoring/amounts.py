from dataclasses import dataclass

from ..ingestion.conversation_loader import Conversation


@dataclass
class OutcomeAmounts:
    net: float | None


def get_outcome_amounts(conversation: Conversation) -> OutcomeAmounts:
    outcome_type = conversation.outcome.type
    outcome = conversation.outcome.raw

    if outcome_type == "delivered":
        total = outcome.get("total")
        return OutcomeAmounts(net=float(total) if total is not None else None)

    if outcome_type in ("ghosted", "cancelled"):
        return OutcomeAmounts(net=0.0)

    if outcome_type == "refunded":
        total = outcome.get("total")
        refunded_amount = outcome.get("refunded_amount")
        if total is None or refunded_amount is None:
            return OutcomeAmounts(net=None)
        return OutcomeAmounts(net=total - refunded_amount)

    # stuck_pending, active, adversarial, unknown labels, and orders missing an
    # amount -> excluded (net=None), never counted as a failed sale
    return OutcomeAmounts(net=None)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations

    convs = load_conversations("data/train/conv_train.json")
    for c in convs[:25]:
        amounts = get_outcome_amounts(c)
        print(f"{c.id} | outcome={c.outcome.type:12s} | net={amounts.net}")