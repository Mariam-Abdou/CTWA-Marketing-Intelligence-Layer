from dataclasses import dataclass, field

from .action import (
    CPA_STOP_MULTIPLIER,
    FATIGUE_CTR_DROP_THRESHOLD,
    FATIGUE_FREQUENCY_THRESHOLD,
    MIN_SPEND_MULTIPLIER,
    PROBABILITY_THRESHOLD,
    _probabilities_against_baseline,
    frequency_warning,
    is_fatigued,
    is_underperforming,
)
from ..ingestion.meta_loader import DailyInsight
from ..scoring.corrector import Posterior

EXPLOIT_SHARE = 0.70
EXPLORE_SHARE = 0.30

# Below this, we don't trust the posterior enough to size an exploit bet on it
MIN_PRECISION_FOR_EXPLOIT = 5.0

STOP_RULE_MIN_IMPRESSIONS = 1000
STOP_RULE_MAX_DAYS = 7
STOP_RULE_CONFIDENCE = 0.90

MAX_EXPLORE_TESTS = 3


@dataclass
class ExploitAllocation:
    id: str
    action: str
    weight: float
    budget_share: float
    p_better: float
    p_worse: float


@dataclass
class ExploreExperiment:
    id: str
    name: str
    hypothesis: str
    stop_rule: str
    budget_share: float
    p_better: float
    p_worse: float
    precision: float


@dataclass
class AllocationPlan:
    exploit: list[ExploitAllocation] = field(default_factory=list)
    explore: list[ExploreExperiment] = field(default_factory=list)
    fatigued: list[str] = field(default_factory=list)
    underperforming: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)


def _classify(
    id_: str,
    posterior: Posterior | None,
    baseline: float,
    probability_threshold: float = PROBABILITY_THRESHOLD,
) -> tuple[str, float, float]:
    if posterior is None:
        return "explore", 0.0, 0.0

    p_better, p_worse = _probabilities_against_baseline(posterior, baseline)

    if p_better >= probability_threshold and posterior.precision >= MIN_PRECISION_FOR_EXPLOIT:
        return "scale", p_better, p_worse
    if p_worse >= probability_threshold and posterior.precision >= MIN_PRECISION_FOR_EXPLOIT:
        return "kill", p_better, p_worse
    return "hold", p_better, p_worse


def _build_hypothesis(id_: str, posterior: Posterior, baseline: float, p_better: float, p_worse: float) -> str:
    direction = "above" if p_better >= p_worse else "below"
    confidence = max(p_better, p_worse)
    return (
        f"{id_}'s true rate is {direction} its baseline of {baseline:.3f} "
        f"(currently {confidence:.0%} confident, interval "
        f"[{posterior.interval_low:.3f}, {posterior.interval_high:.3f}])."
    )


def _build_stop_rule() -> str:
    return (
        f"Stop after {STOP_RULE_MIN_IMPRESSIONS}+ impressions AND "
        f"(P(better) or P(worse) >= {STOP_RULE_CONFIDENCE:.0%}), "
        f"or after {STOP_RULE_MAX_DAYS} days, whichever comes first."
    )


def build_allocation_plan(
    posteriors: dict[str, Posterior | None],
    get_baseline,
    daily_insights_by_ad: dict[str, list[DailyInsight]] | None = None,
    shrunk_roas: dict[str, float] | None = None,
    spend_and_orders: dict[str, tuple[float, int]] | None = None,
    baseline_cpa: float | None = None,
) -> AllocationPlan:
    # no ROAS signal yet for this id, falls back to p_better/priority alone.
    def _roas(id_: str) -> float:
        return (shrunk_roas or {}).get(id_, 1.0)

    scale_candidates: list[tuple[str, Posterior, float, float]] = []
    kill_candidates: list[tuple[str, Posterior, float, float]] = []
    explore_candidates: list[tuple[str, Posterior | None, float, float, float]] = []
    fatigued_candidates: list[tuple[str, Posterior, float, float, float]] = []
    underperforming_candidates: list[tuple[str, Posterior, float, float, float]] = []

    warned_ids: list[str] = []

    for id_, posterior in posteriors.items():
        baseline = get_baseline(id_)
        action, p_better, p_worse = _classify(id_, posterior, baseline)

        if action == "scale" and daily_insights_by_ad is not None:
            insights = daily_insights_by_ad.get(id_, [])
            if is_fatigued(insights):
                fatigued_candidates.append((id_, posterior, baseline, p_better, p_worse))
                continue
            if frequency_warning(insights):
                warned_ids.append(id_)

        if action == "scale" and spend_and_orders is not None:
            spend, orders = spend_and_orders.get(id_, (None, 0))
            if is_underperforming(spend, orders, baseline_cpa):
                underperforming_candidates.append((id_, posterior, baseline, p_better, p_worse))
                continue

        if action == "scale":
            scale_candidates.append((id_, posterior, p_better, p_worse))
        elif action == "kill":
            kill_candidates.append((id_, posterior, p_better, p_worse))
        else:
            explore_candidates.append((id_, posterior, baseline, p_better, p_worse))

    plan = AllocationPlan()
    plan.fatigued = [id_ for id_, *_ in fatigued_candidates]
    plan.underperforming = [id_ for id_, *_ in underperforming_candidates]
    plan.warned = warned_ids

    exploit_scores = {id_: p_better * _roas(id_) for id_, _, p_better, _ in scale_candidates}
    total_weight = sum(exploit_scores.values())

    for id_, posterior, p_better, p_worse in scale_candidates:
        weight = exploit_scores[id_] / total_weight if total_weight else 0.0
        plan.exploit.append(
            ExploitAllocation(
                id=id_, action="scale", weight=weight,
                budget_share=weight * EXPLOIT_SHARE,
                p_better=p_better, p_worse=p_worse,
            )
        )

    for id_, posterior, p_better, p_worse in kill_candidates:
        plan.exploit.append(
            ExploitAllocation(
                id=id_, action="kill", weight=0.0, budget_share=0.0,
                p_better=p_better, p_worse=p_worse,
            )
        )

    def _priority(candidate: tuple) -> float:
        id_, posterior = candidate[0], candidate[1]
        if posterior is None:
            return 1.0 * _roas(id_)
        return posterior.interval_high * _roas(id_)

    explore_candidates = sorted(explore_candidates, key=_priority, reverse=True)[:MAX_EXPLORE_TESTS]

    all_explore_items = explore_candidates + fatigued_candidates + underperforming_candidates
    per_experiment_share = EXPLORE_SHARE / len(all_explore_items) if all_explore_items else 0.0

    for id_, posterior, baseline, p_better, p_worse in explore_candidates:
        if posterior is None:
            plan.explore.append(
                ExploreExperiment(
                    id=id_, name=f"test_{id_}",
                    hypothesis=f"{id_} has no data yet; true rate relative to baseline {baseline:.3f} is unknown.",
                    stop_rule=_build_stop_rule(),
                    budget_share=per_experiment_share,
                    p_better=0.0, p_worse=0.0, precision=0.0,
                )
            )
            continue

        plan.explore.append(
            ExploreExperiment(
                id=id_, name=f"test_{id_}",
                hypothesis=_build_hypothesis(id_, posterior, baseline, p_better, p_worse),
                stop_rule=_build_stop_rule(),
                budget_share=per_experiment_share,
                p_better=p_better, p_worse=p_worse, precision=posterior.precision,
            )
        )

    for id_, posterior, baseline, p_better, p_worse in fatigued_candidates:
        plan.explore.append(
            ExploreExperiment(
                id=id_, name=f"test_{id_}",
                hypothesis=(
                    f"{id_} would qualify for scale (P(better)={p_better:.0%} vs baseline "
                    f"{baseline:.3f}), but is held back by the frequency+CTR fatigue guardrail: "
                    f"frequency >= {FATIGUE_FREQUENCY_THRESHOLD} with a CTR drop >= "
                    f"{FATIGUE_CTR_DROP_THRESHOLD:.0%} vs its own first week."
                ),
                stop_rule=_build_stop_rule(),
                budget_share=per_experiment_share,
                p_better=p_better, p_worse=p_worse, precision=posterior.precision,
            )
        )

    for id_, posterior, baseline, p_better, p_worse in underperforming_candidates:
        spend, orders = spend_and_orders.get(id_, (None, 0))
        cpa = f"{spend / orders:.2f}" if orders else "undefined (0 orders)"
        plan.explore.append(
            ExploreExperiment(
                id=id_, name=f"test_{id_}",
                hypothesis=(
                    f"{id_} would qualify for scale (P(better)={p_better:.0%} vs baseline "
                    f"{baseline:.3f}), but is held back by the CPA stop-rule guardrail: "
                    f"CPA={cpa} vs baseline CPA {baseline_cpa:.2f} "
                    f"(stop threshold {CPA_STOP_MULTIPLIER}x after spend >= "
                    f"{MIN_SPEND_MULTIPLIER}x baseline CPA)."
                ),
                stop_rule=_build_stop_rule(),
                budget_share=per_experiment_share,
                p_better=p_better, p_worse=p_worse, precision=posterior.precision,
            )
        )

    return plan


def print_plan(plan: AllocationPlan) -> None:
    print(f"Exploit ({EXPLOIT_SHARE:.0%} of budget)")
    print(f"Explore ({EXPLORE_SHARE:.0%} of budget)\n")

    if plan.warned:
        print(
            f"Frequency warning (2.5-{FATIGUE_FREQUENCY_THRESHOLD:.1f}, "
            f"informational only, not vetoed): {', '.join(plan.warned)}\n"
        )

    print("--- Exploit ---")
    for a in sorted(plan.exploit, key=lambda a: a.budget_share, reverse=True):
        print(
            f"{a.id}: {a.action}, budget_share={a.budget_share:.1%}, "
            f"P(better)={a.p_better:.4f}, P(worse)={a.p_worse:.4f}"
        )

    print("\n--- Explore ---")
    for e in plan.explore:
        flag = ""
        if e.id in plan.fatigued:
            flag = " [fatigue-vetoed scale]"
        elif e.id in plan.underperforming:
            flag = " [CPA-vetoed scale]"
        print(f"{e.name} ({e.id}){flag}: budget_share={e.budget_share:.1%}")
        print(f"  hypothesis: {e.hypothesis}")
        print(f"  stop_rule: {e.stop_rule}")


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta, insights_by_ad
    from ..ingestion.joiner import join_conversations_to_meta
    from ..scoring.aggregator import raw_rates
    from ..scoring.corrector import PRIOR_STRENGTH, compute_posterior, compute_top_level_posterior
    from ..scoring.revenue import revenue_totals
    from ..auditing.audit import spend_totals, shrunk_roas_by_id, baseline_cpa as compute_baseline_cpa

    convs = load_conversations("data/train/train.json")
    meta = load_meta("data/train/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)
    adset_to_campaign = {j.adset.id: j.campaign.id for j in joined}
    ad_to_adset = {j.ad.id: j.adset.id for j in joined}

    campaign_posteriors = {
        campaign_id: compute_top_level_posterior(campaign_rc)
        for campaign_id, campaign_rc in campaign_rates.items()
    }

    adset_posteriors = {}
    for adset_id, adset_rc in adset_rates.items():
        parent = campaign_posteriors[adset_to_campaign[adset_id]]
        baseline = parent.score if parent else 0.5
        adset_posteriors[adset_id] = compute_posterior(adset_rc, baseline, PRIOR_STRENGTH)

    ad_posteriors = {}
    for ad_id, ad_rc in ad_rates.items():
        parent = adset_posteriors[ad_to_adset[ad_id]]
        baseline = parent.score if parent else 0.5
        ad_posteriors[ad_id] = compute_posterior(ad_rc, baseline, PRIOR_STRENGTH)

    total_successes = sum(rc.successes for rc in campaign_rates.values())
    total_failures = sum(rc.failures for rc in campaign_rates.values())
    total_n = total_successes + total_failures
    overall_baseline = total_successes / total_n if total_n else 0.5

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
    ad_spend, adset_spend, campaign_spend = spend_totals(meta)
    campaign_roas = shrunk_roas_by_id(campaign_revenue, campaign_spend)
    adset_roas = shrunk_roas_by_id(adset_revenue, adset_spend)
    ad_roas = shrunk_roas_by_id(ad_revenue, ad_spend)

    campaign_cpa_inputs = {id_: (campaign_spend.get(id_), rev.n) for id_, rev in campaign_revenue.items()}
    adset_cpa_inputs = {id_: (adset_spend.get(id_), rev.n) for id_, rev in adset_revenue.items()}
    ad_cpa_inputs = {id_: (ad_spend.get(id_), rev.n) for id_, rev in ad_revenue.items()}

    campaign_baseline_cpa = compute_baseline_cpa(campaign_revenue, campaign_spend)
    adset_baseline_cpa = compute_baseline_cpa(adset_revenue, adset_spend)
    ad_baseline_cpa = compute_baseline_cpa(ad_revenue, ad_spend)

    campaign_plan = build_allocation_plan(
        campaign_posteriors,
        get_baseline=lambda cid: overall_baseline,
        shrunk_roas=campaign_roas,
        spend_and_orders=campaign_cpa_inputs,
        baseline_cpa=campaign_baseline_cpa,
    )
    print("=== Campaigns ===")
    print_plan(campaign_plan)

    adset_plan = build_allocation_plan(
        adset_posteriors,
        get_baseline=lambda aid: (
            campaign_posteriors[adset_to_campaign[aid]].score
            if campaign_posteriors[adset_to_campaign[aid]] else 0.5
        ),
        shrunk_roas=adset_roas,
        spend_and_orders=adset_cpa_inputs,
        baseline_cpa=adset_baseline_cpa,
    )
    print("\n=== Adsets ===")
    print_plan(adset_plan)

    ad_plan = build_allocation_plan(
        ad_posteriors,
        get_baseline=lambda aid: (
            adset_posteriors[ad_to_adset[aid]].score
            if adset_posteriors[ad_to_adset[aid]] else 0.5
        ),
        daily_insights_by_ad=insights_by_ad(meta),
        shrunk_roas=ad_roas,
        spend_and_orders=ad_cpa_inputs,
        baseline_cpa=ad_baseline_cpa,
    )
    print("\n=== Ads ===")
    print_plan(ad_plan)
