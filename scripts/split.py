"""
Train/holdout split, from the ORIGINAL data. Method: split by campaign
start_date (see SPLIT_REASONING.md and split_lib.py). products.json is NOT
split - it's a static catalog needed identically on both sides.

For carving a validation slice OUT OF train (to tune thresholds without ever
touching holdout), see split_validation.py -- same method, different cutoff.

Usage:
    python scripts/split.py --conversations data/original/conversations.json \
                             --meta data/original/meta_data.json \
                             --products data/original/products.json \
                             --out-dir ./data
"""

import argparse
import shutil
from pathlib import Path

from split_lib import (
    campaigns_on_or_after,
    load_json,
    load_split_config,
    split_conversations,
    split_meta,
    summarize,
    write_json,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversations", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    conversations = load_json(args.conversations)
    meta = load_json(args.meta)

    split_config = load_split_config()
    holdout_campaign_ids = campaigns_on_or_after(
        meta["campaigns"], split_config["holdout_cutoff_date"], split_config["excluded_campaign_types"]
    )

    train_convs, holdout_convs = split_conversations(conversations, holdout_campaign_ids)
    train_meta, holdout_meta = split_meta(meta, holdout_campaign_ids)

    out_dir = Path(args.out_dir)
    train_dir = out_dir / "train"
    holdout_dir = out_dir / "holdout"

    write_json(train_dir / "train.json", train_convs)
    write_json(holdout_dir / "holdout.json", holdout_convs)
    write_json(train_dir / "meta_train.json", train_meta)
    write_json(holdout_dir / "meta_holdout.json", holdout_meta)

    shutil.copy(args.products, train_dir / "products.json")
    shutil.copy(args.products, holdout_dir / "products.json")

    total = len(conversations)
    print(f"Holdout campaigns (start_date >= {split_config['holdout_cutoff_date']}):")
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
