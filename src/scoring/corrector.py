from dataclasses import dataclass

from scipy.stats import beta as beta_dist

from .aggregator import RawCounts

# Fixed prior strength (alpha+beta)
# grounded in pooled variance of raw rates of ad-level (method-of-moments)
PRIOR_STRENGTH = 10

# A common standard choice (convention)
CREDIBLE_INTERVAL = 0.90

# Jeffreys prior, used for top-level items with no parent to shrink toward
UNINFORMATIVE_ALPHA = 0.5
UNINFORMATIVE_BETA = 0.5


@dataclass
class Posterior:
    score: float
    interval_low: float
    interval_high: float
    alpha_post: float
    beta_post: float
    precision: float


def _build_posterior(alpha_post: float, beta_post: float) -> Posterior:
    score = alpha_post / (alpha_post + beta_post)
    lower_q = (1 - CREDIBLE_INTERVAL) / 2
    interval_low = beta_dist.ppf(lower_q, alpha_post, beta_post)
    interval_high = beta_dist.ppf(1 - lower_q, alpha_post, beta_post)

    # Precision = inverse variance of the Beta posterior
    # Grows with prior_strength and n (alpha_post + beta_post)
    variance = (alpha_post * beta_post) / (
        (alpha_post + beta_post) ** 2 * (alpha_post + beta_post + 1)
    )
    precision = 1 / variance

    return Posterior(score=score, interval_low=interval_low, interval_high=interval_high,
                      alpha_post=alpha_post, beta_post=beta_post, precision=precision)


def compute_top_level_posterior(item: RawCounts) -> Posterior | None:
    if item.n == 0:
        return None

    alpha_post = UNINFORMATIVE_ALPHA + item.successes
    beta_post = UNINFORMATIVE_BETA + item.failures
    return _build_posterior(alpha_post, beta_post)


def compute_posterior(child: RawCounts, parent_rate: float, prior_strength: float) -> Posterior | None:
    if child.n == 0:
        return None

    alpha_prior = parent_rate * prior_strength
    beta_prior = (1 - parent_rate) * prior_strength

    alpha_post = alpha_prior + child.successes
    beta_post = beta_prior + child.failures
    return _build_posterior(alpha_post, beta_post)


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta
    from ..ingestion.joiner import join_conversations_to_meta
    from .aggregator import raw_rates

    convs = load_conversations("data/train/train.json")
    meta = load_meta("data/train/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)

    sample_ad_id = sorted(ad_rates.keys())[0]
    jc = next(j for j in joined if j.ad.id == sample_ad_id)
    adset_id = jc.adset.id
    campaign_id = jc.campaign.id

    campaign_rc = campaign_rates[campaign_id]
    adset_rc = adset_rates[adset_id]
    ad_rc = ad_rates[sample_ad_id]

    campaign_posterior = compute_top_level_posterior(campaign_rc)
    adset_posterior = compute_posterior(adset_rc, campaign_posterior.score, PRIOR_STRENGTH)
    ad_posterior = compute_posterior(ad_rc, adset_posterior.score, PRIOR_STRENGTH)

    print(f"Campaign {campaign_id}: raw_rate={campaign_rc.raw_rate:.4f} -> score={campaign_posterior.score:.4f}, "
          f"interval=[{campaign_posterior.interval_low:.4f}, {campaign_posterior.interval_high:.4f}], "
          f"precision={campaign_posterior.precision:.4f} (n={campaign_rc.n})")
    print(f"   Adset {adset_id}: raw_rate={adset_rc.raw_rate:.4f} -> shrunk score={adset_posterior.score:.4f}, "
          f"interval=[{adset_posterior.interval_low:.4f}, {adset_posterior.interval_high:.4f}], "
          f"precision={adset_posterior.precision:.4f} (n={adset_rc.n})")
    print(f"      Ad {sample_ad_id}: raw_rate={ad_rc.raw_rate:.4f} -> shrunk score={ad_posterior.score:.4f}, "
          f"interval=[{ad_posterior.interval_low:.4f}, {ad_posterior.interval_high:.4f}], "
          f"precision={ad_posterior.precision:.4f} (n={ad_rc.n})")