from collections import defaultdict
from dataclasses import dataclass

from src.ingestion.conversation_loader import load_conversations
from src.ingestion.meta_loader import load_meta
from src.ingestion.joiner import join_conversations_to_meta


@dataclass
class RevenueSummary:
    total_revenue: float
    avg_revenue: float | None
    n: int


def conversation_revenue(conversation) -> float | None:
    outcome_type = conversation.outcome.type
    raw = conversation.outcome.raw

    if outcome_type == "delivered":
        return raw.get("total", 0)
    if outcome_type == "refunded":
        total = raw.get("total", 0)
        refunded = raw.get("refunded_amount", 0)
        return total - refunded
    if outcome_type in ("ghosted", "cancelled"):
        return 0.0
    # stuck_pending, active, adversarial -> excluded, same as classifier
    return None


def summarize_revenue(conversations) -> RevenueSummary:
    amounts = [r for c in conversations if (r := conversation_revenue(c)) is not None]
    total_revenue = sum(amounts)
    n = len(amounts)
    avg_revenue = total_revenue / n if n else None
    return RevenueSummary(total_revenue=total_revenue, avg_revenue=avg_revenue, n=n)


def revenue_totals(joined) -> tuple[dict, dict, dict]:
    by_ad, by_adset, by_campaign = defaultdict(list), defaultdict(list), defaultdict(list)

    for jc in joined:
        by_ad[jc.ad.id].append(jc.conversation)
        by_adset[jc.adset.id].append(jc.conversation)
        by_campaign[jc.campaign.id].append(jc.conversation)

    ad_revenue = {ad_id: summarize_revenue(convs) for ad_id, convs in by_ad.items()}
    adset_revenue = {adset_id: summarize_revenue(convs) for adset_id, convs in by_adset.items()}
    campaign_revenue = {c_id: summarize_revenue(convs) for c_id, convs in by_campaign.items()}

    return ad_revenue, adset_revenue, campaign_revenue


def spend_totals(meta) -> tuple[dict, dict, dict]:
    ad_spend, adset_spend, campaign_spend = defaultdict(float), defaultdict(float), defaultdict(float)

    for insight in meta.insights:
        if insight.spend:
            ad_spend[insight.ad_id] += insight.spend
            adset_spend[insight.adset_id] += insight.spend
            campaign_spend[insight.campaign_id] += insight.spend

    return ad_spend, adset_spend, campaign_spend


def build_audit_rows(revenue_by_id: dict, spend_by_id: dict) -> list[dict]:
    rows = []
    for id_, rev in revenue_by_id.items():
        spend = spend_by_id.get(id_)
        roas = rev.total_revenue / spend if spend else None
        rows.append({
            "id": id_,
            "total_revenue": rev.total_revenue,
            "avg_revenue": rev.avg_revenue,
            "n": rev.n,
            "spend": spend,
            "roas": roas,
        })
    rows.sort(key=lambda r: -(r["roas"] or 0))
    return rows


def print_audit(label, rows):
    print(f"--- {label} ---")
    for r in rows:
        avg = f"{r['avg_revenue']:.2f}" if r["avg_revenue"] is not None else "n/a"
        spend = f"{r['spend']:.2f}" if r["spend"] is not None else "n/a"
        roas = f"{r['roas']:.2f}" if r["roas"] is not None else "n/a"
        print(
            f"{r['id']:<24} revenue={r['total_revenue']:>10.2f}  avg={avg:<8} n={r['n']:<4} "
            f"spend={spend:<10} roas={roas}"
        )


if __name__ == "__main__":
    convs = load_conversations("data/train.json")
    meta = load_meta("data/meta_data.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
    ad_spend, adset_spend, campaign_spend = spend_totals(meta)

    print_audit("Campaigns", build_audit_rows(campaign_revenue, campaign_spend))
    print()
    print_audit("Adsets", build_audit_rows(adset_revenue, adset_spend))
    print()
    print_audit("Ads", build_audit_rows(ad_revenue, ad_spend))