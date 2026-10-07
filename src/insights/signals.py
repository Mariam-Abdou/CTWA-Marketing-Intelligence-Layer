"""
What happened in a conversation, read from its messages by RULES (no LLM).

For every chat this returns: who spoke last, whether the customer's last
message was a question we never answered, order value / refunds / net,
products, the customer's city -- and for chats that did NOT end in a sale,
a reason tag plus the exact message that triggered it (the evidence), so
any tag can be checked by reading one line.

Reasons (first match wins, most specific first):
    quality       product complaint after delivery (stale, broken, smell...)
    delivery      delivery late / not arrived / "any update?"
    price         price objection ("expensive", "غالي")
    changed_mind  customer cancelled / bought elsewhere / no longer needs it
    wrong_number  not a real enquiry
    no_reply      customer's LAST message was a question and we never answered
    thinking      customer left to think / come back later
    spam          adversarial outcome (only "?" / "price?")
    unclear       none of the above

These are keyword rules, not understanding: they are a floor, reported as
"tagged by rules", and every tag carries its evidence line.
"""

import re

RULES = [
    ("quality", [r"stale", r"burnt", r"broken", r"smell", r"expired", r"damaged", r"not what i expected",
                 r"taste[sd]? (bad|off|weird)", r"ريحة", r"زنخ", r"عيب", r"مكسور", r"بايظ", r"مش زي ما",
                 r"مش زي اللي", r"طعمه? (وحش|غريب)", r"منتهي", r"قديم"]),
    ("delivery", [r"\blate\b", r"delay", r"not (arrived|delivered)", r"where is (my|the) order", r"still waiting",
                  r"any update", r"اتأخر", r"متأخر", r"لسه ما?\s?وصل", r"موصلش", r"ماوصلش", r"التحديث",
                  r"مفيش رد", r"فين الطلب", r"فين الأوردر"]),
    ("price", [r"expensive", r"too much", r"pricey", r"cheaper", r"\bghaly\b", r"\bghalya\b", r"غالي", r"غالية",
               r"كتير عليا", r"مش معايا", r"over ?budget"]),
    ("changed_mind", [r"cancel", r"نلغي", r"الغي", r"الغو", r"ألغي", r"إلغاء", r"الإلغاء", r"alghi",
                      r"bought (it )?(from|elsewhere)", r"اشتريت من", r"مش هحتاج", r"mosh hahtag",
                      r"اشتراها لي", r"refund", r"ارجعوا المبلغ"]),
    ("wrong_number", [r"wrong number", r"مش الرقم", r"غلطت", r"by mistake"]),
]
THINKING = [r"let me think", r"think about it", r"will think", r"get back", r"revert", r"later", r"tomorrow",
            r"bokra", r"ba3den", r"b3den", r"discuss with", r"ask my", r"ra2y", r"هفكر", r"افكر", r"هرجعلك",
            r"ارجعلك", r"بكره", r"بعدين", r"هشوف", r"اشوف", r"رأي", r"هسأل"]
QUESTION = re.compile(r"[?؟]|\b(how much|bekam|b kam|kam|price|what|which|is it|do you|can i|when)\b|"
                      r"بكام|كام|ايه|إيه|امتى|فين|هل |ينفع|في ", re.I)
PHONE = re.compile(r"\+?\d[\d\s-]{7,}\d")


def redact(text: str) -> str:
    return PHONE.sub("[phone]", text or "")


def _first_hit(patterns, messages):
    """Last matching customer message (closest to how the chat ended)."""
    for m in reversed(messages):
        if m.get("direction") != "inbound":
            continue
        for p in patterns:
            if re.search(p, m.get("text", ""), re.I):
                return m
    return None


def conversation_signals(raw: dict, success) -> dict:
    msgs = raw.get("messages") or []
    outcome = raw.get("outcome") or {}
    otype = outcome.get("type")
    last = msgs[-1] if msgs else {}
    last_in = next((m for m in reversed(msgs) if m.get("direction") == "inbound"), None)
    unanswered = bool(last and last.get("direction") == "inbound" and QUESTION.search(last.get("text", "")))

    gross = outcome.get("total")
    refunded = outcome.get("refunded_amount") or 0
    products = [li.get("product_id") for li in outcome.get("line_items") or [] if li.get("product_id")]
    mentioned = sorted({p for m in msgs for p in (m.get("products") or [])})

    reason, evidence = None, None
    if success is not True:
        if otype == "adversarial":
            reason, evidence = "spam", last_in
        else:
            for name, pats in RULES:
                hit = _first_hit(pats, msgs)
                if hit:
                    reason, evidence = name, hit
                    break
            if reason is None and unanswered:
                reason, evidence = "no_reply", last
            if reason is None:
                hit = _first_hit(THINKING, msgs)
                if hit:
                    reason, evidence = "thinking", hit
            if reason is None:
                reason, evidence = "unclear", last_in

    return {
        "message_count": len(msgs),
        "last_sender": "customer" if last.get("direction") == "inbound" else ("merchant" if last else None),
        "unanswered_question": unanswered,
        "gross_amount": gross,
        "refunded_amount": refunded if gross is not None else None,
        "ordered_products": products,
        "mentioned_products": mentioned,
        "city": (raw.get("customer") or {}).get("city"),
        "reason": reason,
        "reason_evidence": redact(evidence.get("text")) if evidence else None,
    }
