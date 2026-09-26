"""
Shared date-cutoff split logic, used by BOTH split.py (train/holdout, from the
original data) and split_validation.py (inner_train/validation, carved out of
train only). One method, one implementation -- see SPLIT_REASONING.md.

Method: any campaign whose start_date falls on/after a cutoff date goes to
the "held" side; everything else (including organic/direct, which carry no
campaign_id at all and can never be scored) goes to the "kept" side.
campaign_type values in `excluded_types` are always kept regardless of their
start_date (e.g. "always_on" runs the whole project timeline, so ordering it
by start_date would misplace it).

Standalone-friendly: no src.* imports, so this works whether or not the
package is on sys.path.
"""

import json
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_split_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["split"]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def campaigns_on_or_after(campaigns, cutoff_date: str, excluded_types: list[str]) -> set[str]:
    """Campaign ids to hold back: start_date >= cutoff, UNLESS their
    campaign_type is in excluded_types (those always stay on the "kept" side,
    whatever their start_date)."""
    return {
        c["id"] for c in campaigns
        if c["start_date"] >= cutoff_date and c.get("campaign_type") not in excluded_types
    }


def split_conversations(conversations, held_campaign_ids: set[str]):
    kept, held = [], []
    for conv in conversations:
        campaign_id = conv.get("source", {}).get("campaign_id")
        if campaign_id and campaign_id in held_campaign_ids:
            held.append(conv)
        else:
            kept.append(conv)
    return kept, held


def split_meta(meta, held_campaign_ids: set[str]):
    kept = {"campaigns": [], "adsets": [], "ads": [], "creatives": [], "insights": []}
    held = {"campaigns": [], "adsets": [], "ads": [], "creatives": [], "insights": []}

    for c in meta["campaigns"]:
        (held if c["id"] in held_campaign_ids else kept)["campaigns"].append(c)

    for a in meta["adsets"]:
        (held if a["campaign_id"] in held_campaign_ids else kept)["adsets"].append(a)

    for ad in meta["ads"]:
        (held if ad["campaign_id"] in held_campaign_ids else kept)["ads"].append(ad)

    for i in meta["insights"]:
        (held if i["campaign_id"] in held_campaign_ids else kept)["insights"].append(i)

    kept_creative_ids = {ad["creative"]["id"] for ad in kept["ads"] if ad.get("creative")}
    held_creative_ids = {ad["creative"]["id"] for ad in held["ads"] if ad.get("creative")}

    for cr in meta["creatives"]:
        if cr["id"] in kept_creative_ids:
            kept["creatives"].append(cr)
        if cr["id"] in held_creative_ids:
            held["creatives"].append(cr)

    return kept, held


def customer_overlap(kept_conversations, held_conversations) -> tuple[int, int, int]:
    """(customers only in `kept`, only in `held`, in BOTH -- the leakage count
    the brief's split rule exists to catch: repeat customers on both sides)."""
    kept_ids = {c.get("customer", {}).get("id") for c in kept_conversations}
    held_ids = {c.get("customer", {}).get("id") for c in held_conversations}
    both = kept_ids & held_ids
    return len(kept_ids - both), len(held_ids - both), len(both)


def summarize(label, conversations, total):
    from collections import Counter

    n = len(conversations)
    pct = n / total * 100 if total else 0.0
    platforms = Counter(c.get("source", {}).get("platform") for c in conversations)
    outcomes = Counter(c.get("outcome", {}).get("type") for c in conversations)
    print(f"\n{label}: {n} conversations ({pct:.1f}% of {total})")
    print(f"  platforms: {dict(platforms)}")
    print(f"  outcomes:  {dict(outcomes.most_common())}")
