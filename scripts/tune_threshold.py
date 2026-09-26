"""
Tunes PROBABILITY_THRESHOLD (never on holdout -- see VALIDATION_SPLIT_REASONING.md).

What transfers between conv_train and conv_val is NOT any specific ad's score
-- conv_val's campaigns (Eid Gifting Premium, Post-Eid Lookalike Test) don't
exist in conv_train at all, and IDs are opaque anyway. What transfers is the
FITTED HYPERPARAMETER: adset/ad prior strength, estimated once from
conv_train (estimate_prior_strength), then reused to score conv_val's OWN
campaigns/adsets/ads from conv_val's OWN raw counts, cascading fresh within
conv_val (Jeffreys prior at campaign level, same as production always does
for a brand-new cycle's campaigns).

Two checks, for every candidate threshold:
  1. "Bad calls" -- scale calls whose shrunk ROAS < 1.0 (funded, losing money)
     and kill calls whose shrunk ROAS >= 1.0 (cut, still profitable). ROAS is
     real observed money, not a modeled probability, so this is an honest
     ground truth to check decide()'s threshold against -- same check
     findings.py already runs at one fixed threshold, swept here across many.
  2. A Brier-score comparison (shrunk score vs raw rate) at the conversation
     level, as each conversation's predicted P(success) -- NOT threshold-
     dependent (threshold only changes the discrete action, not the
     probability), included here as a sanity check that shrinkage is
     actually buying calibration, not just noise reduction we're assuming.

HONEST LIMITATION, read before trusting a "best" threshold: conv_val has only
2 campaigns / 4 adsets / 5 ads (see VALIDATION_SPLIT_REASONING.md). Treat the
sweep as "does the decision get meaningfully worse in either direction," not
"the precise optimal value" -- conv_train's own (larger, but in-sample) sweep
is reported alongside as a secondary sanity check, not a substitute.

Usage:
    python3 -m scripts.tune_threshold
"""

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import load_meta
from src.ingestion.joiner import join_conversations_to_meta
from src.scoring.aggregator import raw_rates
from src.scoring.classifier import classify
from src.scoring.corrector import compute_posterior, compute_top_level_posterior, estimate_prior_strength
from src.scoring.revenue import revenue_totals
from src.auditing.audit import spend_totals, shrunk_roas_by_id
from src.decision.outcome_decision import decide, MIN_N_FOR_ACTION

THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]


def score_dataset(conv_path, meta_path, adset_prior_strength, ad_prior_strength):
    """Runs the same ingest -> join -> raw_rates -> hierarchical-posterior
    pipeline pipeline.py runs, but with the shrinkage strengths passed in
    (fitted elsewhere) instead of self-fit -- so the SAME code path used for
    self-fitting (pass None) can also score a new dataset with hyperparameters
    learned from a different one."""
    convs = load_conversations(conv_path)
    meta = load_meta(meta_path)
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)
    adset_to_campaign = {j.adset.id: j.campaign.id for j in joined}
    ad_to_adset = {j.ad.id: j.adset.id for j in joined}

    if adset_prior_strength is None:
        adset_prior_strength = estimate_prior_strength(adset_rates)
    if ad_prior_strength is None:
        ad_prior_strength = estimate_prior_strength(ad_rates)

    campaign_posteriors = {cid: compute_top_level_posterior(rc) for cid, rc in campaign_rates.items()}

    adset_posteriors = {}
    for aid, rc in adset_rates.items():
        parent = campaign_posteriors[adset_to_campaign[aid]]
        baseline = parent.score if parent else 0.5
        adset_posteriors[aid] = compute_posterior(rc, baseline, adset_prior_strength)

    ad_posteriors = {}
    for aid, rc in ad_rates.items():
        parent = adset_posteriors[ad_to_adset[aid]]
        baseline = parent.score if parent else 0.5
        ad_posteriors[aid] = compute_posterior(rc, baseline, ad_prior_strength)

    total_s = sum(rc.successes for rc in campaign_rates.values())
    total_f = sum(rc.failures for rc in campaign_rates.values())
    overall_baseline = total_s / (total_s + total_f) if (total_s + total_f) else 0.5

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
    ad_spend, adset_spend, campaign_spend = spend_totals(meta)

    return {
        "joined": joined,
        "rates": {"campaign": campaign_rates, "adset": adset_rates, "ad": ad_rates},
        "posteriors": {"campaign": campaign_posteriors, "adset": adset_posteriors, "ad": ad_posteriors},
        "baselines": {
            "campaign": lambda cid: overall_baseline,
            "adset": lambda aid: campaign_posteriors[adset_to_campaign[aid]].score if campaign_posteriors[adset_to_campaign[aid]] else 0.5,
            "ad": lambda aid: adset_posteriors[ad_to_adset[aid]].score if adset_posteriors[ad_to_adset[aid]] else 0.5,
        },
        "roas": {
            "campaign": shrunk_roas_by_id(campaign_revenue, campaign_spend),
            "adset": shrunk_roas_by_id(adset_revenue, adset_spend),
            "ad": shrunk_roas_by_id(ad_revenue, ad_spend),
        },
        "adset_prior_strength": adset_prior_strength,
        "ad_prior_strength": ad_prior_strength,
    }


def bad_calls(scored, threshold, min_n=MIN_N_FOR_ACTION):
    """Counts, across all 3 levels: scale calls with roas<1.0 (funded, losing
    money) and kill calls with roas>=1.0 (cut, still profitable). Also
    returns the raw scale/kill/hold counts so a threshold that just calls
    "hold" on everything doesn't look artificially safe."""
    n_scale = n_kill = n_hold = n_bad = 0
    for level in ("campaign", "adset", "ad"):
        rates = scored["rates"][level]
        posteriors = scored["posteriors"][level]
        get_baseline = scored["baselines"][level]
        roas_by_id = scored["roas"][level]
        for id_, posterior in posteriors.items():
            if posterior is None:
                continue
            rc = rates[id_]
            action, _, _ = decide(posterior, get_baseline(id_), rc.n, probability_threshold=threshold, min_n=min_n)
            if action == "scale":
                n_scale += 1
                if roas_by_id.get(id_, 1.0) < 1.0:
                    n_bad += 1
            elif action == "kill":
                n_kill += 1
                if roas_by_id.get(id_, 1.0) >= 1.0:
                    n_bad += 1
            else:
                n_hold += 1
    return {"scale": n_scale, "kill": n_kill, "hold": n_hold, "bad": n_bad}


def conversation_brier(scored, joined) -> tuple[float, float, int]:
    """Brier score of (a) the ad-level shrunk score and (b) the ad's raw rate,
    each used as every one of that ad's own conversations' predicted
    P(success). Lower is better. Not threshold-dependent -- a sanity check
    that shrinkage is actually closer to what happened than the noisy raw
    rate, not a threshold-tuning signal."""
    shrunk_sq = raw_sq = 0.0
    n = 0
    ad_posteriors = scored["posteriors"]["ad"]
    ad_rates = scored["rates"]["ad"]
    for jc in joined:
        result = classify(jc.conversation)
        if result.success is None:
            continue
        posterior = ad_posteriors.get(jc.ad.id)
        rc = ad_rates.get(jc.ad.id)
        if posterior is None or rc is None or rc.raw_rate is None:
            continue
        y = 1.0 if result.success else 0.0
        shrunk_sq += (posterior.score - y) ** 2
        raw_sq += (rc.raw_rate - y) ** 2
        n += 1
    return (shrunk_sq / n if n else float("nan"), raw_sq / n if n else float("nan"), n)


def print_sweep(label, scored):
    # bad_calls alone rewards a threshold that just stops making calls at all
    # (0 scale + 0 kill = 0 bad by construction) -- that's not a good outcome,
    # it's a useless one. bad_rate = bad / (scale+kill) is the number that
    # actually says whether the calls being made are getting more trustworthy.
    print(f"--- {label} ---")
    print(f"{'threshold':<10}{'scale':<7}{'kill':<7}{'hold':<7}{'bad_calls':<10}{'bad_rate':<10}")
    for t in THRESHOLDS:
        r = bad_calls(scored, t)
        actionable = r["scale"] + r["kill"]
        rate = f"{r['bad']/actionable:.0%}" if actionable else "n/a (0 calls)"
        print(f"{t:<10}{r['scale']:<7}{r['kill']:<7}{r['hold']:<7}{r['bad']:<10}{rate:<10}")


if __name__ == "__main__":
    train = score_dataset(
        "data/train/conv_train.json", "data/train/meta_train.json",
        adset_prior_strength=None, ad_prior_strength=None,  # self-fit
    )
    print(f"Fitted on conv_train: adset_prior_strength={train['adset_prior_strength']:.2f}, "
          f"ad_prior_strength={train['ad_prior_strength']:.2f}\n")

    val = score_dataset(
        "data/train/conv_val.json", "data/train/meta_val.json",
        adset_prior_strength=train["adset_prior_strength"],
        ad_prior_strength=train["ad_prior_strength"],
    )

    print_sweep("CONV_VAL (target -- 2 campaigns/4 adsets/5 ads, thin: read as a direction check, not a precise optimum)", val)
    print()
    print_sweep("CONV_TRAIN (in-sample sanity check, larger N but NOT an honest holdout signal)", train)

    print()
    for label, scored in [("conv_train", train), ("conv_val", val)]:
        shrunk_brier, raw_brier, n = conversation_brier(scored, scored["joined"])
        print(
            f"{label} conversation-level Brier (n={n}): shrunk_score={shrunk_brier:.4f} "
            f"vs raw_rate={raw_brier:.4f}  ({'shrinkage helps' if shrunk_brier < raw_brier else 'raw rate looks better -- investigate'})"
        )
