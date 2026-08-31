from dataclasses import dataclass

from scipy.stats import beta as beta_dist

from ..scoring.corrector import Posterior

# Temporary threshold.
# We will tune this using validation later.
PROBABILITY_THRESHOLD = 0.90


def _probabilities_against_baseline(
    posterior: Posterior,
    baseline: float,
) -> tuple[float, float]:
    """
    Calculate:
      - P(true performance > baseline)
      - P(true performance < baseline)
    """

    p_better = 1 - beta_dist.cdf(
        baseline,
        posterior.alpha_post,
        posterior.beta_post,
    )

    p_worse = beta_dist.cdf(
        baseline,
        posterior.alpha_post,
        posterior.beta_post,
    )

    return p_better, p_worse


def decide(
    posterior: Posterior,
    baseline: float,
    probability_threshold: float = PROBABILITY_THRESHOLD,
) -> str:

    p_better, p_worse = _probabilities_against_baseline(
        posterior,
        baseline,
    )

    if p_better >= probability_threshold:
        return "scale"

    if p_worse >= probability_threshold:
        return "kill"

    return "hold"


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta
    from ..ingestion.joiner import join_conversations_to_meta
    from ..scoring.aggregator import raw_rates
    from ..scoring.corrector import (
        PRIOR_STRENGTH,
        compute_posterior,
        compute_top_level_posterior,
    )

    convs = load_conversations("data/train.json")
    meta = load_meta("data/meta_data.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)

    # Build hierarchy mappings
    adset_to_campaign = {
        j.adset.id: j.campaign.id
        for j in joined
    }

    ad_to_adset = {
        j.ad.id: j.adset.id
        for j in joined
    }

    # ---------------------------------------------------------
    # 1. Overall baseline
    # ---------------------------------------------------------

    total_successes = sum(rc.successes for rc in campaign_rates.values())
    total_failures = sum(rc.failures for rc in campaign_rates.values())
    total_n = total_successes + total_failures

    overall_baseline = (
        total_successes / total_n
        if total_n
        else 0.0
    )

    print(f"Overall baseline: {overall_baseline:.4f}")
    print(f"Probability threshold: {PROBABILITY_THRESHOLD:.2f}\n")

    # ---------------------------------------------------------
    # 2. Campaign posteriors
    # ---------------------------------------------------------

    campaign_posteriors = {
        campaign_id: compute_top_level_posterior(campaign_rc)
        for campaign_id, campaign_rc in campaign_rates.items()
    }

    # ---------------------------------------------------------
    # 3. Adset posteriors
    # ---------------------------------------------------------

    adset_posteriors = {}

    for adset_id, adset_rc in adset_rates.items():

        campaign_id = adset_to_campaign[adset_id]
        campaign_posterior = campaign_posteriors[campaign_id]

        adset_posteriors[adset_id] = compute_posterior(
            adset_rc,
            campaign_posterior.score,
            PRIOR_STRENGTH,
        )

    # ---------------------------------------------------------
    # 4. Ad posteriors
    # ---------------------------------------------------------

    ad_posteriors = {}

    for ad_id, ad_rc in ad_rates.items():

        adset_id = ad_to_adset[ad_id]
        adset_posterior = adset_posteriors[adset_id]

        ad_posteriors[ad_id] = compute_posterior(
            ad_rc,
            adset_posterior.score,
            PRIOR_STRENGTH,
        )

    # ---------------------------------------------------------
    # 5. Campaign decisions
    #    Baseline = overall business rate
    # ---------------------------------------------------------

    print("--- Campaigns ---")

    for campaign_id in sorted(campaign_posteriors.keys()):

        posterior = campaign_posteriors[campaign_id]

        action = decide(
            posterior,
            overall_baseline,
        )

        p_better, p_worse = _probabilities_against_baseline(
            posterior,
            overall_baseline,
        )

        print(
            f"{campaign_id}: "
            f"score={posterior.score:.4f}, "
            f"interval=[{posterior.interval_low:.4f}, "
            f"{posterior.interval_high:.4f}], "
            f"P(better)={p_better:.4f}, "
            f"P(worse)={p_worse:.4f}, "
            f"n={posterior.n} "
            f"-> {action}"
        )

    # ---------------------------------------------------------
    # 6. Adset decisions
    #    Baseline = campaign posterior score
    # ---------------------------------------------------------

    print("\n--- Adsets ---")

    for adset_id in sorted(adset_posteriors.keys()):

        posterior = adset_posteriors[adset_id]

        campaign_id = adset_to_campaign[adset_id]
        campaign_baseline = campaign_posteriors[campaign_id].score

        action = decide(
            posterior,
            campaign_baseline,
        )

        p_better, p_worse = _probabilities_against_baseline(
            posterior,
            campaign_baseline,
        )

        print(
            f"{adset_id}: "
            f"score={posterior.score:.4f}, "
            f"interval=[{posterior.interval_low:.4f}, "
            f"{posterior.interval_high:.4f}], "
            f"baseline={campaign_baseline:.4f}, "
            f"P(better)={p_better:.4f}, "
            f"P(worse)={p_worse:.4f}, "
            f"n={posterior.n} "
            f"-> {action}"
        )

    # ---------------------------------------------------------
    # 7. Ad decisions
    #    Baseline = adset posterior score
    # ---------------------------------------------------------

    print("\n--- Ads ---")

    for ad_id in sorted(ad_posteriors.keys()):

        posterior = ad_posteriors[ad_id]

        adset_id = ad_to_adset[ad_id]
        adset_baseline = adset_posteriors[adset_id].score

        action = decide(
            posterior,
            adset_baseline,
        )

        p_better, p_worse = _probabilities_against_baseline(
            posterior,
            adset_baseline,
        )

        print(
            f"{ad_id}: "
            f"score={posterior.score:.4f}, "
            f"interval=[{posterior.interval_low:.4f}, "
            f"{posterior.interval_high:.4f}], "
            f"baseline={adset_baseline:.4f}, "
            f"P(better)={p_better:.4f}, "
            f"P(worse)={p_worse:.4f}, "
            f"n={posterior.n} "
            f"-> {action}"
        )