from dataclasses import dataclass

import numpy as np

from ..scoring.corrector import Posterior

# thresholds are derived from the distribution of shrunk scores in train.json,
# not hand-picked (same reasoning as PRIOR_STRENGTH in corrector.py)
SCALE_PERCENTILE = 66
KILL_PERCENTILE = 33


@dataclass
class Thresholds:
    scale_threshold: float
    kill_threshold: float


def compute_thresholds(
    scores: list[float],
    scale_percentile: float = SCALE_PERCENTILE,
    kill_percentile: float = KILL_PERCENTILE,
) -> Thresholds:
    scale_threshold = float(np.percentile(scores, scale_percentile))
    kill_threshold = float(np.percentile(scores, kill_percentile))
    return Thresholds(scale_threshold=scale_threshold, kill_threshold=kill_threshold)


def decide(posterior: Posterior, thresholds: Thresholds) -> str:
    if posterior.interval_low > thresholds.scale_threshold:
        return "scale"
    if posterior.interval_high < thresholds.kill_threshold:
        return "kill"
    return "hold"


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
        campaign_posterior = campaign_posteriors[adset_to_campaign[adset_id]]
        adset_posteriors[adset_id] = compute_posterior(adset_rc, campaign_posterior.score, PRIOR_STRENGTH)

    ad_posteriors = {}
    for ad_id, ad_rc in ad_rates.items():
        parent_posterior = adset_posteriors[ad_to_adset[ad_id]]
        ad_posteriors[ad_id] = compute_posterior(ad_rc, parent_posterior.score, PRIOR_STRENGTH)

    campaign_thresholds = compute_thresholds([p.score for p in campaign_posteriors.values()])
    adset_thresholds = compute_thresholds([p.score for p in adset_posteriors.values()])
    ad_thresholds = compute_thresholds([p.score for p in ad_posteriors.values()])

    print(f"campaign: scale_threshold={campaign_thresholds.scale_threshold:.4f}, "
          f"kill_threshold={campaign_thresholds.kill_threshold:.4f}")
    print(f"adset:    scale_threshold={adset_thresholds.scale_threshold:.4f}, "
          f"kill_threshold={adset_thresholds.kill_threshold:.4f}")
    print(f"ad:       scale_threshold={ad_thresholds.scale_threshold:.4f}, "
          f"kill_threshold={ad_thresholds.kill_threshold:.4f}\n")

    print("--- Campaigns ---")
    for campaign_id in sorted(campaign_posteriors.keys()):
        posterior = campaign_posteriors[campaign_id]
        action = decide(posterior, campaign_thresholds)
        print(f"{campaign_id}: score={posterior.score:.4f}, "
              f"interval=[{posterior.interval_low:.4f}, {posterior.interval_high:.4f}] -> {action}")

    print("\n--- Adsets ---")
    for adset_id in sorted(adset_posteriors.keys()):
        posterior = adset_posteriors[adset_id]
        action = decide(posterior, adset_thresholds)
        print(f"{adset_id}: score={posterior.score:.4f}, "
              f"interval=[{posterior.interval_low:.4f}, {posterior.interval_high:.4f}] -> {action}")

    print("\n--- Ads ---")
    for ad_id in sorted(ad_posteriors.keys()):
        posterior = ad_posteriors[ad_id]
        action = decide(posterior, ad_thresholds)
        print(f"{ad_id}: score={posterior.score:.4f}, "
              f"interval=[{posterior.interval_low:.4f}, {posterior.interval_high:.4f}] -> {action}")