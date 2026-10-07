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
    not_available customer wanted something we don't have
    no_reply      customer spoke last (a question, an order, an address) and we never answered
    thinking      customer left to think / come back later
    spam          adversarial outcome (only "?" / "price?")
    unclear       none of the above

These are keyword rules, not understanding: they are a floor, reported as
"tagged by rules", and every tag carries its evidence line.
"""

import re

RULES = [
    ("quality", [r"stale", r"burnt", r"broken", r"smell", r"expired", r"damaged", r"not what i expected",
                 r"\bdry\b", r"best.by", r"moldy?", r"rotten", r"scratch", r"taste[sd]? (bad|off|weird)",
                 r"ريح", r"رائحة", r"زنخ", r"عفن", r"عيب", r"مكسور", r"بايظ", r"باهت", r"ناشف", r"مفتوح",
                 r"خدش", r"فيرمونت", r"مش زي ما", r"مش زي اللي", r"مش متأكد إنه أصلي", r"حالة سيئة",
                 r"طعمه?ا? (وحش|غريب|مر)", r"منتهي"]),
    ("delivery", [r"(received|arrived|delivered|came|got it).{0,25}\blate\b", r"\blate\b.{0,15}(delivery|order|arriv)",
                  r"not (arrived|delivered)", r"where is (my|the) order", r"still waiting", r"any update",
                  r"اتأخر", r"لسه ما?\s?وصل", r"موصلش", r"ماوصلش", r"التحديث", r"مفيش رد", r"فين الطلب",
                  r"فين الأوردر", r"قبل التوصيل"]),
    ("price", [r"expensive", r"too much", r"pricey", r"cheaper", r"\bghaly\b", r"\bghalya\b", r"\bkete+r\b",
               r"غالي", r"غالية", r"كتير عليا", r"كتير شوية", r"كبيرة بالنسبالي", r"مش معايا", r"over ?budget"]),
    ("changed_mind", [r"cancel", r"نلغي", r"الغي", r"الغو", r"ألغي", r"تلغ", r"الغاء", r"إلغاء", r"الإلغاء", r"alghi",
                      r"bought (it )?(from|elsewhere)", r"اشتريت من", r"لقيت معايا", r"مش هحتاج", r"mosh hahtag",
                      r"اشتراها لي", r"refund", r"استرد", r"ارجعوا المبلغ"]),
    ("wrong_number", [r"wrong number", r"مش الرقم", r"غلط في الرقم", r"غلطت", r"by mistake"]),
    ("not_available", [r"not available", r"don'?t have", r"مش متوفر", r"مش موجود", r"بدور على .* تحديد",
                       r"looking for .* specifically"]),
]
THINKING = [r"let me think", r"think(ing)? about", r"will think", r"consider", r"get back", r"revert",
            r"reach out", r"will return", r"plan with", r"later", r"bokra a?h?kalm", r"ba3den", r"b3den",
            r"hargalek", r"discuss with", r"ask my", r"check with", r"review", r"decide", r"ra2y", r"هفكر",
            r"افكر", r"هرجع", r"ارجعلك", r"هكلمك", r"بعدين", r"هشوف", r"اشوف", r"رأي", r"[أا]سأل", r"هسأل",
            r"(?<!\w)وقتي", r"مش متاكد", r"مش متأكد", r"مش هطلب دلوقتي", r"بفكر", r"اقول ل",
            r"بكره (برد|هرد|اكلم|أكلم|اقول|أقول)", r"bokra a?\d?\w*\s?(feedback|3aleek|a2olak|aklmak)"]
# "tomorrow"/"بكره" were dropped on purpose: customers use them for the delivery day.
# A customer's last message that closes the chat on their side -- if they said
# this last, silence after it is NOT us failing to reply.
CLOSING = re.compile(r"thank|thanks|thx|\bok\b|okay|\bbye\b|sorry|no thanks|\bdone\b|perfect|great|appreciate|"
                     r"(?<!\w)تم(?!\w)|خلصت|شكر|تمام|اوكي|أوكي|اوك|ماشي|"
                     r"آسف|اسف|معلش|خلاص|مع السلامة|\bla2\b|^لا\b", re.I)
PHONE = re.compile(r"\+?\d[\d\s-]{7,}\d|\d{5}X{3,}\d*")


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
    # We left the customer waiting: they spoke last and it was not a goodbye /
    # "let me think" -- a question, an order, an address we never answered.
    last_text = last.get("text", "") if last else ""
    # A message with digits (an address, a quantity, a price) is never a goodbye,
    # even if it starts with "تمام" / "ok".
    closing = CLOSING.search(last_text) and not re.search(r"\d", last_text)
    unanswered = bool(last and last.get("direction") == "inbound" and not closing
                      and not any(re.search(p, last_text, re.I) for p in THINKING))

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
                # no evidence for "unclear": the last message is often just a name
                # and address, which proves nothing and should not be quoted.
                reason, evidence = "unclear", None

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
