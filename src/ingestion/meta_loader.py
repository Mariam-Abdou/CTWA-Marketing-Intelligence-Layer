from dataclasses import dataclass
from pathlib import Path

from io_utils import load_json, validate_enum

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


if __name__ == "__main__":
    meta = load_meta("data/meta_data.json")
    print(f"Campaigns: {len(meta.campaigns)}, Adsets: {len(meta.adsets)}, "
          f"Ads: {len(meta.ads)}, Creatives: {len(meta.creatives)}, Insights: {len(meta.insights)}")
    from collections import Counter
    print("Optimization goals:", Counter(a.optimization_goal for a in meta.adsets))