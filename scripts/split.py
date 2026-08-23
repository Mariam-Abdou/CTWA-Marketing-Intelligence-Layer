"""
Method: split by campaign start_date.

Usage:
    python split.py --conversations data/conversations.json \
                    --meta data/meta_data.json \
                    --out-dir ./data
"""

import argparse
import json
from pathlib import Path

SPLIT_CONFIG = {
    "holdout_cutoff_date": "2026-04-29",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_holdout_campaign_ids(campaigns, cutoff_date):
    """Campaigns starting on/after cutoff are holdout."""
    return {
        c["id"]
        for c in campaigns
        if c["start_date"] >= cutoff_date
    }


def split_conversations(conversations, holdout_campaign_ids):
    train, holdout = [], []

    for conv in conversations:
        campaign_id = conv.get("source", {}).get("campaign_id")

        if campaign_id and campaign_id in holdout_campaign_ids:
            holdout.append(conv)
        else:
            train.append(conv)

    return train, holdout


def summarize(label, conversations, total):
    from collections import Counter

    n = len(conversations)
    pct = n / total * 100
    platforms = Counter(c.get("source", {}).get("platform") for c in conversations)
    outcomes = Counter(c.get("outcome", {}).get("type") for c in conversations)
    print(f"\n{label}: {n} conversations ({pct:.1f}% of {total})")
    print(f"  platforms: {dict(platforms)}")
    print(f"  outcomes:  {dict(outcomes.most_common())}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversations", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    config = SPLIT_CONFIG
    conversations = load_json(args.conversations)
    meta = load_json(args.meta)

    holdout_campaign_ids = get_holdout_campaign_ids(
        meta["campaigns"],
        config["holdout_cutoff_date"],
    )

    train, holdout = split_conversations(
        conversations,
        holdout_campaign_ids,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "train.json", "w", encoding="utf-8") as f:
        json.dump(train, f, ensure_ascii=False, indent=2)

    with open(out_dir / "holdout.json", "w", encoding="utf-8") as f:
        json.dump(holdout, f, ensure_ascii=False, indent=2)

    total = len(conversations)
    print(f"Holdout campaigns (start_date >= {config['holdout_cutoff_date']}):")
    for c in meta["campaigns"]:
        if c["id"] in holdout_campaign_ids:
            print(f"  {c['start_date']}  {c['name']}")

    summarize("TRAIN", train, total)
    summarize("HOLDOUT", holdout, total)

    print(f"\nWrote {out_dir / 'train.json'} and {out_dir / 'holdout.json'}")


if __name__ == "__main__":
    main()
