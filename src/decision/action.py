from dataclasses import dataclass

from scipy.stats import beta as beta_dist

from ..scoring.corrector import Posterior

# Temporary threshold. We will tune this using validation later.
PROBABILITY_THRESHOLD = 0.70


def _probabilities_against_baseline(posterior: Posterior, baseline: float) -> tuple[float, float]:
    p_better = 1 - beta_dist.cdf(baseline, posterior.alpha_post, posterior.beta_post)
    p_worse = beta_dist.cdf(baseline, posterior.alpha_post, posterior.beta_post)
    return p_better, p_worse


def decide(
    posterior: Posterior,
    baseline: float,
    probability_threshold: float = PROBABILITY_THRESHOLD,
) -> tuple[str, float, float]:
    p_better, p_worse = _probabilities_against_baseline(posterior, baseline)

    if p_better >= probability_threshold:
        return "scale", p_better, p_worse
    if p_worse >= probability_threshold:
        return "kill", p_better, p_worse
    return "hold", p_better, p_worse


def report(label, posteriors, get_baseline):
    print(f"--- {label} ---")
    for id_ in sorted(posteriors.keys()):
        posterior = posteriors[id_]
        baseline = get_baseline(id_)
        action, p_better, p_worse = decide(posterior, baseline)
        print(
            f"{id_}: score={posterior.score:.4f}, "
            f"interval=[{posterior.interval_low:.4f}, {posterior.interval_high:.4f}], "
            f"baseline={baseline:.4f}, P(better)={p_better:.4f}, P(worse)={p_worse:.4f}, -> {action}"
        )


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

    total_successes = sum(rc.successes for rc in campaign_rates.values())
    total_failures = sum(rc.failures for rc in campaign_rates.values())
    total_n = total_successes + total_failures
    overall_baseline = total_successes / total_n if total_n else 0.0

    print(f"Overall baseline: {overall_baseline:.4f}")
    print(f"Probability threshold: {PROBABILITY_THRESHOLD:.2f}\n")

    campaign_posteriors = {
        campaign_id: compute_top_level_posterior(campaign_rc)
        for campaign_id, campaign_rc in campaign_rates.items()
    }

    adset_posteriors = {
        adset_id: compute_posterior(
            adset_rc, campaign_posteriors[adset_to_campaign[adset_id]].score, PRIOR_STRENGTH
        )
        for adset_id, adset_rc in adset_rates.items()
    }

    ad_posteriors = {
        ad_id: compute_posterior(
            ad_rc, adset_posteriors[ad_to_adset[ad_id]].score, PRIOR_STRENGTH
        )
        for ad_id, ad_rc in ad_rates.items()
    }

    report("Campaigns", campaign_posteriors, lambda cid: overall_baseline)
    print()
    report("Adsets", adset_posteriors, lambda aid: campaign_posteriors[adset_to_campaign[aid]].score)
    print()
    report("Ads", ad_posteriors, lambda aid: adset_posteriors[ad_to_adset[aid]].score)