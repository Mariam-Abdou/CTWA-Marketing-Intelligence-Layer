"""
Deterministic reply: the stored decision, written by code. No LLM, no network.

Used by bot.py when the answer step cannot be trusted or cannot run:
  - no API key
  - the model errored
  - the guard rejected the answer and its one rewrite

bot.py decides WHEN to fall back; this file decides WHAT the fallback says.
"""

from ..store import decision_summary, stored_decisions

NO_ANSWER = {
    "en": "I couldn't produce an answer I can verify from the stored data. Try asking about a specific "
          "campaign, audience or ad.",
    "ar": "مقدرتش أطلع إجابة أقدر أتأكد منها من البيانات المحفوظة. جرب تسأل عن حملة أو جمهور أو إعلان معين.",
    "franco": "Ma2dertsh atala3 egaba a2dar at2aked menha men el data. Garrab tes2al 3an campaign aw "
              "audience aw ad mo3ayan.",
}


def deterministic_reply(ids, lang):
    rows = stored_decisions(ids)
    if not rows:
        return NO_ANSWER[lang]
    lines = []
    for d in rows:
        lines.append("• " + decision_summary(d))
        lines += [f"   – {w}" for w in d["why"]]
        if d.get("stop_rule"):
            lines.append(f"   – stop rule: {d['stop_rule']}")
    return "\n".join(lines)
