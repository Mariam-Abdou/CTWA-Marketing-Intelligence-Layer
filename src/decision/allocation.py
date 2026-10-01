from dataclasses import dataclass, field

from .outcome_decision import decide_with_rule
from .guardrails import FATIGUE_FREQUENCY_THRESHOLD, cpa_check, fatigue_check
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
    # id -> every input and intermediate value behind that id's bucket and
    # budget share. Written as the plan is built, never recomputed after.
    trace: dict[str, dict] = field(default_factory=dict)


def _classify(posterior: Posterior | None, baseline: float, n: int) -> tuple[str, float, float, str]:
    if posterior is None:
        return "explore", 0.0, 0.0, "no resolved conversations yet: no posterior, goes to the test pool"
    return decide_with_rule(posterior, baseline, n)


def build_allocation_plan(
    posteriors: dict[str, Posterior | None],
    get_baseline,
    rates: dict[str, RawCounts],
    daily_insights_by_ad: dict[str, list[DailyInsight]] | None = None,
    shrunk_roas: dict[str, float] | None = None,
    spend_and_sales: dict[str, tuple[float, int]] | None = None,
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
    plan_trace: dict[str, dict] = {}

    for id_, posterior in posteriors.items():
        baseline = get_baseline(id_)
        action, p_better, p_worse, rule = _classify(posterior, baseline, _n(id_))
        t = plan_trace[id_] = {
            "baseline": baseline, "n": _n(id_), "p_better": p_better, "p_worse": p_worse,
            "raw_action": action, "rule": rule,
            "fatigue": None, "fatigue_applied": False,
            "cpa": None, "cpa_applied": False,
        }
        # Computed for every id so the trace can show the numbers; they only
        # change the outcome when the raw action is "scale" (applied=True).
        if daily_insights_by_ad is not None:
            t["fatigue"] = fatigue_check(daily_insights_by_ad.get(id_, []))
        if spend_and_sales is not None:
            _spend, _sales = spend_and_sales.get(id_, (None, 0))
            t["cpa"] = cpa_check(_spend, _sales, baseline_cpa)

        if action == "scale" and daily_insights_by_ad is not None:
            t["fatigue_applied"] = True
            if t["fatigue"]["fatigued"]:
                t["route"] = "test pool: scale vetoed by fatigue"
                fatigued_ids.append(id_)
                explore_pool.append({
                    "id": id_, "posterior": posterior, "baseline": baseline,
                    "p_better": p_better, "p_worse": p_worse, "reason": "fatigued",
                })
                continue
            if t["fatigue"]["frequency_warning"]:
                warned_ids.append(id_)

        if action == "scale" and spend_and_sales is not None:
            spend, sales = spend_and_sales.get(id_, (None, 0))
            t["cpa_applied"] = True
            if t["cpa"]["underperforming"]:
                t["route"] = "test pool: scale vetoed by cost per sale"
                underperforming_ids.append(id_)
                explore_pool.append({
                    "id": id_, "posterior": posterior, "baseline": baseline,
                    "p_better": p_better, "p_worse": p_worse, "reason": "underperforming",
                    "spend": spend, "sales": sales,
                })
                continue

        if action == "scale":
            t["route"] = "exploit candidate"
            scale_candidates.append((id_, posterior, p_better, p_worse))
        elif action == "kill":
            t["route"] = "kill: no budget"
            kill_candidates.append(id_)
        else:
            t["route"] = "test pool: undecided"
            explore_pool.append({
                "id": id_, "posterior": posterior, "baseline": baseline,
                "p_better": p_better, "p_worse": p_worse, "reason": "hold",
            })

    plan = AllocationPlan()
    plan.trace = plan_trace
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
        plan_trace[id_]["exploit_weight"] = {
            "p_better": p_better, "shrunk_roas": _roas(id_),
            "raw_weight": exploit_scores[id_], "total_raw_weight": total_weight,
            "weight": weight, "exploit_share": EXPLOIT_SHARE,
            "budget_share": weight * EXPLOIT_SHARE,
        }

    # kill: no budget
    plan.kill = kill_candidates
    for id_ in plan.kill:
        plan.bucket_by_id[id_] = "kill"

    # explore: which pool candidates actually get a slot and a budget share, with hypothesis and stop rule
    selection = select_explore_tests(
        explore_pool, rates, daily_insights_by_ad, _roas, baseline_cpa,
        horizon_days, proposals, MAX_EXPLORE_TESTS, EXPLORE_SHARE,
        spend_and_sales=spend_and_sales,
    )
    plan.explore = selection.kept
    plan.proposed = selection.proposed
    plan.unresolvable = selection.unresolvable
    plan.dropped_from_explore = selection.dropped_from_explore

    for e in plan.explore:
        plan.bucket_by_id[e.id] = "explore"

    for id_ in plan.dropped_from_explore:
        plan.bucket_by_id[id_] = "none"

    for id_, d in selection.details.items():
        plan_trace[id_]["explore_selection"] = d
        if id_ in plan.unresolvable:
            d["outcome"] = "not worth testing: cannot be judged within one campaign length"
        elif id_ in plan.dropped_from_explore:
            d["outcome"] = f"lost the test slot: ranked below the top {MAX_EXPLORE_TESTS}"
        else:
            d["outcome"] = "funded as a test"
    for id_ in plan_trace:
        plan_trace[id_]["bucket"] = plan.bucket_by_id.get(id_, "none")

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
