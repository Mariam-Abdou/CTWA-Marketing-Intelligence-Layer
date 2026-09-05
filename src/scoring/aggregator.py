from collections import defaultdict
from dataclasses import dataclass

from .classifier import classify
from ..ingestion.joiner import JoinedConversation


@dataclass
class RawCounts:
    successes: int
    failures: int
    n: int
    raw_rate: float | None


def _summarize(conversations) -> RawCounts:
    results = [r for c in conversations if (r := classify(c)).success is not None]
    successes = sum(1 for r in results if r.success)
    failures = len(results) - successes
    n = len(results)
    return RawCounts(successes=successes, failures=failures, n=n, raw_rate=successes / n if n else None)


def raw_rates(joined: list[JoinedConversation]) -> tuple[dict[str, RawCounts], dict[str, RawCounts], dict[str, RawCounts]]:
    by_ad = defaultdict(list)
    by_adset = defaultdict(list)
    by_campaign = defaultdict(list)

    for jc in joined:
        by_ad[jc.ad.id].append(jc.conversation)
        by_adset[jc.adset.id].append(jc.conversation)
        by_campaign[jc.campaign.id].append(jc.conversation)

    ad_rates = {ad_id: _summarize(convs) for ad_id, convs in by_ad.items()}
    adset_rates = {adset_id: _summarize(convs) for adset_id, convs in by_adset.items()}
    campaign_rates = {c_id: _summarize(convs) for c_id, convs in by_campaign.items()}

    return ad_rates, adset_rates, campaign_rates


if __name__ == "__main__":
    from ..ingestion.conversation_loader import load_conversations
    from ..ingestion.meta_loader import load_meta
    from ..ingestion.joiner import join_conversations_to_meta

    convs = load_conversations("data/train/train.json")
    meta = load_meta("data/train/meta_train.json")
    joined = join_conversations_to_meta(convs, meta).scoreable

    ad_rates, adset_rates, campaign_rates = raw_rates(joined)

    print(f"Ads: {len(ad_rates)}, Adsets: {len(adset_rates)}, Campaigns: {len(campaign_rates)}")
    print("-"*50)

    sample_ad_id = sorted(ad_rates.keys())[0]
    jc = next(j for j in joined if j.ad.id == sample_ad_id)
    adset_id = jc.adset.id
    campaign_id = jc.campaign.id

    print(f"Campaign {campaign_id}: {campaign_rates[campaign_id]}")
    print(f"   Adset {adset_id}: {adset_rates[adset_id]}")
    print(f"      Ad {sample_ad_id}: {ad_rates[sample_ad_id]}")