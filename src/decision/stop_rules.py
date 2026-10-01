"""
"How much more spend, and how long, before an explore test can be called" --
commercial, CPA-guardrail based, not a
statistical resolution of the WhatsApp conversion rate.

The earlier design projected the Beta posterior forward assuming the observed
rate holds steady and reported "N more conversations" -- a unit the merchant
has no lever over, and a number that swings hard on the next single
conversation given how few an explore test usually has. The line here is
something the merchant actually controls: spend and days, gated on the same
CPA thresholds the scale/kill guardrail already uses (guardrails.py), so a
running test and the guardrail that could later veto a scale never disagree
about what "underperforming" means.
"""

from ..ingestion.meta_loader import DailyInsight
from ..config import load_config

_cpa_cfg = load_config()["cpa"]
MIN_SPEND_MULTIPLIER = _cpa_cfg["min_spend_multiplier"]
CPA_STOP_MULTIPLIER = _cpa_cfg["stop_multiplier"]


def daily_spend_rate(insights: list[DailyInsight]) -> float:
    """This test's own recent spend pace. 0 means we cannot express the stop
    rule in days."""
    active = [i for i in insights if (i.impressions or 0) > 0]
    if not active:
        return 0.0
    total_spend = sum(i.spend or 0.0 for i in active)
    active_days = len({i.date_start for i in active})
    return total_spend / active_days if active_days else 0.0


def resolution_check(
    spend: float | None, baseline_cpa: float | None, per_day: float, horizon_days: float,
) -> dict:
    """resolves_in_time() with its working, for the decision trace."""
    d = {"spend": spend, "baseline_cpa": baseline_cpa, "daily_spend_rate": per_day,
         "horizon_days": horizon_days, "min_spend_multiplier": MIN_SPEND_MULTIPLIER}
    if baseline_cpa is None:
        return {**d, "resolvable": True, "reason": "no baseline CPA, nothing to check"}
    judge_line = baseline_cpa * MIN_SPEND_MULTIPLIER
    needed = max(0.0, judge_line - (spend or 0.0))
    d.update({"judge_line": judge_line, "spend_still_needed": needed})
    if needed <= 0:
        return {**d, "resolvable": True, "reason": "already past the judging line"}
    if not per_day:
        return {**d, "resolvable": False, "reason": "no recent delivery pace, cannot reach the judging line"}
    days = needed / per_day
    ok = days <= horizon_days
    return {**d, "days_to_judge": days, "resolvable": ok,
            "reason": "reaches the judging line within one campaign length" if ok
                      else "would take longer than one campaign length to judge"}


def resolves_in_time(
    spend: float | None, baseline_cpa: float | None, per_day: float, horizon_days: float,
) -> bool:
    """Whether this test can even reach the spend needed to judge it
    (min_spend_multiplier x baseline CPA) inside one campaign's length.
    No baseline CPA to compare against, or already past the judging line,
    both count as "yes" -- there's either nothing to check yet or the line
    is already crossed."""
    if baseline_cpa is None:
        return True
    needed = max(0.0, baseline_cpa * MIN_SPEND_MULTIPLIER - (spend or 0.0))
    if needed <= 0:
        return True
    if not per_day:
        return False
    return needed / per_day <= horizon_days


def build_stop_rule(
    spend: float | None, orders: int, baseline_cpa: float | None,
    insights: list[DailyInsight],
) -> str:
    if baseline_cpa is None:
        return "No account-level CPA baseline to compare against yet -- cannot set a spend-based stop line."

    spend = spend or 0.0
    per_day = daily_spend_rate(insights)
    judge_line = baseline_cpa * MIN_SPEND_MULTIPLIER
    stop_line = baseline_cpa * CPA_STOP_MULTIPLIER

    if spend >= judge_line:
        if orders == 0:
            return (
                f"Already spent {spend:,.0f} (past the {judge_line:,.0f} needed to judge) "
                "with zero orders. Stop now."
            )
        cpa = spend / orders
        if cpa > stop_line:
            return (
                f"Already spent {spend:,.0f} at a CPA of {cpa:,.0f}, above the "
                f"{stop_line:,.0f} stop line ({CPA_STOP_MULTIPLIER}x baseline). Stop now."
            )
        cpa = spend / orders
        return (
            f"Spent {spend:,.0f} so far ({orders} order{'s' if orders != 1 else ''}, CPA "
            f"{cpa:,.0f}), past the {judge_line:,.0f} needed to judge and still under the "
            f"{stop_line:,.0f} CPA stop line -- keep it running and re-check after the next "
            "few orders."
        )

    remaining = judge_line - spend
    stop_clause = (
        f"Stop it then if it still has zero orders, or if its CPA is above "
        f"{stop_line:,.0f} ({CPA_STOP_MULTIPLIER}x baseline)."
    )
    if not per_day:
        return (
            f"Needs {remaining:,.0f} more spend (no recent delivery pace to convert that "
            f"into days) before there is enough spend to judge. {stop_clause}"
        )
    days = round(remaining / per_day)
    return (
        f"Give it about {days:,} more day{'s' if days != 1 else ''} at its recent pace "
        f"({remaining:,.0f} more spend) before judging. {stop_clause}"
    )
