"""
Everything that turns finished scoreboard rows into output: the CSV (the
brief's deliverable, one row per id), the plan JSON (the app's companion
file -- money and proposed tests, which have no id to sit on), and the
console printout. Split out of pipeline.py, which used to carry these
alongside the orchestration itself; pipeline.py now only builds rows and
calls these to write/print them.
"""

import csv
import json
from datetime import datetime

from .auditing.findings import flag_findings
from .decision.outcome_decision import PROBABILITY_THRESHOLD

CSV_FIELDS = [
    "level", "id", "name", "raw_rate", "score", "interval_low", "interval_high",
    "action", "bucket", "budget_share", "why", "reasoning", "detail", "hypothesis", "stop_rule",
]


def write_plan(path, level_data, horizon_days, priors):
    """Companion to the CSV. Carries the money a merchant decides with -- spend,
    revenue, return, sales -- and each row's parent, none of which the CSV
    schema (one row per id, per the brief) has a column for."""
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "horizon_days": horizon_days,
        "probability_threshold": PROBABILITY_THRESHOLD,
        "fitted_prior_strength": priors,
        "levels": {},
    }
    for level, rows, plan, money, parents in level_data:
        spend, revenue, roas = money
        findings = flag_findings(rows, roas)
        total_spend = sum(spend.get(r["id"], 0.0) or 0.0 for r in rows)
        total_revenue = sum(
            revenue[r["id"]].total_revenue for r in rows if r["id"] in revenue
        )
        payload["levels"][level] = {
            "totals": {
                "spend": total_spend,
                "revenue": total_revenue,
                "roas": (total_revenue / total_spend) if total_spend else None,
                "sales": sum(revenue[r["id"]].sales for r in rows if r["id"] in revenue),
                "resolved_conversations": sum(revenue[r["id"]].n for r in rows if r["id"] in revenue),
            },
            "rows": [
                {
                    "id": r["id"], "name": r["name"], "detail": r["detail"],
                    "raw_rate": r["raw_rate"], "score": r["score"],
                    "interval_low": r["interval"][0] if r["interval"] else None,
                    "interval_high": r["interval"][1] if r["interval"] else None,
                    "action": r["action"], "bucket": r["bucket"],
                    "budget_share": r["budget_share"], "why": r["why"],
                    "reasoning": r["reasoning"], "hypothesis": r["hypothesis"],
                    "stop_rule": r["stop_rule"],
                    "successes": r["successes"], "n": r["n"], "excluded": r["excluded"],
                    "parent": parents.get(r["id"]),
                    # Why the funding does not simply follow the decision. Without
                    # these a merchant sees "scale" sitting in the test bucket with
                    # nothing on the page explaining who overruled it.
                    "held_back_by": (
                        "audience fatigue" if r["id"] in plan.fatigued
                        else "cost per sale" if r["id"] in plan.underperforming
                        else None
                    ),
                    "frequency_warning": r["id"] in plan.warned,
                    "not_worth_testing": r["id"] in plan.unresolvable,
                    "lost_test_slot": (
                        r["id"] in plan.dropped_from_explore
                        and r["id"] not in plan.unresolvable
                    ),
                    "spend": spend.get(r["id"]),
                    "revenue": revenue[r["id"]].total_revenue if r["id"] in revenue else None,
                    "sales": revenue[r["id"]].sales if r["id"] in revenue else None,
                    "resolved_conversations": revenue[r["id"]].n if r["id"] in revenue else None,
                    "roas": roas.get(r["id"]),
                    "cost_per_sale": (
                        spend[r["id"]] / revenue[r["id"]].sales
                        if spend.get(r["id"]) and revenue.get(r["id"]) and revenue[r["id"]].sales
                        else None
                    ),
                }
                for r in rows
            ],
            "proposed": [
                {
                    "name": t.name, "audience_type": t.audience_type, "theme": t.theme,
                    "hypothesis": t.hypothesis, "stop_rule": t.stop_rule,
                    "budget_share": t.budget_share, "expected_rate": t.expected_rate,
                }
                for t in plan.proposed
            ],
            "unresolvable": plan.unresolvable,
            "findings": findings,
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def print_scoreboard(label, rows):
    print(f"--- {label} ---")
    for r in rows:
        score = f"{r['score']:.3f}" if r["score"] is not None else "n/a"
        interval = f"[{r['interval'][0]:.3f}, {r['interval'][1]:.3f}]" if r["interval"] else "n/a"
        budget = f"{r['budget_share']:.1%}" if r["budget_share"] else "-"
        print(
            f"{r['id']:<24} {r['name']:<28} {r['detail']:<32}\n"
            f"    raw={r['raw_rate']:.3f}  score={score:<6} interval={interval:<18} "
            f"action={r['action']:<5} bucket={r['bucket']:<8} budget={budget:<6} why={'; '.join(r['why'])}"
        )
        print(f"    -> {r['reasoning']}")

    explore_rows = [r for r in rows if r["bucket"] == "explore"]
    if explore_rows:
        print(f"\n{label} explore tests:")
        for r in explore_rows:
            print(f"  {r['name']} ({r['id']}):")
            print(f"    hypothesis: {r['hypothesis']}")
            print(f"    stop_rule: {r['stop_rule']}")


def write_csv(path, level_rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for level, rows in level_rows:
            for r in rows:
                writer.writerow({
                    "level": level,
                    "id": r["id"],
                    "name": r["name"],
                    "raw_rate": r["raw_rate"],
                    "score": r["score"],
                    "interval_low": r["interval"][0] if r["interval"] else "",
                    "interval_high": r["interval"][1] if r["interval"] else "",
                    "action": r["action"],
                    "bucket": r["bucket"],
                    "budget_share": r["budget_share"],
                    "why": "; ".join(r["why"]),
                    "reasoning": r["reasoning"],
                    "detail": r["detail"],
                    "hypothesis": r["hypothesis"] or "",
                    "stop_rule": r["stop_rule"] or "",
                })
