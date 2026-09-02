from dataclasses import dataclass, field

from .action import PROBABILITY_THRESHOLD, _probabilities_against_baseline
from ..scoring.corrector import Posterior

EXPLOIT_SHARE = 0.70
EXPLORE_SHARE = 0.30

# Below this, we don't trust the posterior enough to size an exploit bet
# on it, even if it happened to land on the scale/kill side.
MIN_PRECISION_FOR_EXPLOIT = 5.0

STOP_RULE_MIN_IMPRESSIONS = 1000
STOP_RULE_MAX_DAYS = 7
STOP_RULE_CONFIDENCE = 0.90

# Requirement: explore is 2-3 specific tests per run, not "every hold item".
MAX_EXPLORE_TESTS = 3


@dataclass
class ExploitAllocation:
    id: str
    action: str
    weight: float
    budget: float
    p_better: float
    p_worse: float


@dataclass
class ExploreExperiment:
    id: str
    name: str
    hypothesis: str
    stop_rule: str
    budget: float
    p_better: float
    p_worse: float
    precision: float


@dataclass
class AllocationPlan:
    total_budget: float
    exploit: list[ExploitAllocation] = field(default_factory=list)
    explore: list[ExploreExperiment] = field(default_factory=list)


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
    total_budget: float,
) -> AllocationPlan:
    scale_candidates: list[tuple[str, Posterior, float, float]] = []
    kill_candidates: list[tuple[str, Posterior, float, float]] = []
    explore_candidates: list[tuple[str, Posterior | None, float, float, float]] = []

    for id_, posterior in posteriors.items():
        baseline = get_baseline(id_)
        action, p_better, p_worse = _classify(id_, posterior, baseline)

        if action == "scale":
            scale_candidates.append((id_, posterior, p_better, p_worse))
        elif action == "kill":
            kill_candidates.append((id_, posterior, p_better, p_worse))
        else:
            explore_candidates.append((id_, posterior, baseline, p_better, p_worse))

    plan = AllocationPlan(total_budget=total_budget)

    exploit_budget = total_budget * EXPLOIT_SHARE
    total_weight = sum(p_better for _, _, p_better, _ in scale_candidates)

    for id_, posterior, p_better, p_worse in scale_candidates:
        weight = p_better / total_weight if total_weight else 0.0
        plan.exploit.append(
            ExploitAllocation(
                id=id_, action="scale", weight=weight,
                budget=weight * exploit_budget,
                p_better=p_better, p_worse=p_worse,
            )
        )

    for id_, posterior, p_better, p_worse in kill_candidates:
        plan.exploit.append(
            ExploitAllocation(
                id=id_, action="kill", weight=0.0, budget=0.0,
                p_better=p_better, p_worse=p_worse,
            )
        )

    # Most ambiguous first: no data at all is maximally ambiguous;
    # otherwise, the closer p_better and p_worse are, the less resolved
    # the decision is, and the more a test would actually teach us.
    def _ambiguity(candidate: tuple) -> float:
        posterior, p_better, p_worse = candidate[1], candidate[3], candidate[4]
        if posterior is None:
            return 1.0
        return 1 - abs(p_better - p_worse)

    explore_candidates = sorted(explore_candidates, key=_ambiguity, reverse=True)[:MAX_EXPLORE_TESTS]

    explore_budget = total_budget * EXPLORE_SHARE
    per_experiment_budget = explore_budget / len(explore_candidates) if explore_candidates else 0.0

    for id_, posterior, baseline, p_better, p_worse in explore_candidates:
        if posterior is None:
            plan.explore.append(
                ExploreExperiment(
                    id=id_, name=f"test_{id_}",
                    hypothesis=f"{id_} has no data yet; true rate relative to baseline {baseline:.3f} is unknown.",
                    stop_rule=_build_stop_rule(),
                    budget=per_experiment_budget,
                    p_better=0.0, p_worse=0.0, precision=0.0,
                )
            )
            continue

        plan.explore.append(
            ExploreExperiment(
                id=id_, name=f"test_{id_}",
                hypothesis=_build_hypothesis(id_, posterior, baseline, p_better, p_worse),
                stop_rule=_build_stop_rule(),
                budget=per_experiment_budget,
                p_better=p_better, p_worse=p_worse, precision=posterior.precision,
            )
        )

    return plan


def print_plan(plan: AllocationPlan) -> None:
    print(f"Total budget: {plan.total_budget:.2f}")
    print(f"Exploit ({EXPLOIT_SHARE:.0%}): {plan.total_budget * EXPLOIT_SHARE:.2f}")
    print(f"Explore ({EXPLORE_SHARE:.0%}): {plan.total_budget * EXPLORE_SHARE:.2f}\n")

    print("--- Exploit ---")
    for a in sorted(plan.exploit, key=lambda a: a.budget, reverse=True):
        print(
            f"{a.id}: {a.action}, budget={a.budget:.2f}, "
            f"P(better)={a.p_better:.4f}, P(worse)={a.p_worse:.4f}"
        )

    print("\n--- Explore ---")
    for e in plan.explore:
        print(f"{e.name} ({e.id}): budget={e.budget:.2f}")
        print(f"  hypothesis: {e.hypothesis}")
        print(f"  stop_rule: {e.stop_rule}")


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta
    from ..ingestion.joiner import join_conversations_to_meta
    from ..scoring.aggregator import raw_rates
    from ..scoring.corrector import PRIOR_STRENGTH, compute_posterior, compute_top_level_posterior

    convs = load_conversations("data/train.json")
    meta = load_meta("data/meta_data.json")
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

    # TODO: replace with the real budget source (Ads API / config / user input).
    # This is a placeholder so the script is runnable end-to-end.
    TOTAL_BUDGET = 10000.0

    campaign_plan = build_allocation_plan(
        campaign_posteriors,
        get_baseline=lambda cid: overall_baseline,
        total_budget=TOTAL_BUDGET,
    )
    print("=== Campaigns ===")
    print_plan(campaign_plan)

    adset_plan = build_allocation_plan(
        adset_posteriors,
        get_baseline=lambda aid: (
            campaign_posteriors[adset_to_campaign[aid]].score
            if campaign_posteriors[adset_to_campaign[aid]] else 0.5
        ),
        total_budget=TOTAL_BUDGET,
    )
    print("\n=== Adsets ===")
    print_plan(adset_plan)

    ad_plan = build_allocation_plan(
        ad_posteriors,
        get_baseline=lambda aid: (
            adset_posteriors[ad_to_adset[aid]].score
            if adset_posteriors[ad_to_adset[aid]] else 0.5
        ),
        total_budget=TOTAL_BUDGET,
    )
    print("\n=== Ads ===")
    print_plan(ad_plan)