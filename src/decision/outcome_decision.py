
from scipy.stats import beta as beta_dist

from ..scoring.corrector import Posterior
from ..config import load_config

_cfg = load_config()["decision"]

# Config-driven (config.yaml: decision.probability_threshold). Temporary --
# not yet tuned against a holdout evaluation.
PROBABILITY_THRESHOLD = _cfg["probability_threshold"]

# Config-driven (config.yaml: decision.min_n_for_action). Minimum number of
# *real, resolved* conversations behind a score before we'll act on it at all
# (scale/kill). Below this, one flipped outcome can swing the whole call,
# however confident the posterior math looks -- see corrector.py: with a
# fixed prior strength the posterior's "precision" is already high from the
# prior alone even at n=0, so precision can't be used as the evidence gate.
# n is the honest, data-grounded gate instead.
MIN_N_FOR_ACTION = _cfg["min_n_for_action"]


def _probabilities_against_baseline(posterior: Posterior, baseline: float) -> tuple[float, float]:
    p_better = 1 - beta_dist.cdf(baseline, posterior.alpha_post, posterior.beta_post)
    p_worse = beta_dist.cdf(baseline, posterior.alpha_post, posterior.beta_post)
    return p_better, p_worse


def decide(
    posterior: Posterior,
    baseline: float,
    n: int,
    probability_threshold: float = PROBABILITY_THRESHOLD,
    min_n: int = MIN_N_FOR_ACTION,
) -> tuple[str, float, float]:
    p_better, p_worse = _probabilities_against_baseline(posterior, baseline)

    if n < min_n:
        return "hold", p_better, p_worse
    if p_better >= probability_threshold:
        return "scale", p_better, p_worse
    if p_worse >= probability_threshold:
        return "kill", p_better, p_worse
    return "hold", p_better, p_worse


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
