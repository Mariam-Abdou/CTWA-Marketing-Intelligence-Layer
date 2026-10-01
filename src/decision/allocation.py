from dataclasses import dataclass, field

from .outcome_decision import decide
from .guardrails import FATIGUE_FREQUENCY_THRESHOLD, frequency_warning, is_fatigued, is_underperforming
from .explore_selection import ExploreExperiment, select_explore_tests
from ..ingestion.meta_loader import DailyInsight
from ..scoring.aggregator import RawCounts

from ..scoring.corrector import Posterior
from .new_tests import ProposedTest
from ..config import load_config

_alloc_cfg = load_config()["allocation"]

EXPLOIT_SHARE = _alloc_cfg["exploit_share"]
EXPLORE_SHARE = _alloc_cfg["explore_share"]

MAX_EXPLORE_TESTS = _alloc_cfg["max_explore_tests"]


@dataclass
class ExploitAllocation:
    id: str
    action: str
    weight: float
    budget_share: float
    p_better: float
    p_worse: float


@dataclass
class AllocationPlan:
    exploit: list[ExploitAllocation] = field(default_factory=list)
    explore: list[ExploreExperiment] = field(default_factory=list)
    kill: list[str] = field(default_factory=list)
    proposed: list[ProposedTest] = field(default_factory=list)
    unresolvable: list[str] = field(default_factory=list)
    bucket_by_id: dict[str, str] = field(default_factory=dict)
    fatigued: list[str] = field(default_factory=list)
    underperforming: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    dropped_from_explore: list[str] = field(default_factory=list)


def _classify(posterior: Posterior | None, baseline: float, n: int) -> tuple[str, float, float]:
    if posterior is None:
        return "explore", 0.0, 0.0
    return decide(posterior, baseline, n)


def build_allocation_plan(
    posteriors: dict[str, Posterior | None],
    get_baseline,
    rates: dict[str, RawCounts],
    daily_insights_by_ad: dict[str, list[DailyInsight]] | None = None,
    shrunk_roas: dict[str, float] | None = None,
    spend_and_orders: dict[str, tuple[float, int]] | None = None,
    baseline_cpa: float | None = None,
    horizon_days: float = 30.0,
    proposals: list[ProposedTest] | None = None,
) -> AllocationPlan:
    def _roas(id_: str) -> float:
        return (shrunk_roas or {}).get(id_, 1.0)

    def _n(id_: str) -> int:
        rc = rates.get(id_)
        return rc.n if rc else 0

    scale_candidates: list[tuple[str, Posterior, float, float]] = []
    kill_candidates: list[str] = []
    explore_pool: list[dict] = []  # everything that COULD end up in explore, one pool, capped once below

    fatigued_ids, underperforming_ids, warned_ids = [], [], []

    for id_, posterior in posteriors.items():
        baseline = get_baseline(id_)
        action, p_better, p_worse = _classify(posterior, baseline, _n(id_))

        if action == "scale" and daily_insights_by_ad is not None:
            insights = daily_insights_by_ad.get(id_, [])
            if is_fatigued(insights):
                fatigued_ids.append(id_)
                explore_pool.append({
                    "id": id_, "posterior": posterior, "baseline": baseline,
                    "p_better": p_better, "p_worse": p_worse, "reason": "fatigued",
                })
                continue
            if frequency_warning(insights):
                warned_ids.append(id_)

        if action == "scale" and spend_and_orders is not None:
            spend, orders = spend_and_orders.get(id_, (None, 0))
            if is_underperforming(spend, orders, baseline_cpa):
                underperforming_ids.append(id_)
                explore_pool.append({
                    "id": id_, "posterior": posterior, "baseline": baseline,
                    "p_better": p_better, "p_worse": p_worse, "reason": "underperforming",
                    "spend": spend, "orders": orders,
                })
                continue

        if action == "scale":
            scale_candidates.append((id_, posterior, p_better, p_worse))
        elif action == "kill":
            kill_candidates.append(id_)
        else:
            explore_pool.append({
                "id": id_, "posterior": posterior, "baseline": baseline,
                "p_better": p_better, "p_worse": p_worse, "reason": "hold",
            })

    plan = AllocationPlan()
    plan.fatigued = fatigued_ids
    plan.underperforming = underperforming_ids
    plan.warned = warned_ids

    # exploit: confidence x ROAS weighted split of EXPLOIT_SHARE 
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
        plan.bucket_by_id[id_] = "exploit"

    # kill: no budget
    plan.kill = kill_candidates
    for id_ in plan.kill:
        plan.bucket_by_id[id_] = "kill"

    # explore: which pool candidates actually get a slot and a budget share, with hypothesis and stop rule
    selection = select_explore_tests(
        explore_pool, rates, daily_insights_by_ad, _roas, baseline_cpa,
        horizon_days, proposals, MAX_EXPLORE_TESTS, EXPLORE_SHARE,
        spend_and_orders=spend_and_orders,
    )
    plan.explore = selection.kept
    plan.proposed = selection.proposed
    plan.unresolvable = selection.unresolvable
    plan.dropped_from_explore = selection.dropped_from_explore

    for e in plan.explore:
        plan.bucket_by_id[e.id] = "explore"

    for id_ in plan.dropped_from_explore:
        plan.bucket_by_id[id_] = "none"

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

    if plan.kill:
        print("\n--- Kill (no budget, stop entirely) ---")
        for id_ in plan.kill:
            print(id_)

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

    if plan.dropped_from_explore:
        print(
            f"\n--- Held, no budget (lost the top-{MAX_EXPLORE_TESTS} explore ranking) ---"
        )
        print(", ".join(plan.dropped_from_explore))
