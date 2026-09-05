"""
Full pipeline: Classifier -> Aggregator -> Score Corrector -> Action -> Allocation.
Runs on all campaigns, adsets, and ads and prints one ordered scoreboard per level:
raw rate, corrected score, interval, decision, and bucket - with explore test
details (hypothesis/stop_rule) listed separately below each scoreboard.
Also saves the full result (all levels) to a timestamped CSV under
outputs/, so re-running never overwrites a previous run's file.
"""

import csv
from datetime import datetime

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import load_meta
from src.ingestion.joiner import join_conversations_to_meta
from src.scoring.aggregator import raw_rates
from src.scoring.corrector import PRIOR_STRENGTH, compute_posterior, compute_top_level_posterior
from src.decision.action import PROBABILITY_THRESHOLD, decide
from src.decision.allocation import build_allocation_plan

# TODO: replace with the real budget source (Ads API / meta_data.json daily_budget),
# still a placeholder pending that decision.
TOTAL_BUDGET = 10000.0

OUTPUT_FILE = f"outputs/scoreboard_{datetime.now():%Y%m%d_%H%M%S}.csv"
CSV_FIELDS = [
    "level", "id", "raw_rate", "score", "interval_low", "interval_high",
    "action", "bucket", "hypothesis", "stop_rule",
]

# exploit-scale first (highest confidence first), then explore, then none, then exploit-kill
_SORT_ORDER = {("exploit", "scale"): 0, ("explore", "hold"): 1, ("none", "hold"): 2, ("exploit", "kill"): 3}


def build_scoreboard(rates, posteriors, get_baseline, plan):
    exploit_by_id = {a.id: a for a in plan.exploit}
    explore_by_id = {e.id: e for e in plan.explore}

    rows = []
    for id_, posterior in posteriors.items():
        rc = rates[id_]

        if posterior is None:
            rows.append({
                "id": id_, "raw_rate": rc.raw_rate, "score": None, "interval": None,
                "action": "hold", "bucket": "none",
                "hypothesis": None, "stop_rule": None,
            })
            continue

        baseline = get_baseline(id_)
        action, p_better, p_worse = decide(posterior, baseline)
        interval = (posterior.interval_low, posterior.interval_high)

        if id_ in exploit_by_id:
            rows.append({
                "id": id_, "raw_rate": rc.raw_rate, "score": posterior.score, "interval": interval,
                "action": action, "bucket": "exploit",
                "hypothesis": None, "stop_rule": None,
            })
        elif id_ in explore_by_id:
            e = explore_by_id[id_]
            rows.append({
                "id": id_, "raw_rate": rc.raw_rate, "score": posterior.score, "interval": interval,
                "action": action, "bucket": "explore",
                "hypothesis": e.hypothesis, "stop_rule": e.stop_rule,
            })
        else:
            rows.append({
                "id": id_, "raw_rate": rc.raw_rate, "score": posterior.score, "interval": interval,
                "action": action, "bucket": "none",
                "hypothesis": None, "stop_rule": None,
            })

    rows.sort(key=lambda r: (_SORT_ORDER[(r["bucket"], r["action"])], -(r["score"] or 0)))
    return rows


def print_scoreboard(label, rows):
    print(f"--- {label} ---")
    for r in rows:
        score = f"{r['score']:.3f}" if r["score"] is not None else "n/a"
        interval = f"[{r['interval'][0]:.3f}, {r['interval'][1]:.3f}]" if r["interval"] else "n/a"
        print(
            f"{r['id']:<24} raw={r['raw_rate']:.3f}  score={score:<6} interval={interval:<18} "
            f"action={r['action']:<5} bucket={r['bucket']:<8}"
        )

    explore_rows = [r for r in rows if r["bucket"] == "explore"]
    if explore_rows:
        print(f"\n{label} explore tests:")
        for r in explore_rows:
            print(f"  {r['id']}:")
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
                    "raw_rate": r["raw_rate"],
                    "score": r["score"],
                    "interval_low": r["interval"][0] if r["interval"] else "",
                    "interval_high": r["interval"][1] if r["interval"] else "",
                    "action": r["action"],
                    "bucket": r["bucket"],
                    "hypothesis": r["hypothesis"] or "",
                    "stop_rule": r["stop_rule"] or "",
                })


if __name__ == "__main__":
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

    campaign_baseline = lambda cid: overall_baseline
    adset_baseline = lambda aid: (
        campaign_posteriors[adset_to_campaign[aid]].score
        if campaign_posteriors[adset_to_campaign[aid]] else 0.5
    )
    ad_baseline = lambda aid: (
        adset_posteriors[ad_to_adset[aid]].score
        if adset_posteriors[ad_to_adset[aid]] else 0.5
    )

    campaign_plan = build_allocation_plan(campaign_posteriors, campaign_baseline, TOTAL_BUDGET)
    adset_plan = build_allocation_plan(adset_posteriors, adset_baseline, TOTAL_BUDGET)
    ad_plan = build_allocation_plan(ad_posteriors, ad_baseline, TOTAL_BUDGET)

    campaign_rows = build_scoreboard(campaign_rates, campaign_posteriors, campaign_baseline, campaign_plan)
    adset_rows = build_scoreboard(adset_rates, adset_posteriors, adset_baseline, adset_plan)
    ad_rows = build_scoreboard(ad_rates, ad_posteriors, ad_baseline, ad_plan)

    print(f"Probability threshold: {PROBABILITY_THRESHOLD:.2f}  |  Total budget: {TOTAL_BUDGET:.2f}\n")
    print_scoreboard("Campaigns", campaign_rows)
    print()
    print_scoreboard("Adsets", adset_rows)
    print()
    print_scoreboard("Ads", ad_rows)

    write_csv(OUTPUT_FILE, [("campaign", campaign_rows), ("adset", adset_rows), ("ad", ad_rows)])