"""
Method: split by campaign start_date (see SPLIT_REASONING.md).
Splits conversations.json AND meta_data.json using the same holdout
campaign set. products.json is NOT split - it's a static catalog needed
identically on both sides.

Usage:
    python scripts/split.py --conversations data/original/conversations.json \
                             --meta data/original/meta_data.json \
                             --products data/original/products.json \
                             --out-dir ./data
"""

import argparse
import json
import shutil
from pathlib import Path

SPLIT_CONFIG = {
    "holdout_cutoff_date": "2026-04-29",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_holdout_campaign_ids(campaigns, cutoff_date):
    """Campaigns starting on/after cutoff are holdout."""
    return {c["id"] for c in campaigns if c["start_date"] >= cutoff_date}


def split_conversations(conversations, holdout_campaign_ids):
    train, holdout = [], []
    for conv in conversations:
        campaign_id = conv.get("source", {}).get("campaign_id")
        if campaign_id and campaign_id in holdout_campaign_ids:
            holdout.append(conv)
        else:
            train.append(conv)
    return train, holdout


def split_meta(meta, holdout_campaign_ids):
    train = {"campaigns": [], "adsets": [], "ads": [], "creatives": [], "insights": []}
    holdout = {"campaigns": [], "adsets": [], "ads": [], "creatives": [], "insights": []}

    for c in meta["campaigns"]:
        (holdout if c["id"] in holdout_campaign_ids else train)["campaigns"].append(c)

    for a in meta["adsets"]:
        (holdout if a["campaign_id"] in holdout_campaign_ids else train)["adsets"].append(a)

    for ad in meta["ads"]:
        (holdout if ad["campaign_id"] in holdout_campaign_ids else train)["ads"].append(ad)

    for i in meta["insights"]:
        (holdout if i["campaign_id"] in holdout_campaign_ids else train)["insights"].append(i)

    train_creative_ids = {ad["creative"]["id"] for ad in train["ads"] if ad.get("creative")}
    holdout_creative_ids = {ad["creative"]["id"] for ad in holdout["ads"] if ad.get("creative")}

    for cr in meta["creatives"]:
        if cr["id"] in train_creative_ids:
            train["creatives"].append(cr)
        if cr["id"] in holdout_creative_ids:
            holdout["creatives"].append(cr)

    return train, holdout


def summarize(label, conversations, total):
    from collections import Counter

    n = len(conversations)
    pct = n / total * 100 if total else 0.0
    platforms = Counter(c.get("source", {}).get("platform") for c in conversations)
    outcomes = Counter(c.get("outcome", {}).get("type") for c in conversations)
    print(f"\n{label}: {n} conversations ({pct:.1f}% of {total})")
    print(f"  platforms: {dict(platforms)}")
    print(f"  outcomes:  {dict(outcomes.most_common())}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversations", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    conversations = load_json(args.conversations)
    meta = load_json(args.meta)

    holdout_campaign_ids = get_holdout_campaign_ids(
        meta["campaigns"], SPLIT_CONFIG["holdout_cutoff_date"]
    )

    train_convs, holdout_convs = split_conversations(conversations, holdout_campaign_ids)
    train_meta, holdout_meta = split_meta(meta, holdout_campaign_ids)

    out_dir = Path(args.out_dir)
    train_dir = out_dir / "train"
    holdout_dir = out_dir / "holdout"
    train_dir.mkdir(parents=True, exist_ok=True)
    holdout_dir.mkdir(parents=True, exist_ok=True)

    with open(train_dir / "train.json", "w", encoding="utf-8") as f:
        json.dump(train_convs, f, ensure_ascii=False, indent=2)
    with open(holdout_dir / "holdout.json", "w", encoding="utf-8") as f:
        json.dump(holdout_convs, f, ensure_ascii=False, indent=2)

    with open(train_dir / "meta_train.json", "w", encoding="utf-8") as f:
        json.dump(train_meta, f, ensure_ascii=False, indent=2)
    with open(holdout_dir / "meta_holdout.json", "w", encoding="utf-8") as f:
        json.dump(holdout_meta, f, ensure_ascii=False, indent=2)

    shutil.copy(args.products, train_dir / "products.json")
    shutil.copy(args.products, holdout_dir / "products.json")

    total = len(conversations)
    print(f"Holdout campaigns (start_date >= {SPLIT_CONFIG['holdout_cutoff_date']}):")
    for c in meta["campaigns"]:
        if c["id"] in holdout_campaign_ids:
            print(f"  {c['start_date']}  {c['name']}")

    summarize("TRAIN", train_convs, total)
    summarize("HOLDOUT", holdout_convs, total)

    print(
        f"\nmeta train:   campaigns={len(train_meta['campaigns'])} "
        f"adsets={len(train_meta['adsets'])} ads={len(train_meta['ads'])} "
        f"creatives={len(train_meta['creatives'])} insights={len(train_meta['insights'])}"
    )
    print(
        f"meta holdout: campaigns={len(holdout_meta['campaigns'])} "
        f"adsets={len(holdout_meta['adsets'])} ads={len(holdout_meta['ads'])} "
        f"creatives={len(holdout_meta['creatives'])} insights={len(holdout_meta['insights'])}"
    )

    print(f"\nWrote {train_dir} and {holdout_dir}")


if __name__ == "__main__":
    main()
