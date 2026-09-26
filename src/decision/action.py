def resolve_action(
    raw_action: str,
    *,
    is_fatigued: bool = False,
    is_underperforming: bool = False,
) -> str:
    # decide() (conversation.py) already gates scale/kill on evidence (n vs
    # MIN_N_FOR_ACTION, probability vs PROBABILITY_THRESHOLD). Guardrails here
    # only ever push a "scale" down to "hold" -- they never create or upgrade
    # a scale/kill call.
    if raw_action == "scale" and (is_fatigued or is_underperforming):
        return "hold"
    return raw_action


# Demo removed -- it rebuilt the whole posterior chain from scratch just to
# print one example, drifting out of sync with the real pipeline in the
# process (see auditing/findings.py). To inspect one id end-to-end, run:
#   python3 -m scripts.inspect --level <campaign|adset|ad> --id <id>
