
from scipy.stats import beta as beta_dist

from ..scoring.corrector import Posterior
from ..config import load_config

_cfg = load_config()["decision"]

# not yet tuned against the data.
PROBABILITY_THRESHOLD = _cfg["probability_threshold"]

MIN_N_FOR_ACTION = _cfg["min_n_for_action"]


def _probabilities_against_baseline(posterior: Posterior, baseline: float) -> tuple[float, float]:
    p_better = 1 - beta_dist.cdf(baseline, posterior.alpha_post, posterior.beta_post)
    p_worse = beta_dist.cdf(baseline, posterior.alpha_post, posterior.beta_post)
    return p_better, p_worse


def decide_with_rule(posterior: Posterior, baseline: float, n: int,
                     probability_threshold: float = PROBABILITY_THRESHOLD,
                     min_n: int = MIN_N_FOR_ACTION) -> tuple[str, float, float, str]:
    """decide() plus the name of the rule that fired, for the decision trace."""
    p_better, p_worse = _probabilities_against_baseline(posterior, baseline)

    if n < min_n:
        return "hold", p_better, p_worse, f"n={n} < min_n_for_action={min_n}: too little evidence to act"
    if p_better >= probability_threshold:
        return "scale", p_better, p_worse, f"P(better than baseline)={p_better:.3f} >= threshold {probability_threshold}"
    if p_worse >= probability_threshold:
        return "kill", p_better, p_worse, f"P(worse than baseline)={p_worse:.3f} >= threshold {probability_threshold}"
    return "hold", p_better, p_worse, f"neither P(better)={p_better:.3f} nor P(worse)={p_worse:.3f} reaches {probability_threshold}"


def decide(posterior: Posterior, baseline: float, n: int, probability_threshold: float = PROBABILITY_THRESHOLD,
    min_n: int = MIN_N_FOR_ACTION,) -> tuple[str, float, float]:
    action, p_better, p_worse, _ = decide_with_rule(posterior, baseline, n, probability_threshold, min_n)
    return action, p_better, p_worse


def report(label, posteriors, get_baseline, get_n):
    print(f"--- {label} ---")
    for id_ in sorted(posteriors.keys()):
        posterior = posteriors[id_]
        baseline = get_baseline(id_)
        action, p_better, p_worse = decide(posterior, baseline, get_n(id_))
        print(
            f"{id_}: score={posterior.score:.4f}, "
            f"interval=[{posterior.interval_low:.4f}, {posterior.interval_high:.4f}], "
            f"baseline={baseline:.4f}, P(better)={p_better:.4f}, P(worse)={p_worse:.4f}, -> {action}"
        )
