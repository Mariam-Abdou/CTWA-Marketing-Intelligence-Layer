"""
Honest, OUT-OF-SAMPLE version of the shrinkage-vs-raw-rate check
tune_threshold.py's conversation_brier() couldn't actually deliver -- that
one tested each ad's score against the SAME conversations that produced it,
so raw_rate wins by construction (it IS the in-sample optimum; shrinkage is
deliberately biased away from it on purpose).

Method: for each ad with enough conversations, split ITS OWN conversations
in time (first half vs second half, chronological by started_at -- never
random). Compute a raw rate AND a shrunk score using ONLY the early half,
then check both against the LATE half's actual outcomes -- data neither
score ever saw. This is what actually tests whether shrinkage buys you
anything: does pulling a noisy early estimate toward the parent predict what
the ad ACTUALLY DOES NEXT better than trusting the early raw rate alone.

Runs on the FULL train set (data/train/full_train.json), not
conv_train/conv_val -- this check is orthogonal to that split (it's about
within-ad temporal generalization, not next-cycle generalization), and using
all of train instead of a subset gives it the most ads to work with, without
touching holdout.

Stated simplification: the shrinkage target (the ad's parent adset score) is
computed from ALL of that adset's data, not time-split to match the ad's own
early half -- splitting the entire hierarchy consistently by one global time
cutoff would need much more data than this project has. This lets a small
amount of future information leak into the PARENT baseline only -- far
smaller than the leak this test exists to eliminate (an ad scored against its
own late data), so worth building, not worth overselling as airtight.

Usage:
    python3 -m scripts.time_split_brier
"""

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import load_meta
from src.ingestion.joiner import join_conversations_to_meta
from src.scoring.classifier import classify
from src.scoring.aggregator import raw_rates
from src.scoring.corrector import compute_posterior, compute_top_level_posterior, estimate_prior_strength

MIN_EARLY_N = 3  # early half needs at least this many resolved outcomes to fit a score from
MIN_LATE_N = 2   # late half needs at least this many resolved outcomes to check against


def split_by_time(joined_for_ad):
    ordered = sorted(joined_for_ad, key=lambda jc: jc.conversation.started_at or "")
    mid = len(ordered) // 2
    return ordered[:mid], ordered[mid:]


def resolved_counts(joined_slice):
    results = [classify(jc.conversation) for jc in joined_slice]
    scored = [r for r in results if r.success is not None]
    successes = sum(1 for r in scored if r.success)
    return successes, len(scored)


def main():
    convs = load_conversations("data/train/full_train.json")
    meta = load_meta("data/train/meta_full_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)
    adset_to_campaign = {j.adset.id: j.campaign.id for j in joined}
    ad_to_adset = {j.ad.id: j.adset.id for j in joined}
    ad_prior_strength = estimate_prior_strength(ad_rates)
    adset_prior_strength = estimate_prior_strength(adset_rates)

    campaign_posteriors = {cid: compute_top_level_posterior(rc) for cid, rc in campaign_rates.items()}
    adset_posteriors = {}
    for aid, rc in adset_rates.items():
        parent = campaign_posteriors[adset_to_campaign[aid]]
        baseline = parent.score if parent else 0.5
        adset_posteriors[aid] = compute_posterior(rc, baseline, adset_prior_strength)

    by_ad = {}
    for jc in joined:
        by_ad.setdefault(jc.ad.id, []).append(jc)

    shrunk_sq = raw_sq = 0.0
    n_late_total = 0
    rows = []

    for ad_id, jcs in by_ad.items():
        early, late = split_by_time(jcs)
        early_s, early_n = resolved_counts(early)
        late_results = [(jc, classify(jc.conversation)) for jc in late]
        late_scored = [(jc, r) for jc, r in late_results if r.success is not None]

        if early_n < MIN_EARLY_N or len(late_scored) < MIN_LATE_N:
            continue

        early_raw_rate = early_s / early_n

        parent = adset_posteriors.get(ad_to_adset[ad_id])
        parent_score = parent.score if parent else 0.5
        alpha_post = parent_score * ad_prior_strength + early_s
        beta_post = (1 - parent_score) * ad_prior_strength + (early_n - early_s)
        early_shrunk = alpha_post / (alpha_post + beta_post)

        ad_shrunk_sq = sum((early_shrunk - (1.0 if r.success else 0.0)) ** 2 for _, r in late_scored)
        ad_raw_sq = sum((early_raw_rate - (1.0 if r.success else 0.0)) ** 2 for _, r in late_scored)

        shrunk_sq += ad_shrunk_sq
        raw_sq += ad_raw_sq
        n_late_total += len(late_scored)
        rows.append({
            "ad_id": ad_id, "early_n": early_n, "early_raw_rate": early_raw_rate,
            "early_shrunk": early_shrunk, "late_n": len(late_scored),
            "late_actual_rate": sum(1 for _, r in late_scored if r.success) / len(late_scored),
        })

    print(f"Ads qualifying (early_n>={MIN_EARLY_N}, late_n>={MIN_LATE_N}): {len(rows)} / {len(by_ad)}\n")
    print(f"{'ad_id':<24}{'early_n':<9}{'raw':<8}{'shrunk':<8}{'late_n':<8}{'late_actual'}")
    for r in sorted(rows, key=lambda r: -r["early_n"]):
        print(f"{r['ad_id']:<24}{r['early_n']:<9}{r['early_raw_rate']:<8.3f}{r['early_shrunk']:<8.3f}{r['late_n']:<8}{r['late_actual_rate']:.3f}")

    print()
    if n_late_total:
        shrunk_brier = shrunk_sq / n_late_total
        raw_brier = raw_sq / n_late_total
        print(f"OUT-OF-SAMPLE Brier (n={n_late_total} late conversations, {len(rows)} ads):")
        print(f"  shrunk_score = {shrunk_brier:.4f}")
        print(f"  raw_rate     = {raw_brier:.4f}")
        verdict = "shrinkage helps" if shrunk_brier < raw_brier else "raw rate still wins here -- shrinkage isn't earning its keep on this data"
        print(f"  -> {verdict}")
    else:
        print("No ads qualified -- not enough data per ad to run this check.")


if __name__ == "__main__":
    main()
