from .conversation import PROBABILITY_THRESHOLD, _probabilities_against_baseline, decide, report
from .meta import (
    CPA_STOP_MULTIPLIER,
    FATIGUE_CTR_DROP_THRESHOLD,
    FATIGUE_FREQUENCY_THRESHOLD,
    FATIGUE_MIN_DAYS_FOR_CTR_CHECK,
    FATIGUE_WARNING_FREQUENCY_THRESHOLD,
    FATIGUE_WINDOW_DAYS,
    MIN_SPEND_MULTIPLIER,
    frequency_warning,
    is_fatigued,
    is_underperforming,
)


def resolve_action(
    raw_action: str,
    *,
    is_fatigued: bool = False,
    precision_ok: bool = True,
) -> str:
    # decide() (conversation.py) and is_fatigued() (meta.py) stay untouched;
    # guardrails here only push toward "hold", never create/upgrade a
    # scale or kill.
    if raw_action in ("scale", "kill") and not precision_ok:
        return "hold"
    if raw_action == "scale" and is_fatigued:
        return "hold"
    return raw_action


if __name__ == "__main__":
    from .allocation import MIN_PRECISION_FOR_EXPLOIT
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta, insights_by_ad
    from ..ingestion.joiner import join_conversations_to_meta
    from ..scoring.aggregator import raw_rates
    from ..scoring.corrector import PRIOR_STRENGTH, compute_posterior, compute_top_level_posterior

    convs = load_conversations("data/train/train.json")
    meta = load_meta("data/train/meta_train.json")
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

    print("\n--- Ads (resolved) ---")
    insights = insights_by_ad(meta)
    for id_ in sorted(ad_posteriors.keys()):
        posterior = ad_posteriors[id_]
        baseline = adset_posteriors[ad_to_adset[id_]].score
        raw_action, p_better, p_worse = decide(posterior, baseline)
        fatigued = is_fatigued(insights.get(id_, []))
        precision_ok = posterior.precision >= MIN_PRECISION_FOR_EXPLOIT
        final_action = resolve_action(raw_action, is_fatigued=fatigued, precision_ok=precision_ok)
        flag = " (overwritten)" if final_action != raw_action else ""
        print(f"{id_}: raw={raw_action} -> final={final_action}{flag}")
