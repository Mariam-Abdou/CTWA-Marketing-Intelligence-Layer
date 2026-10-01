from dataclasses import dataclass

from scipy.stats import beta as beta_dist

from .aggregator import RawCounts
from ..config import load_config

_cfg = load_config()["scoring"]

# Fallback prior strength (alpha+beta), used only when a level doesn't have enough groups to fit one from data
PRIOR_STRENGTH = _cfg["prior_strength"]
PRIOR_STRENGTH_MIN_GROUP_N = _cfg["prior_strength_min_group_n"]
PRIOR_STRENGTH_MIN_GROUPS = _cfg["prior_strength_min_groups"]
PRIOR_STRENGTH_FLOOR = _cfg["prior_strength_floor"]
PRIOR_STRENGTH_CEILING = _cfg["prior_strength_ceiling"]

# Convention choice
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
    # The prior that was added to the observed counts -- kept so the trace can
    # show "prior + data = posterior" instead of only the result.
    alpha_prior: float | None = None
    beta_prior: float | None = None


def _build_posterior(alpha_post: float, beta_post: float,
                     alpha_prior: float | None = None, beta_prior: float | None = None) -> Posterior:
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
                      alpha_post=alpha_post, beta_post=beta_post, precision=precision,
                      alpha_prior=alpha_prior, beta_prior=beta_prior)


def compute_top_level_posterior(item: RawCounts) -> Posterior | None:
    if item.n == 0:
        return None

    alpha_post = UNINFORMATIVE_ALPHA + item.successes
    beta_post = UNINFORMATIVE_BETA + item.failures
    return _build_posterior(alpha_post, beta_post, UNINFORMATIVE_ALPHA, UNINFORMATIVE_BETA)


def compute_posterior(child: RawCounts, parent_rate: float, prior_strength: float) -> Posterior | None:
    if child.n == 0:
        return None

    alpha_prior = parent_rate * prior_strength
    beta_prior = (1 - parent_rate) * prior_strength

    alpha_post = alpha_prior + child.successes
    beta_post = beta_prior + child.failures
    return _build_posterior(alpha_post, beta_post, alpha_prior, beta_prior)


def estimate_prior_strength_details(raw_counts: dict[str, RawCounts], *, min_group_n: int = PRIOR_STRENGTH_MIN_GROUP_N,
    min_groups: int = PRIOR_STRENGTH_MIN_GROUPS, floor: float = PRIOR_STRENGTH_FLOOR, ceiling: float = PRIOR_STRENGTH_CEILING,
    fallback: float = PRIOR_STRENGTH) -> dict:
    """estimate_prior_strength() with its working: which groups qualified,
    the pooled rate, observed vs noise variance, and whether the value came
    from the fit, the fallback, or the floor/ceiling clamp."""
    d = {"min_group_n": min_group_n, "min_groups": min_groups, "floor": floor,
         "ceiling": ceiling, "fallback": fallback}
    groups = [rc for rc in raw_counts.values() if rc.n >= min_group_n]
    d["qualifying_groups"] = len(groups)
    if len(groups) < min_groups:
        return {**d, "prior_strength": fallback, "source": "fallback: too few qualifying groups"}

    total_successes = sum(rc.successes for rc in groups)
    total_n = sum(rc.n for rc in groups)
    if total_n == 0:
        return {**d, "prior_strength": fallback, "source": "fallback: no observations"}
    p_bar = total_successes / total_n

    k = len(groups)
    observed_variance = sum((rc.raw_rate - p_bar) ** 2 for rc in groups) / (k - 1)

    mean_n = total_n / k
    expected_noise = p_bar * (1 - p_bar) / mean_n if mean_n else 0.0

    between_group_variance = observed_variance - expected_noise
    d.update({"pooled_rate": p_bar, "observed_variance": observed_variance, "mean_group_n": mean_n,
              "expected_noise_variance": expected_noise, "between_group_variance": between_group_variance})
    if between_group_variance <= 0:
        return {**d, "prior_strength": ceiling, "source": "ceiling: no real variation beyond noise"}

    raw = p_bar * (1 - p_bar) / between_group_variance - 1
    value = max(floor, min(ceiling, raw))
    source = "fitted" if value == raw else ("clamped to floor" if value == floor else "clamped to ceiling")
    return {**d, "unclamped_prior_strength": raw, "prior_strength": value, "source": source}


def estimate_prior_strength(raw_counts: dict[str, RawCounts], *, min_group_n: int = PRIOR_STRENGTH_MIN_GROUP_N,
    min_groups: int = PRIOR_STRENGTH_MIN_GROUPS, floor: float = PRIOR_STRENGTH_FLOOR, ceiling: float = PRIOR_STRENGTH_CEILING,
    fallback: float = PRIOR_STRENGTH) -> float:
    return estimate_prior_strength_details(
        raw_counts, min_group_n=min_group_n, min_groups=min_groups,
        floor=floor, ceiling=ceiling, fallback=fallback,
    )["prior_strength"]
