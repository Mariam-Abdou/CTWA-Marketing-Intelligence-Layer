"""
Revenue against spend, from the conversations we have. The mentor's wording
is the rule: this covers ONLY the chats in our data, not the merchant's full
return -- so every place that shows these numbers carries CAVEAT.
"""

CAVEAT = {
    "en": "These figures cover only the WhatsApp chats in our data, not the merchant's full return "
          "(no offline or untracked sales, nothing outside the period of the data).",
    "ar": "الأرقام دي بتغطي بس محادثات واتساب اللي عندنا، مش كل عائد التاجر "
          "(من غير المبيعات اللي برّه الشات أو اللي ما اتتبعتش أو برّه فترة البيانات).",
    "franco": "El ar2am di bt-ghatty bas mo7adsat WhatsApp elly 3andena, mesh kol 3a2ed el tager "
              "(men gher el mabe3at barra el chat aw barra fatret el data).",
}

DELIVERED = ("delivered", "refunded")     # an order that reached the customer


def order_money(conv_rows) -> dict:
    """conv_rows: dicts with outcome_type, gross_amount, refunded_amount.
    net = order value of delivered/refunded orders - refunds  (= the
    pipeline's revenue; checked equal for every campaign)."""
    g = lambda r: r.get("gross_amount") or 0.0
    delivered = [r for r in conv_rows if r.get("outcome_type") in DELIVERED]
    cancelled = [r for r in conv_rows if r.get("outcome_type") == "cancelled"]
    pending = [r for r in conv_rows if r.get("outcome_type") == "stuck_pending"]
    refunded = [r for r in delivered if (r.get("refunded_amount") or 0) > 0]
    value = sum(g(r) for r in delivered)
    refunds = sum(r.get("refunded_amount") or 0 for r in delivered)
    return {
        "order_value": value, "refunds": refunds, "net_revenue": value - refunds,
        "orders_delivered": len(delivered), "orders_refunded": len(refunded),
        "cancelled_order_value": sum(g(r) for r in cancelled), "orders_cancelled": len(cancelled),
        "pending_order_value": sum(g(r) for r in pending), "orders_pending": len(pending),
    }
