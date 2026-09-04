import statistics

from ..ingestion.conversation_loader import load_conversations
from ..ingestion.meta_loader import load_meta
from ..ingestion.joiner import join_conversations_to_meta
from ..scoring.aggregator import raw_rates
from ..scoring.corrector import PRIOR_STRENGTH, compute_posterior, compute_top_level_posterior
from ..scoring.revenue import revenue_totals
from ..decision.action import decide
from .audit import spend_totals, build_roas_rows


def flag_findings(actions_by_id: dict, roas_rows: list[dict]) -> list[dict]:
    by_id = {r["id"]: r for r in roas_rows}
    all_roas = [r["roas"] for r in roas_rows if r["roas"] is not None]
    overall_median = statistics.median(all_roas) if all_roas else None

    findings = []
    for id_, r in by_id.items():
        action = actions_by_id.get(id_)
        roas = r["roas"]
        if roas is None or action is None:
            continue

        if action == "scale" and roas < 1.0:
            findings.append({
                "id": id_, "action": action, "roas": roas,
                "finding": f"funded as SCALE but roas={roas:.2f} is below breakeven (losing money)",
            })
        elif action == "kill" and roas >= 1.0:
            findings.append({
                "id": id_, "action": action, "roas": roas,
                "finding": f"cut as KILL but roas={roas:.2f} is still profitable",
            })
        elif action != "scale" and overall_median is not None and roas > overall_median:
            findings.append({
                "id": id_, "action": action, "roas": roas,
                "finding": f"{action.upper()} but roas={roas:.2f} beats the level's overall median ({overall_median:.2f})",
            })

    findings.sort(key=lambda f: -f["roas"])
    return findings


def print_findings(label, findings):
    if not findings:
        print(f"--- {label} findings: none ---")
        return
    print(f"--- {label} findings ({len(findings)}) ---")
    for f in findings:
        print(f"{f['id']:<24} {f['finding']}")


if __name__ == "__main__":
    convs = load_conversations("data/train.json")
    meta = load_meta("data/meta_data.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
    ad_spend, adset_spend, campaign_spend = spend_totals(meta)
    campaign_roas = build_roas_rows(campaign_revenue, campaign_spend)
    adset_roas = build_roas_rows(adset_revenue, adset_spend)
    ad_roas = build_roas_rows(ad_revenue, ad_spend)

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)
    adset_to_campaign = {j.adset.id: j.campaign.id for j in joined}
    ad_to_adset = {j.ad.id: j.adset.id for j in joined}

    campaign_posteriors = {
        cid: compute_top_level_posterior(rc) for cid, rc in campaign_rates.items()
    }
    adset_posteriors = {}
    for aid, rc in adset_rates.items():
        parent = campaign_posteriors[adset_to_campaign[aid]]
        adset_posteriors[aid] = compute_posterior(rc, parent.score if parent else 0.5, PRIOR_STRENGTH)
    ad_posteriors = {}
    for aid, rc in ad_rates.items():
        parent = adset_posteriors[ad_to_adset[aid]]
        ad_posteriors[aid] = compute_posterior(rc, parent.score if parent else 0.5, PRIOR_STRENGTH)

    total_successes = sum(rc.successes for rc in campaign_rates.values())
    total_failures = sum(rc.failures for rc in campaign_rates.values())
    total_n = total_successes + total_failures
    overall_baseline = total_successes / total_n if total_n else 0.5

    def actions_for(posteriors, get_baseline):
        result = {}
        for id_, posterior in posteriors.items():
            if posterior is None:
                continue
            action, _, _ = decide(posterior, get_baseline(id_))
            result[id_] = action
        return result

    campaign_actions = actions_for(campaign_posteriors, lambda cid: overall_baseline)
    adset_actions = actions_for(
        adset_posteriors,
        lambda aid: campaign_posteriors[adset_to_campaign[aid]].score if campaign_posteriors[adset_to_campaign[aid]] else 0.5,
    )
    ad_actions = actions_for(
        ad_posteriors,
        lambda aid: adset_posteriors[ad_to_adset[aid]].score if adset_posteriors[ad_to_adset[aid]] else 0.5,
    )

    print_findings("Campaigns", flag_findings(campaign_actions, campaign_roas))
    print()
    print_findings("Adsets", flag_findings(adset_actions, adset_roas))
    print()
    print_findings("Ads", flag_findings(ad_actions, ad_roas))