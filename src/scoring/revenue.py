from dataclasses import dataclass

from .amounts import get_outcome_amounts
from ..ingestion.joiner import group_by_level


@dataclass
class RevenueSummary:
    total_revenue: float
    avg_revenue: float | None
    n: int


def conversation_revenue(conversation) -> float | None:
    return get_outcome_amounts(conversation).net


def summarize_revenue(conversations) -> RevenueSummary:
    amounts = [r for c in conversations if (r := conversation_revenue(c)) is not None]
    total_revenue = sum(amounts)
    n = len(amounts)
    avg_revenue = total_revenue / n if n else None
    return RevenueSummary(total_revenue=total_revenue, avg_revenue=avg_revenue, n=n)


def revenue_totals(joined) -> tuple[dict, dict, dict]:
    by_ad, by_adset, by_campaign = group_by_level(joined)

    ad_revenue = {ad_id: summarize_revenue(convs) for ad_id, convs in by_ad.items()}
    adset_revenue = {adset_id: summarize_revenue(convs) for adset_id, convs in by_adset.items()}
    campaign_revenue = {c_id: summarize_revenue(convs) for c_id, convs in by_campaign.items()}

    return ad_revenue, adset_revenue, campaign_revenue


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta
    from ..ingestion.joiner import join_conversations_to_meta

    convs = load_conversations("data/train/conv_train.json")
    meta = load_meta("data/train/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_revenue, adset_revenue, campaign_revenue = revenue_totals(joined)

    print(f"Ads: {len(ad_revenue)}, Adsets: {len(adset_revenue)}, Campaigns: {len(campaign_revenue)}")
    for label, rev_dict in [("Campaigns", campaign_revenue), ("Adsets", adset_revenue), ("Ads", ad_revenue)]:
        print(f"--- {label} ---")
        for id_, rev in rev_dict.items():
            avg = f"{rev.avg_revenue:.2f}" if rev.avg_revenue is not None else "n/a"
            print(f"{id_:<24} total={rev.total_revenue:>10.2f}  avg={avg:<8} n={rev.n}")