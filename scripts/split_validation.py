"""
Carves a VALIDATION slice out of TRAIN -- never touches holdout. Same method
as split.py (date cutoff on campaign start_date; see split_lib.py),
applied a second time, one level in.

Why this needs to exist at all: PROBABILITY_THRESHOLD (and anything else
that needs tuning) has to be tuned SOMEWHERE. Tuning it on holdout would
invalidate the final evaluation ("holdout is sacred -- touch it once, at the
very end"). This gives that somewhere.

Reads the FULL train set written by split.py and writes the tuning slices next
to it. Nothing is overwritten or deleted, so split.py and this script can be
re-run in order, any number of times, from the original data:
    data/train/full_train.json, meta_full_train.json   <- input (split.py)
    data/train/conv_train.json, meta_train.json        <- tuning fit set
    data/train/conv_val.json,   meta_val.json          <- tuning check set
The merchant-facing pipeline runs on full_train; conv_train is for tuning only.

Usage (from the repo root):
    python3 -m scripts.split_validation
"""

import argparse
from pathlib import Path

from scripts.split_lib import (
    campaigns_on_or_after,
    customer_overlap,
    load_json,
    load_split_config,
    split_conversations,
    split_meta,
    summarize,
    write_json,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-dir", default="data/train", help="Directory holding full_train.json + meta_full_train.json")
    parser.add_argument("--out-dir", default="data/train", help="Where to write conv_train/meta_train/conv_val/meta_val")
    args = parser.parse_args()

    train_dir = Path(args.train_dir)
    train_json_path = train_dir / "full_train.json"
    if not train_json_path.exists():
        raise SystemExit(
            f"{train_json_path} not found. Run `python3 -m scripts.split` first."
        )

    conversations = load_json(train_json_path)
    meta = load_json(train_dir / "meta_full_train.json")

    split_config = load_split_config()
    validation_campaign_ids = campaigns_on_or_after(
        meta["campaigns"], split_config["validation_cutoff_date"], split_config["excluded_campaign_types"]
    )

    conv_train, conv_val = split_conversations(conversations, validation_campaign_ids)
    meta_train, meta_val = split_meta(meta, validation_campaign_ids)

    out_dir = Path(args.out_dir)
    write_json(out_dir / "conv_train.json", conv_train)
    write_json(out_dir / "meta_train.json", meta_train)
    write_json(out_dir / "conv_val.json", conv_val)
    write_json(out_dir / "meta_val.json", meta_val)

    total = len(conversations)
    print(f"Validation campaigns (start_date >= {split_config['validation_cutoff_date']}, within train):")
    for c in meta["campaigns"]:
        if c["id"] in validation_campaign_ids:
            print(f"  {c['start_date']}  {c['objective']:<18} {c['name']}")

    summarize("CONV_TRAIN (tuning fit set)", conv_train, total)
    summarize("CONV_VAL (tuning check set)", conv_val, total)

    only_train, only_val, both = customer_overlap(conv_train, conv_val)
    total_customers = only_train + only_val + both
    overlap_convs = sum(
        1 for c in conv_val
        if c.get("customer", {}).get("id") in {cc.get("customer", {}).get("id") for cc in conv_train}
    )
    print(
        f"\nCustomer overlap: {both}/{total_customers} customers appear in BOTH "
        f"conv_train and conv_val ({both / total_customers * 100:.1f}% of customers touched). "
        f"{overlap_convs}/{len(conv_val)} conv_val conversations belong to a customer already "
        f"seen in conv_train.\n"
        f"This is a REAL overlap, same shape as the train/holdout one -- "
        f"it means threshold-tuning here is not customer-disjoint. Report whatever gets tuned on "
        f"conv_val both including and excluding those overlapping customers' conversations, same as "
        f"you'll do for holdout."
    )

    print(
        f"\nmeta_train (conv_train's meta): campaigns={len(meta_train['campaigns'])} "
        f"adsets={len(meta_train['adsets'])} ads={len(meta_train['ads'])} "
        f"creatives={len(meta_train['creatives'])} insights={len(meta_train['insights'])}"
    )
    print(
        f"meta_val   (conv_val's meta):   campaigns={len(meta_val['campaigns'])} "
        f"adsets={len(meta_val['adsets'])} ads={len(meta_val['ads'])} "
        f"creatives={len(meta_val['creatives'])} insights={len(meta_val['insights'])}"
    )

    print(f"\nWrote conv_train.json / meta_train.json / conv_val.json / meta_val.json to {out_dir}")


if __name__ == "__main__":
    main()
