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
from ..scoring.corrector import PRIOR_STRENGTH


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


def roas_details_by_id(revenue_by_id: dict, spend_by_id: dict, prior_strength: float = PRIOR_STRENGTH) -> dict[str, dict]:
    """shrunk_roas_by_id() with its working per id: raw ROAS, the portfolio
    mean it is pulled toward, the pull strength, and the result."""
    raw = {}
    for id_, rev in revenue_by_id.items():
        spend = spend_by_id.get(id_)
        raw[id_] = rev.total_revenue / spend if spend else None
    known = [v for v in raw.values() if v is not None]
    prior_mean = sum(known) / len(known) if known else 0.0
    shrunk = shrunk_roas_by_id(revenue_by_id, spend_by_id, prior_strength)
    return {
        id_: {"revenue": rev.total_revenue, "sales": rev.sales, "resolved_conversations": rev.n,
              "spend": spend_by_id.get(id_),
              "raw_roas": raw[id_], "portfolio_mean_roas": prior_mean,
              "shrinkage_strength": prior_strength, "shrunk_roas": shrunk[id_]}
        for id_, rev in revenue_by_id.items()
    }


def shrunk_roas_by_id(revenue_by_id: dict, spend_by_id: dict, prior_strength: float = PRIOR_STRENGTH) -> dict[str, float]:
    # Bayesian average (same shrinkage idea as corrector.py's PRIOR_STRENGTH):
    # regularizes small-sample ROAS toward the portfolio-wide mean so a
    # handful of orders can't swing the signal to an extreme value.
    raw = {}
    for id_, rev in revenue_by_id.items():
        spend = spend_by_id.get(id_)
        raw[id_] = rev.total_revenue / spend if spend else None

    known = [v for v in raw.values() if v is not None]
    prior_mean = sum(known) / len(known) if known else 0.0

    return {
        id_: (
            prior_mean if raw[id_] is None
            else (prior_strength * prior_mean + rev.n * raw[id_]) / (prior_strength + rev.n)
        )
        for id_, rev in revenue_by_id.items()
    }


def baseline_cpa_details(revenue_by_id: dict, spend_by_id: dict) -> dict:
    """The level's typical cost per SALE: all spend over all real sales.
    (It used to divide by resolved conversations, which counted ghosted and
    cancelled chats as if they were orders.)"""
    total_spend = sum(spend_by_id.get(id_, 0.0) for id_ in revenue_by_id)
    total_sales = sum(rev.sales for rev in revenue_by_id.values())
    return {"total_spend": total_spend, "total_sales": total_sales,
            "baseline_cost_per_sale": total_spend / total_sales if total_sales else None}


def baseline_cpa(revenue_by_id: dict, spend_by_id: dict) -> float | None:
    return baseline_cpa_details(revenue_by_id, spend_by_id)["baseline_cost_per_sale"]


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
    convs = load_conversations("data/train/conv_train.json")
    meta = load_meta("data/train/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)
    ad_spend, adset_spend, campaign_spend = spend_totals(meta)

    print_roas("Campaigns", build_roas_rows(campaign_revenue, campaign_spend))
    print()
    print_roas("Adsets", build_roas_rows(adset_revenue, adset_spend))
    print()
    print_roas("Ads", build_roas_rows(ad_revenue, ad_spend))