from dataclasses import dataclass

from conversation_loader import Conversation, load_conversations
from meta_loader import Ad, Adset, Campaign, MetaData, load_meta


@dataclass
class JoinedConversation:
    conversation: Conversation
    ad: Ad
    adset: Adset
    campaign: Campaign


@dataclass
class JoinResult:
    scoreable: list[JoinedConversation]
    organic_or_direct: list[Conversation]
    unmatched_ctwa: list[Conversation]


def join_conversations_to_meta(conversations: list[Conversation], meta: MetaData) -> JoinResult:
    ads_by_id = {ad.id: ad for ad in meta.ads}
    adsets_by_id = {a.id: a for a in meta.adsets}
    campaigns_by_id = {c.id: c for c in meta.campaigns}

    scoreable, organic_or_direct, unmatched_ctwa = [], [], []

    for conv in conversations:
        if conv.source.platform in ("organic", "direct"):
            organic_or_direct.append(conv)
            continue

        ad = ads_by_id.get(conv.source.ad_id)
        adset = adsets_by_id.get(ad.adset_id) if ad else None
        campaign = campaigns_by_id.get(ad.campaign_id) if ad else None

        if ad is None or adset is None or campaign is None:
            unmatched_ctwa.append(conv)
            continue

        scoreable.append(JoinedConversation(conversation=conv, ad=ad, adset=adset, campaign=campaign))

    return JoinResult(scoreable=scoreable, organic_or_direct=organic_or_direct, unmatched_ctwa=unmatched_ctwa)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--conversations", default="data/train.json")
    parser.add_argument("--meta", default="data/meta_data.json")
    args = parser.parse_args()

    convs = load_conversations(args.conversations)
    meta = load_meta(args.meta)
    result = join_conversations_to_meta(convs, meta)

    print(f"Scoreable: {len(result.scoreable)}")
    print(f"Organic/direct: {len(result.organic_or_direct)}")
    print(f"Unmatched CTWA: {len(result.unmatched_ctwa)}")