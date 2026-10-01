"""
Given every candidate that could end up funded as a test (the explore pool
built in allocation.py -- untested holds, fatigue-vetoed scales, CPA-vetoed
scales -- plus never-run combinations from new_tests.py), decide which ones
actually get a slot and a budget share, and write each one's hypothesis and
stop rule.

This is a different responsibility from classification (build_allocation_plan
in allocation.py deciding whether an id is scale/kill/explore-candidate in
the first place): here every candidate is already agreed to be a candidate,
and the only questions left are "can it even be resolved in time", "which
handful is worth the limited explore budget", and "what does the resulting
test say". Split out of allocation.py, which used to carry this alongside the
classification loop.
"""

from dataclasses import dataclass, field

from .guardrails import (
    CPA_STOP_MULTIPLIER,
    FATIGUE_CTR_DROP_THRESHOLD,
    FATIGUE_FREQUENCY_THRESHOLD,
    MIN_SPEND_MULTIPLIER,
)
from .stop_rules import build_stop_rule, daily_spend_rate, resolution_check
from .new_tests import ProposedTest
from ..scoring.corrector import Posterior


@dataclass
class ExploreExperiment:
    id: str
    name: str
    hypothesis: str
    stop_rule: str
    budget_share: float
    p_better: float
    p_worse: float


@dataclass
class ExploreSelection:
    kept: list[ExploreExperiment] = field(default_factory=list)
    proposed: list[ProposedTest] = field(default_factory=list)
    unresolvable: list[str] = field(default_factory=list)
    dropped_from_explore: list[str] = field(default_factory=list)  # includes unresolvable
    # id -> every number the selection read for that candidate (trace only)
    details: dict[str, dict] = field(default_factory=dict)


def _priority(id_: str, posterior: Posterior | None, roas) -> float:
    # Optimistic (UCB-style) ranking across ALL explore candidates, whatever
    # got them there (no data yet, held-at-hold, fatigue-vetoed, CPA-vetoed):
    # how good could this plausibly be, weighted by money riding on it.
    upper = posterior.interval_high if posterior is not None else 1.0
    return upper * roas(id_)


def _build_hypothesis(id_: str, posterior: Posterior, baseline: float, p_better: float, p_worse: float) -> str:
    direction = "above" if p_better >= p_worse else "below"
    confidence = max(p_better, p_worse)
    return (
        f"{id_}'s true rate is {direction} its baseline of {baseline:.3f} "
        f"(currently {confidence:.0%} confident, interval "
        f"[{posterior.interval_low:.3f}, {posterior.interval_high:.3f}])."
    )


def _hypothesis_for(c: dict, baseline_cpa: float | None) -> str:
    id_, posterior, baseline = c["id"], c["posterior"], c["baseline"]
    p_better, p_worse = c["p_better"], c["p_worse"]

    if c["reason"] == "hold" and posterior is None:
        return f"{id_} has no data yet; true rate relative to baseline {baseline:.3f} is unknown."

    if c["reason"] == "fatigued":
        return (
            f"{id_} would qualify for scale (P(better)={p_better:.0%} vs baseline "
            f"{baseline:.3f}), but is held back by the frequency+CTR fatigue guardrail: "
            f"frequency >= {FATIGUE_FREQUENCY_THRESHOLD} with a CTR drop >= "
            f"{FATIGUE_CTR_DROP_THRESHOLD:.0%} vs its own first week."
        )

    if c["reason"] == "underperforming":
        spend, sales = c["spend"], c["sales"]
        cpa = f"{spend / sales:.2f}" if sales else "undefined (0 sales)"
        return (
            f"{id_} would qualify for scale (P(better)={p_better:.0%} vs baseline "
            f"{baseline:.3f}), but is held back by the CPA stop-rule guardrail: "
            f"cost per sale={cpa} vs baseline {baseline_cpa:.2f} "
            f"(stop threshold {CPA_STOP_MULTIPLIER}x after spend >= "
            f"{MIN_SPEND_MULTIPLIER}x baseline CPA)."
        )

    return _build_hypothesis(id_, posterior, baseline, p_better, p_worse)


def select_explore_tests(
    explore_pool: list[dict],
    rates,
    daily_insights_by_ad,
    roas,
    baseline_cpa: float | None,
    horizon_days: float,
    proposals: list[ProposedTest] | None,
    max_tests: int,
    explore_share: float,
    spend_and_sales: dict | None = None,
) -> ExploreSelection:
    """rates/daily_insights_by_ad/roas mirror what build_allocation_plan already
    has: rates is {id: RawCounts}, daily_insights_by_ad is {id: [DailyInsight]}
    or None, roas is a callable id -> float (allocation.py's `_roas` closure).
    spend_and_sales is {id: (spend, sales)}, the same dict the CPA guardrail
    in allocation.py uses -- the stop rule here is judged on the same numbers."""

    def _spend_sales(id_: str) -> tuple[float | None, int]:
        return (spend_and_sales or {}).get(id_, (None, 0))

    selection = ExploreSelection()

    # 1. Drop anything that can't even reach the spend needed to judge it
    # (min_spend_multiplier x baseline CPA) inside one campaign's length --
    # a test that never gets there is not a test, it is just spend.
    resolvable = []
    for c in explore_pool:
        spend, _ = _spend_sales(c["id"])
        per_day = daily_spend_rate((daily_insights_by_ad or {}).get(c["id"], []))
        check = resolution_check(spend, baseline_cpa, per_day, horizon_days)
        selection.details[c["id"]] = {"pool_reason": c["reason"], "resolution": check}
        if check["resolvable"]:
            resolvable.append(c)
        else:
            selection.unresolvable.append(c["id"])

    # 2. Rank the whole resolvable pool once, cap once at max_tests.
    resolvable.sort(key=lambda c: _priority(c["id"], c["posterior"], roas), reverse=True)
    kept, dropped = resolvable[:max_tests], resolvable[max_tests:]
    selection.dropped_from_explore = [c["id"] for c in dropped] + selection.unresolvable
    for rank, c in enumerate(resolvable, start=1):
        upper = c["posterior"].interval_high if c["posterior"] is not None else 1.0
        selection.details[c["id"]]["priority"] = {
            "interval_high": upper, "shrunk_roas": roas(c["id"]),
            "priority": _priority(c["id"], c["posterior"], roas),
            "rank": rank, "pool_size": len(resolvable), "max_tests": max_tests,
            "kept": rank <= max_tests,
        }

    # 3. Whatever live entities didn't fill goes to combinations never run.
    free_slots = max_tests - len(kept)
    selection.proposed = list(proposals or [])[:free_slots]

    total_tests = len(kept) + len(selection.proposed)
    per_experiment_share = explore_share / total_tests if total_tests else 0.0
    for proposal in selection.proposed:
        proposal.budget_share = per_experiment_share

    # 4. Write the hypothesis + stop rule for each surviving candidate.
    for c in kept:
        id_ = c["id"]
        spend, sales = _spend_sales(id_)
        selection.details[id_]["budget"] = {
            "explore_share": explore_share, "tests_funded": total_tests,
            "live_tests": len(kept), "proposed_tests": len(selection.proposed),
            "budget_share": per_experiment_share,
        }
        selection.kept.append(
            ExploreExperiment(
                id=id_, name=f"test_{id_}", hypothesis=_hypothesis_for(c, baseline_cpa),
                stop_rule=build_stop_rule(
                    spend, sales, baseline_cpa,
                    (daily_insights_by_ad or {}).get(id_, []),
                ),
                budget_share=per_experiment_share, p_better=c["p_better"], p_worse=c["p_worse"],
            )
        )

    return selection
