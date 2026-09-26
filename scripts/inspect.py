"""
One id, full trace: classifier -> aggregator -> corrector -> decision, plus
the allocation/guardrail reasoning if it's live in the current plan.

This replaces the demo blocks that used to live at the bottom of
corrector.py, action.py and allocation.py -- each one rebuilt the whole
posterior chain from scratch just to print one example, and the three
copies drifted out of sync with each other and with the real pipeline
(see auditing/findings.py's docstring for a case where that already bit us).
This script is the one place that chain gets built for inspection; the
pipeline itself (src/pipeline.py) never imports it.

Usage:
    python3 -m scripts.inspect --level ad --id 120209876543210009
    python3 -m scripts.inspect --level adset --id <adset_id>
    python3 -m scripts.inspect --level campaign --id <campaign_id>
    python3 -m scripts.inspect --level ad --id <ad_id> --data data/train  # default
"""

import argparse

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import (
    load_meta, insights_by_ad, insights_by_adset, insights_by_campaign,
)
from src.ingestion.joiner import join_conversations_to_meta
from src.scoring.classifier import classify
from src.scoring.aggregator import raw_rates
from src.scoring.corrector import compute_posterior, compute_top_level_posterior, estimate_prior_strength
from src.scoring.revenue import revenue_totals
from src.auditing.audit import spend_totals, baseline_cpa as compute_baseline_cpa
from src.decision.outcome_decision import decide
from src.decision.action import resolve_action
from src.decision.guardrails import is_fatigued, is_underperforming, describe_signals

LEVELS = ("campaign", "adset", "ad")


def _load(train_dir: str):
    convs = load_conversations(f"{train_dir}/conv_train.json")
    meta = load_meta(f"{train_dir}/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable
    return convs, meta, joined


def _build_chain(joined, meta):
    """Everything build_scoreboard()/build_allocation_plan() need, computed
    once -- same shape src/pipeline.py builds, so what this script shows is
    what the real run would have shown."""
    ad_rates, adset_rates, campaign_rates = raw_rates(joined)
    adset_to_campaign = {j.adset.id: j.campaign.id for j in joined}
    ad_to_adset = {j.ad.id: j.adset.id for j in joined}

    adset_prior_strength = estimate_prior_strength(adset_rates)
    ad_prior_strength = estimate_prior_strength(ad_rates)

    campaign_posteriors = {
        cid: compute_top_level_posterior(rc) for cid, rc in campaign_rates.items()
    }
    adset_posteriors = {
        aid: compute_posterior(rc, campaign_posteriors[adset_to_campaign[aid]].score, adset_prior_strength)
        for aid, rc in adset_rates.items()
    }
    ad_posteriors = {
        aid: compute_posterior(rc, adset_posteriors[ad_to_adset[aid]].score, ad_prior_strength)
        for aid, rc in ad_rates.items()
    }

    total_successes = sum(rc.successes for rc in campaign_rates.values())
    total_failures = sum(rc.failures for rc in campaign_rates.values())
    total_n = total_successes + total_failures
    overall_baseline = total_successes / total_n if total_n else 0.5

    rates = {"campaign": campaign_rates, "adset": adset_rates, "ad": ad_rates}
    posteriors = {"campaign": campaign_posteriors, "adset": adset_posteriors, "ad": ad_posteriors}
    baselines = {
        "campaign": lambda cid: overall_baseline,
        "adset": lambda aid: campaign_posteriors[adset_to_campaign[aid]].score,
        "ad": lambda aid: adset_posteriors[ad_to_adset[aid]].score,
    }
    parent_id = {
        "campaign": lambda cid: None,
        "adset": lambda aid: adset_to_campaign[aid],
        "ad": lambda aid: ad_to_adset[aid],
    }
    insights_fn = {"campaign": insights_by_campaign, "adset": insights_by_adset, "ad": insights_by_ad}
    return rates, posteriors, baselines, parent_id, insights_fn


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--level", choices=LEVELS, required=True)
    parser.add_argument("--id", required=True, help="campaign/adset/ad id to inspect")
    parser.add_argument("--data", default="data/train", help="dir with conv_train.json + meta_train.json")
    args = parser.parse_args()

    convs, meta, joined = _load(args.data)
    rates, posteriors, baselines, parent_id, insights_fn = _build_chain(joined, meta)

    rc = rates[args.level].get(args.id)
    if rc is None:
        print(f"No {args.level} with id {args.id} in {args.data}. "
              f"Known ids: {sorted(rates[args.level])[:5]}{'...' if len(rates[args.level]) > 5 else ''}")
        return

    print("=" * 70)
    print(f"STEP 1 -- classifier: every conversation under this {args.level}")
    print("=" * 70)
    id_field = {"campaign": "campaign", "adset": "adset", "ad": "ad"}[args.level]
    matching = [
        jc.conversation for jc in joined
        if getattr(jc, id_field).id == args.id
    ]
    for c in matching:
        r = classify(c)
        print(f"  {c.id} | outcome={c.outcome.type:12s} | success={r.success}")

    print()
    print("=" * 70)
    print("STEP 2 -- aggregator: raw counts")
    print("=" * 70)
    print(f"  {rc}")

    print()
    print("=" * 70)
    print("STEP 3 -- corrector: shrunk posterior")
    print("=" * 70)
    posterior = posteriors[args.level].get(args.id)
    baseline = baselines[args.level](args.id)
    if posterior is None:
        print("  n=0, no posterior.")
    else:
        print(f"  score={posterior.score:.4f}  interval=[{posterior.interval_low:.4f}, {posterior.interval_high:.4f}]"
              f"  (baseline={baseline:.4f}, raw_rate={rc.raw_rate})")

    print()
    print("=" * 70)
    print("STEP 4 -- decision")
    print("=" * 70)
    if posterior is None:
        print("  hold (no data)")
    else:
        raw_action, p_better, p_worse = decide(posterior, baseline, rc.n)
        insights = insights_fn[args.level](meta).get(args.id, [])
        fatigued = is_fatigued(insights)

        ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
        ad_spend, adset_spend, campaign_spend = spend_totals(meta)
        revenue_by_level = {"campaign": campaign_revenue, "adset": adset_revenue, "ad": ad_revenue}
        spend_by_level = {"campaign": campaign_spend, "adset": adset_spend, "ad": ad_spend}
        rev = revenue_by_level[args.level]
        spd = spend_by_level[args.level]
        baseline_cpa = compute_baseline_cpa(rev, spd)
        spend, orders = spd.get(args.id), (rev[args.id].n if args.id in rev else 0)
        underperforming = is_underperforming(spend, orders, baseline_cpa)

        final_action = resolve_action(raw_action, is_fatigued=fatigued, is_underperforming=underperforming)
        print(f"  P(better)={p_better:.4f}  P(worse)={p_worse:.4f}  n={rc.n}")
        print(f"  raw_action={raw_action}  fatigued={fatigued}  underperforming={underperforming}"
              f"  -> final_action={final_action}")
        signals = describe_signals(insights)
        if signals:
            print(f"  Meta signals (context only): {signals}")

    parent = parent_id[args.level](args.id)
    if parent:
        print(f"\n  parent {('campaign' if args.level == 'adset' else 'adset')}: {parent}")


if __name__ == "__main__":
    main()
