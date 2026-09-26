from dataclasses import dataclass

from scipy.stats import beta as beta_dist

from .aggregator import RawCounts
from ..config import load_config

_cfg = load_config()["scoring"]

# Fallback prior strength (alpha+beta), used only when a level doesn't have
# enough groups to fit one from data -- see estimate_prior_strength() below,
# which is what actually drives shrinkage in the pipeline now.
PRIOR_STRENGTH = _cfg["prior_strength"]
PRIOR_STRENGTH_MIN_GROUP_N = _cfg["prior_strength_min_group_n"]
PRIOR_STRENGTH_MIN_GROUPS = _cfg["prior_strength_min_groups"]
PRIOR_STRENGTH_FLOOR = _cfg["prior_strength_floor"]
PRIOR_STRENGTH_CEILING = _cfg["prior_strength_ceiling"]

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


def estimate_prior_strength(
    raw_counts: dict[str, RawCounts],
    *,
    min_group_n: int = PRIOR_STRENGTH_MIN_GROUP_N,
    min_groups: int = PRIOR_STRENGTH_MIN_GROUPS,
    floor: float = PRIOR_STRENGTH_FLOOR,
    ceiling: float = PRIOR_STRENGTH_CEILING,
    fallback: float = PRIOR_STRENGTH,
) -> float:
    """
    Method-of-moments estimate of the Beta prior's strength (alpha+beta),
    fit fresh from this level's own raw rates (e.g. every ad's RawCounts this
    cycle) instead of a hardcoded constant. Re-run on more cycles of data
    (past + new), the estimate moves -- this is the "dynamic muscle" plugged
    into the static shrinkage formula in compute_posterior().

    The idea: if every group in `raw_counts` truly shared one underlying rate
    p_bar, the only reason each group's own raw_rate would differ from p_bar
    is Binomial sampling noise (~p_bar*(1-p_bar)/n for a group with n
    observations). Whatever variance across groups is LEFT OVER once that
    expected noise is subtracted out is real, prior-worthy heterogeneity.
    Less real heterogeneity -> a group's own point estimate deserves less
    trust and should be pulled harder toward p_bar -> higher prior strength.

    For a Beta(alpha, beta) prior with mean p_bar, Var = p_bar*(1-p_bar) /
    (alpha+beta+1). Solving for alpha+beta given the estimated real
    between-group variance gives this function's return value.

    Falls back to `fallback` when there aren't enough groups with enough data
    to estimate anything meaningful from (see min_group_n / min_groups).
    """
    groups = [rc for rc in raw_counts.values() if rc.n >= min_group_n]
    if len(groups) < min_groups:
        return fallback

    total_successes = sum(rc.successes for rc in groups)
    total_n = sum(rc.n for rc in groups)
    if total_n == 0:
        return fallback
    p_bar = total_successes / total_n

    k = len(groups)
    observed_variance = sum((rc.raw_rate - p_bar) ** 2 for rc in groups) / (k - 1)

    mean_n = total_n / k
    expected_noise = p_bar * (1 - p_bar) / mean_n if mean_n else 0.0

    between_group_variance = observed_variance - expected_noise
    if between_group_variance <= 0:
        # No detectable real heterogeneity above sampling noise: the data is
        # saying "trust the shared rate, distrust individual point estimates"
        # -- i.e. maximal shrinkage, capped at `ceiling` rather than infinity.
        return ceiling

    prior_strength = p_bar * (1 - p_bar) / between_group_variance - 1
    return max(floor, min(ceiling, prior_strength))


# Demo removed -- it rebuilt the whole posterior chain from scratch just to
# print one example, drifting out of sync with the real pipeline in the
# process (see auditing/findings.py). To inspect one id end-to-end, run:
#   python3 -m scripts.inspect --level <campaign|adset|ad> --id <id>
