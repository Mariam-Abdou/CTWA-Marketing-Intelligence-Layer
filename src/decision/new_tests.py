"""
Proposes explore tests for combinations the account has never run.

Why this exists: the explore pool is built from posteriors, and posteriors only
exist for things already running. So the explore bucket could only ever re-test
what was already live -- and on this account everything live sits within 1-3
points of its baseline, which no amount of budget can separate. It was spending
30% of the budget to learn nothing.

The account has 5 audience types and 5 creative themes. 14 of those 25
combinations have never been run. The best audience (demographic) has only ever
been shown one theme. That gap is where the information actually is.

Ranking is a margins estimate, NOT a prediction: how each audience does across
all its creatives, and how each theme does across all its audiences, combined as
if the two effects were independent. The interaction between them is exactly what
is unknown -- which is why the output is a test to run, never a decision to fund.
"""

from collections import defaultdict
from dataclasses import dataclass

from ..ingestion.joiner import JoinedConversation
from ..ingestion.meta_loader import MetaData
from ..scoring.aggregator import _summarize
from ..config import load_config

MIN_MARGIN_N = load_config()["decision"]["min_n_for_action"]


@dataclass
class Margin:
    key: str
    successes: int
    n: int
    rate: float


@dataclass
class ProposedTest:
    audience_type: str
    theme: str
    name: str
    hypothesis: str
    stop_rule: str
    expected_rate: float
    budget_share: float = 0.0
    # Raw margins behind the deterministic hypothesis above, kept so the
    # reasoning layer can rewrite it in merchant language without re-deriving
    # anything -- see hypothesis.py:rewrite_proposed(). `hypothesis` itself
    # stays the fallback text if that rewrite is unavailable.
    audience_rate: float = 0.0
    audience_n: int = 0
    theme_rate: float = 0.0
    theme_n: int = 0
    baseline: float = 0.0

    @property
    def id(self) -> str:
        return f"new:{self.audience_type}x{self.theme}"


def _margins(groups: dict[str, list]) -> dict[str, Margin]:
    out = {}
    for key, convs in groups.items():
        rc = _summarize(convs)
        if rc.n >= MIN_MARGIN_N and rc.raw_rate is not None:
            out[key] = Margin(key=key, successes=rc.successes, n=rc.n, rate=rc.raw_rate)
    return out


def propose_new_tests(
    joined: list[JoinedConversation], meta: MetaData, baseline: float,
    horizon_days: float, limit: int = 5,
) -> list[ProposedTest]:
    creatives = {c.id: c for c in meta.creatives}

    by_audience, by_theme = defaultdict(list), defaultdict(list)
    tried: set[tuple[str, str]] = set()

    for j in joined:
        creative = creatives.get(j.ad.creative_id)
        if not creative or not creative.theme or not j.adset.audience_type:
            continue
        by_audience[j.adset.audience_type].append(j.conversation)
        by_theme[creative.theme].append(j.conversation)
        tried.add((j.adset.audience_type, creative.theme))

    audience_margins = _margins(by_audience)
    theme_margins = _margins(by_theme)

    proposals = []
    for aud in audience_margins.values():
        for theme in theme_margins.values():
            if (aud.key, theme.key) in tried:
                continue
            # Independent-effects estimate: each margin's lift over the account
            # baseline, applied together. Deliberately naive -- if it were
            # reliable this would be a funding decision, not a test.
            expected = min(1.0, aud.rate * theme.rate / baseline) if baseline else 0.0
            proposals.append(ProposedTest(
                audience_type=aud.key, theme=theme.key,
                name=f"{aud.key} audience x {theme.key} creative (never run)",
                hypothesis=(
                    f"The {aud.key} audience has never been shown a {theme.key} creative. "
                    f"On its own that audience converts {aud.rate:.0%} across "
                    f"{aud.n} conversations, and that theme converts {theme.rate:.0%} across "
                    f"{theme.n}, both against an account average of {baseline:.0%}. If the two "
                    f"carry over independently the pairing lands near {expected:.0%}, but nothing "
                    f"here measures how they interact -- that is what the test buys."
                ),
                stop_rule=(
                    f"Run it for {horizon_days:.0f} days, the length of a typical campaign here. "
                    f"Judge it on whether it clears the {baseline:.0%} account average, not on "
                    f"the {expected:.0%} guess above."
                ),
                expected_rate=expected,
                audience_rate=aud.rate, audience_n=aud.n,
                theme_rate=theme.rate, theme_n=theme.n,
                baseline=baseline,
            ))

    proposals.sort(key=lambda p: p.expected_rate, reverse=True)
    return proposals[:limit]
