''' conversation.decide() already gates scale/kill on evidence (n vs MIN_N_FOR_ACTION, 
    probability vs PROBABILITY_THRESHOLD). Guardrails only ever push a "scale" down to "hold".
'''

def resolve_action(raw_action: str, *, is_fatigued: bool = False,
                   is_underperforming: bool = False) -> str:
    if raw_action == "scale" and (is_fatigued or is_underperforming):
        return "hold"
    return raw_action
