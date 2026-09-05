from dataclasses import dataclass

from scipy.stats import beta as beta_dist

from ..scoring.corrector import Posterior

# Temporary threshold. We will tune this using validation later.
PROBABILITY_THRESHOLD = 0.75


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
