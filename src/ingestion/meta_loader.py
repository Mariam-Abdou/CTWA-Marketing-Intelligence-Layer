from dataclasses import dataclass
from pathlib import Path

from src.ingestion.io_utils import load_json, validate_enum

KNOWN_OPTIMIZATION_GOALS = {"CONVERSATIONS", "REACH"}


@dataclass
class Campaign:
    id: str
    name: str
    objective: str
    campaign_type: str
    start_date: str
    end_date: str | None
    status: str


@dataclass
class Adset:
    id: str
    name: str
    campaign_id: str
    audience_type: str
    optimization_goal: str
    daily_budget: float | None
    status: str


@dataclass
class Ad:
    id: str
    name: str
    adset_id: str
    campaign_id: str
    creative_id: str | None
    start_date: str
    end_date: str | None
    status: str


@dataclass
class Creative:
    id: str
    name: str
    theme: str | None
    angle: str | None
    status: str


@dataclass
class DailyInsight:
    ad_id: str
    adset_id: str
    campaign_id: str
    date_start: str
    impressions: int | None
    spend: float | None
    frequency: float | None
    ctr: float | None
    actions: list


@dataclass
class MetaData:
    campaigns: list[Campaign]
    adsets: list[Adset]
    ads: list[Ad]
    creatives: list[Creative]
    insights: list[DailyInsight]


def _parse_campaign(r: dict) -> Campaign:
    return Campaign(
        id=r["id"], name=r.get("name"), objective=r.get("objective"),
        campaign_type=r.get("campaign_type"), start_date=r.get("start_date"),
        end_date=r.get("end_date"), status=r.get("status"),
    )


def _parse_adset(r: dict) -> Adset:
    validate_enum(r.get("optimization_goal"), KNOWN_OPTIMIZATION_GOALS, "optimization_goal", r["id"])
    budget = r.get("daily_budget")
    return Adset(
        id=r["id"], name=r.get("name"), campaign_id=r.get("campaign_id"),
        audience_type=r.get("audience_type"), optimization_goal=r["optimization_goal"],
        daily_budget=float(budget) if budget is not None else None,
        status=r.get("status"),
    )


def _parse_ad(r: dict) -> Ad:
    creative = r.get("creative") or {}
    return Ad(
        id=r["id"], name=r.get("name"), adset_id=r.get("adset_id"),
        campaign_id=r.get("campaign_id"), creative_id=creative.get("id"),
        start_date=r.get("start_date"), end_date=r.get("end_date"),
        status=r.get("status"),
    )


def _parse_creative(r: dict) -> Creative:
    return Creative(
        id=r["id"], name=r.get("name"), theme=r.get("theme"),
        angle=r.get("angle"), status=r.get("status"),
    )


def _parse_insight(r: dict) -> DailyInsight:
    return DailyInsight(
        ad_id=r.get("ad_id"), adset_id=r.get("adset_id"), campaign_id=r.get("campaign_id"),
        date_start=r.get("date_start"),
        impressions=int(r["impressions"]) if r.get("impressions") is not None else None,
        spend=float(r["spend"]) if r.get("spend") is not None else None,
        frequency=float(r["frequency"]) if r.get("frequency") is not None else None,
        ctr=float(r["ctr"]) if r.get("ctr") is not None else None,
        actions=r.get("actions", []),
    )


def load_meta(path: str | Path) -> MetaData:
    raw = load_json(path)
    return MetaData(
        campaigns=[_parse_campaign(r) for r in raw["campaigns"]],
        adsets=[_parse_adset(r) for r in raw["adsets"]],
        ads=[_parse_ad(r) for r in raw["ads"]],
        creatives=[_parse_creative(r) for r in raw["creatives"]],
        insights=[_parse_insight(r) for r in raw["insights"]],
    )


def insights_by_ad(meta: MetaData) -> dict[str, list[DailyInsight]]:
    grouped: dict[str, list[DailyInsight]] = {}
    for insight in meta.insights:
        grouped.setdefault(insight.ad_id, []).append(insight)

    for ad_id in grouped:
        grouped[ad_id].sort(key=lambda i: i.date_start)

    return grouped


def _combine_daily(
    insight_lists: list[list[DailyInsight]],
    *,
    ad_id: str = "",
    adset_id: str = "",
    campaign_id: str = "",
) -> list[DailyInsight]:
    by_date: dict[str, list[DailyInsight]] = {}
    for lst in insight_lists:
        for i in lst:
            by_date.setdefault(i.date_start, []).append(i)

    combined = []
    for date in sorted(by_date):
        day = by_date[date]

        imp_values = [i.impressions for i in day if i.impressions is not None]
        total_impressions = sum(imp_values) if imp_values else None

        spend_values = [i.spend for i in day if i.spend is not None]
        total_spend = sum(spend_values) if spend_values else None

        freq_weight = sum(i.impressions or 0 for i in day if i.frequency is not None)
        avg_frequency = (
            sum((i.frequency or 0) * (i.impressions or 0) for i in day if i.frequency is not None) / freq_weight
            if freq_weight else None
        )

        ctr_weight = sum(i.impressions or 0 for i in day if i.ctr is not None)
        avg_ctr = (
            sum((i.ctr or 0) * (i.impressions or 0) for i in day if i.ctr is not None) / ctr_weight
            if ctr_weight else None
        )

        combined.append(DailyInsight(
            ad_id=ad_id, adset_id=adset_id, campaign_id=campaign_id, date_start=date,
            impressions=total_impressions, spend=total_spend,
            frequency=avg_frequency, ctr=avg_ctr, actions=[],
        ))

    return combined


def insights_by_adset(meta: MetaData) -> dict[str, list[DailyInsight]]:
    """Impression-weighted rollup of each adset's ads, day by day."""
    by_ad = insights_by_ad(meta)
    ad_to_adset = {ad.id: ad.adset_id for ad in meta.ads}
    adset_to_campaign = {a.id: a.campaign_id for a in meta.adsets}

    grouped: dict[str, list[list[DailyInsight]]] = {}
    for ad_id, insights in by_ad.items():
        adset_id = ad_to_adset.get(ad_id)
        if adset_id is None:
            continue
        grouped.setdefault(adset_id, []).append(insights)

    return {
        adset_id: _combine_daily(
            lists, adset_id=adset_id, campaign_id=adset_to_campaign.get(adset_id, "")
        )
        for adset_id, lists in grouped.items()
    }


def insights_by_campaign(meta: MetaData) -> dict[str, list[DailyInsight]]:
    """Impression-weighted rollup of each campaign's adsets, day by day."""
    by_adset = insights_by_adset(meta)
    adset_to_campaign = {a.id: a.campaign_id for a in meta.adsets}

    grouped: dict[str, list[list[DailyInsight]]] = {}
    for adset_id, insights in by_adset.items():
        campaign_id = adset_to_campaign.get(adset_id)
        if campaign_id is None:
            continue
        grouped.setdefault(campaign_id, []).append(insights)

    return {
        campaign_id: _combine_daily(lists, campaign_id=campaign_id)
        for campaign_id, lists in grouped.items()
    }


if __name__ == "__main__":
    meta = load_meta("data/train/meta_train.json")
    print(f"Campaigns: {len(meta.campaigns)}, Adsets: {len(meta.adsets)}, "
          f"Ads: {len(meta.ads)}, Creatives: {len(meta.creatives)}, Insights: {len(meta.insights)}")
    from collections import Counter
    print("Optimization goals:", Counter(a.optimization_goal for a in meta.adsets))

def typical_campaign_days(meta: "MetaData") -> float:
    from datetime import date

    lengths = []
    for c in meta.campaigns:
        if c.campaign_type == "always_on" or not c.end_date:
            continue
        try:
            start = date.fromisoformat(c.start_date[:10])
            end = date.fromisoformat(c.end_date[:10])
        except (ValueError, TypeError):
            continue
        days = (end - start).days
        if days > 0:
            lengths.append(days)

    if not lengths:
        return 30.0
    lengths.sort()
    mid = len(lengths) // 2
    return float(lengths[mid] if len(lengths) % 2 else (lengths[mid - 1] + lengths[mid]) / 2)
