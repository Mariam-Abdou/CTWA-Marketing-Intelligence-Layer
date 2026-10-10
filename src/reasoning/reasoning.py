"""
Explains an already-made decision in one or two plain sentences.

Reads the decision, never writes it. See src/reasoning/llm.py for the
boundary, the grounding check and the fallback behaviour shared by every
task in this package.
"""

from .llm import (
    BUCKET_PLAIN, DecisionFacts, LEVEL_NOUN, client, generate, pct,
    load_cache, report, save_cache,
)

SYSTEM = """You tell a shop owner what to do about one of their ads, and why.

The decision has ALREADY been made by a statistical model. You are not reviewing it, \
not second-guessing it, and not suggesting anything different.

The numbers are ALREADY shown to the owner in a separate column next to yours. Repeating \
them wastes the only line you get. Your job is the part the numbers do not say: what this \
means for their money, and what they should do next.

Rules:
- ONE sentence. Two only if the second says something the first could not.
- At most one number, and only if that single number is what decided it. Prefer none.
- Never invent, derive or estimate a number.
- Plain shop language. Never write: exploit, explore, bucket, baseline, posterior, \
interval, benchmark, model, data set, sample.
- Lead with the action, not with the evidence.
- Money and success rate measure different things. NEVER compare EGP returned against a \
percentage, and never say a money figure is above or below a conversion rate. Compare money \
only to the typical money figure you are given, and rates only to rates.
- Only call the evidence thin if you are told it is thin, and never call it solid, strong \
or conclusive unless you are told the thing was scaled.
- State only the budget move you are given. Never invent one (do not say shrink, pause, \
raise or cut unless that is what you were told).
- Describe what HAS happened. Never predict what scaling will do -- no "should boost \
sales", no "will increase", no "expect more".
- No bullet points, no headings, no markdown."""


def _facts_text(f: DecisionFacts) -> str:
    noun = LEVEL_NOUN[f.level]
    lines = [
        f'What this is: a {noun} called "{f.name}" ({f.detail})',
        f"What was decided: {f.action}, and it {BUCKET_PLAIN[f.bucket]}",
        f"How it did: {f.successes} of {f.n} conversations ended in a sale ({pct(f.raw_rate)})",
    ]
    if f.baseline is not None:
        lines.append(f"What comparable {noun}s manage: {f.baseline:.0%}")
    if f.roas is not None:
        earning = "earning" if f.roas >= 1 else "losing money at"
        money = (f"Money so far: {earning} {f.roas:.2f} EGP back per 1 EGP spent "
                 "(this is what already happened, not a forecast)")
        if f.roas_baseline is not None:
            # Without a money benchmark the model reached for the only other
            # number nearby -- the conversion-rate bar -- and wrote things like
            # "1.86 EGP, far above the typical 59% conversion rate".
            money += f". The typical {noun} here earns back {f.roas_baseline:.2f}"
        lines.append(money)
    if f.is_thin:
        lines.append("How much evidence: TOO FEW conversations to be sure of anything yet")
    else:
        lines.append("How much evidence: a usable number of conversations")
    if f.action == "hold":
        lines.append(
            "IMPORTANT -- why it was not scaled: its rate is too close to comparable "
            f"{noun}s to tell them apart yet. It is NOT proven good. Do not call the "
            "evidence solid, strong or conclusive."
        )
    if f.is_fatigued:
        lines.append("Why it did not get more money: the same people keep seeing it and have stopped clicking")
    if f.is_underperforming:
        lines.append("Why it did not get more money: each sale is costing far more than this shop usually pays")
    return "\n".join(lines)


def _template(f: DecisionFacts) -> str:
    """Deterministic fallback, and what every unfunded row gets. Same brief as
    the prompt: say the action, not the numbers -- the "why" column already
    carries those."""
    if f.action == "scale":
        head = "Your strongest performer here - give it the biggest share of the next budget"
    elif f.action == "kill":
        head = "Stop spending on this one"
    elif f.bucket == "explore":
        head = "Not proven either way yet - worth a small budget to find out"
    else:
        head = "Leave the spend as it is; nothing here argues for a change"

    if f.is_fatigued:
        head += ", and it is held back from more because the same people keep seeing it"
    elif f.is_underperforming:
        head += ", and each sale is costing far more than this shop usually pays"
    elif f.is_thin:
        head += ", on too few conversations to be sure"
    return head + "."


def narrate_all(facts_list: list[DecisionFacts], provenance: dict | None = None) -> dict[str, str]:
    """provenance, if given, is filled id -> {source, prompt}: where each
    sentence came from (llm | cache | template:<why>) and the exact facts the
    model was shown."""
    llm, cache = client(), load_cache()
    out: dict[str, str] = {}
    counts: dict[str, int] = {}

    for f in facts_list:
        if f.bucket == "none":
            # No decision was taken on this row, so there is nothing for a
            # merchant to act on -- the template answers "why no budget?"
            # without spending a model call on 27 of every 38 rows.
            text, source = _template(f), "template:not_funded"
        else:
            text, source = generate(
                key=f.fingerprint("narrate"), system=SYSTEM, user=_facts_text(f),
                fallback=_template(f),
                client=llm, cache=cache, name=f.name,
            )
        out[f.id] = text
        counts[source] = counts.get(source, 0) + 1
        if provenance is not None:
            provenance[f.id] = {"source": source, "prompt": _facts_text(f)}

    save_cache(cache)
    report("Reasoning", counts)
    return out
