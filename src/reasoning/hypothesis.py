"""
Rewrites an explore test's hypothesis in the merchant's language.

The brief asks each explore test to carry "a hypothesis and a stop criterion".
allocation._build_hypothesis() already produces one deterministically, but it
restates the posterior ("X's true rate is above its baseline of 0.579") --
which is the score said twice, not a hypothesis, and it names the id rather
than the audience or creative it stands for.

This turns it into what a merchant can actually act on: what we suspect, why
it is worth 10% of the budget to find out, and what result would settle it.
Nothing here selects the tests or sets their budget -- allocation.py did that
before this module is reached, and its wording is the fallback below.
"""

from .llm import (
    DecisionFacts, LEVEL_NOUN, client, generate, pct,
    load_cache, report, save_cache,
)

# ProposedTest is only used for type hints below; imported lazily inside the
# function to avoid a decision<->reasoning import cycle (new_tests.py does not
# import this module).

SYSTEM = """You write the hypothesis for one advertising test, for a shop owner who does not know statistics.

The test has ALREADY been chosen and funded. Do not propose a different test or a different \
budget, and do not question whether it should run.

A hypothesis is a GUESS ABOUT WHY, not a restatement of the result. "It converts better than \
average" is the result, not a hypothesis. The hypothesis is what it is about THIS kind of \
audience, or THIS creative angle, that might explain the number -- and what would prove it.

Write, in this order:
- what specifically about this audience type or creative angle might be causing what we see \
(say it as a guess: may, might, could)
- what result would confirm or kill that guess

Rules:
- 2 sentences. 3 only if the third is doing real work.
- Name the audience type or creative angle explicitly. A hypothesis that would fit any \
other test in the account is a failed hypothesis.
- You have NOT been told what any ad looks like. Never describe imagery, colour, format, \
video, or wording you were not given -- at campaign level you are told only the goal, and \
inventing a creative to explain the numbers is the worst thing you can do here.
- At most one number, and only if it is the number the test turns on.
- Never invent, derive or estimate a number.
- Never begin with "We think" or "This test will show".
- Plain shop language. Never write: exploit, explore, bucket, baseline, posterior, interval, \
benchmark, model, conversion rate.
- No bullet points, no headings, no markdown."""


def _facts_text(f: DecisionFacts) -> str:
    noun = LEVEL_NOUN[f.level]
    # "creative=premium_heritage/default_premium" is a database slug, and the
    # model quoted it back verbatim. Say it the way a person would.
    kind = f.detail.split("=", 1)[-1] if "=" in f.detail else f.detail
    kind = kind.replace("_", " ").replace("/", ", ")
    ANCHOR = {
        "campaign": "the GOAL it is set to chase (sales, leads, awareness) and whether that "
                    "goal matches what these customers actually do. You know nothing about "
                    "its creatives -- do not guess at them",
        "adset": "the TYPE OF AUDIENCE it targets and what those people are likely to want",
        "ad": "the ANGLE of its message -- what it argues, not what it looks like",
    }
    lines = [
        f'What is being tested: a {noun} called "{f.name}"',
        f"THE KIND OF {noun.upper()} IT IS: {kind}",
        f"YOUR GUESS MUST BE ABOUT {ANCHOR[f.level]}.",
        f"How it did: {f.successes} of {f.n} conversations ended in a sale ({pct(f.raw_rate)}), "
        f"against {f.baseline:.0%} for comparable {noun}s -- close enough that it is not settled",
    ]
    if f.is_thin:
        lines.append("Evidence so far: THIN -- too few conversations to be sure of anything")
    if f.roas is not None:
        earning = "earning" if f.roas >= 1 else "losing money at"
        lines.append(f"Money so far: {earning} {f.roas:.2f} EGP back per 1 EGP spent")
    if f.is_fatigued:
        lines.append("It is being tested rather than scaled because the same people keep seeing it and have stopped clicking")
    if f.is_underperforming:
        lines.append("It is being tested rather than scaled because each sale costs far more than this shop usually pays")
    if f.stop_rule:
        lines.append(f"When the test stops: {f.stop_rule}")
    return "\n".join(lines)


def _template(f: DecisionFacts) -> str:
    noun = LEVEL_NOUN[f.level]
    reason = f"{f.successes} of {f.n} conversations ended in a sale ({pct(f.raw_rate)}) against a {f.baseline:.0%} bar"
    if f.is_fatigued:
        why_here = "it is showing audience fatigue, so it gets a test budget instead of a scale budget"
    elif f.is_underperforming:
        why_here = "its cost per sale is far above the account norm, so it gets a test budget instead of a scale budget"
    else:
        why_here = "there is not yet enough evidence to call it either way"
    return (
        f'Testing whether "{f.name}" ({f.detail}) is genuinely different from other {noun}s: '
        f"{reason}, and {why_here}. "
        f"Spending {f.budget_share:.0%} of the next budget should settle it."
    )


def build_hypotheses(facts_list: list[DecisionFacts], provenance: dict | None = None) -> dict[str, str]:
    """facts_list is the explore rows only -- everything else has no hypothesis."""
    if not facts_list:
        return {}

    llm, cache = client(), load_cache()
    out: dict[str, str] = {}
    counts: dict[str, int] = {}

    for f in facts_list:
        text, source = generate(
            key=f.fingerprint("hypothesis"), system=SYSTEM, user=_facts_text(f),
            fallback=_template(f),
            client=llm, cache=cache, name=f.name,
        )
        out[f.id] = text
        counts[source] = counts.get(source, 0) + 1
        if provenance is not None:
            provenance[f.id] = {"source": source, "prompt": _facts_text(f)}

    save_cache(cache)
    report("Hypotheses", counts)
    return out


PROPOSED_SYSTEM = """You write the hypothesis for an advertising test that has NEVER been run before, for a shop owner who does not know statistics.

This pairing -- one audience type shown one creative angle -- has no results yet. It is being \
proposed for testing, not reviewed after the fact. Do not describe how it "did" or "converted" -- \
it has not run.

Explain, in plain shop language:
- what this audience type has done on its own, and what this creative angle has done on its own \
(each with OTHER pairings), so the owner understands why this untried combination looks promising
- that the two have never been shown together, so nobody actually knows if they will work as well \
combined
- what result over the test period would tell the owner it is worth keeping

Rules:
- 2-3 sentences.
- Name the audience type and the creative angle explicitly.
- At most two numbers, and only the ones that actually justify the guess.
- Never invent, derive or estimate a number beyond what you are given.
- Never begin with "We think" or "This test will show".
- Plain shop language. Never write: exploit, explore, bucket, baseline, posterior, interval, \
benchmark, model, conversion rate, margin.
- No bullet points, no headings, no markdown."""


def _proposed_facts_text(p) -> str:
    return (
        f'What is being tested: showing the "{p.audience_type}" audience a "{p.theme}" creative -- '
        f"a pairing that has never been run\n"
        f"What the audience does on its own (with other creatives): converts {p.audience_rate:.0%} "
        f"across {p.audience_n} conversations\n"
        f"What the creative angle does on its own (with other audiences): converts {p.theme_rate:.0%} "
        f"across {p.theme_n} conversations\n"
        f"Account average, for comparison: {p.baseline:.0%}\n"
        f"Naive combined guess IF the two effects just add up (not a real prediction, the pairing has "
        f"never been observed): {p.expected_rate:.0%}"
    )


def rewrite_proposed(proposals: list, baseline: float, provenance: dict | None = None) -> dict[str, str]:
    """Rewrites each never-run ProposedTest's hypothesis in merchant language.
    Its deterministic `hypothesis` field (built in new_tests.py) is the
    fallback here -- same offline-safe pattern as build_hypotheses(). Runs
    after propose_new_tests() has picked the candidates; nothing here selects
    or ranks them."""
    if not proposals:
        return {}

    llm, cache = client(), load_cache()
    out: dict[str, str] = {}
    counts: dict[str, int] = {}

    for p in proposals:
        key = f"proposed|{p.audience_type}|{p.theme}|{p.audience_rate}|{p.audience_n}|{p.theme_rate}|{p.theme_n}|{baseline}"
        text, source = generate(
            key=key, system=PROPOSED_SYSTEM, user=_proposed_facts_text(p),
            fallback=p.hypothesis,
            client=llm, cache=cache, name=p.name,
        )
        out[p.id] = text
        counts[source] = counts.get(source, 0) + 1
        if provenance is not None:
            provenance[p.id] = {"source": source, "prompt": _proposed_facts_text(p)}

    save_cache(cache)
    report("Proposed-test hypotheses", counts)
    return out
