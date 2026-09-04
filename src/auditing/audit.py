"""
Compares real revenue (src.scoring.revenue, derived from WhatsApp outcomes)
against real spend (meta_data.json) to produce ROAS per level.

Validation/audit layer only - does NOT feed back into the score or the
scale/hold/kill decision.
"""

from collections import defaultdict

from ..ingestion.conversation_loader import load_conversations
from ..ingestion.meta_loader import load_meta
from ..ingestion.joiner import join_conversations_to_meta
from ..scoring.revenue import revenue_totals


def spend_totals(meta) -> tuple[dict, dict, dict]:
    ad_spend, adset_spend, campaign_spend = defaultdict(float), defaultdict(float), defaultdict(float)

    for insight in meta.insights:
        if insight.spend:
            ad_spend[insight.ad_id] += insight.spend
            adset_spend[insight.adset_id] += insight.spend
            campaign_spend[insight.campaign_id] += insight.spend

    return ad_spend, adset_spend, campaign_spend


def build_roas_rows(revenue_by_id: dict, spend_by_id: dict) -> list[dict]:
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


def print_roas(label, rows):
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

    print_roas("Campaigns", build_roas_rows(campaign_revenue, campaign_spend))
    print()
    print_roas("Adsets", build_roas_rows(adset_revenue, adset_spend))
    print()
    print_roas("Ads", build_roas_rows(ad_revenue, ad_spend))