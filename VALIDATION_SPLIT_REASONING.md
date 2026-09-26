# Validation Split — Reasoning

**Why this exists at all:** `PROBABILITY_THRESHOLD` (and any other threshold that
needs tuning) has to be tuned *somewhere*. Tuning it on holdout would invalidate
the final evaluation — "holdout is sacred, touch it once, at the very end." This
carves a legal place to tune: a slice taken OUT OF `train` only, never touching
`data/holdout/`.

**Method:** identical to the train/holdout split (see `SPLIT_REASONING.md`) —
campaign `start_date` cutoff, applied a second time, one level in.

**File layout (this is a ONE-WAY transform of `data/train/` -- see
`scripts/split_validation.py`'s docstring):**
1. `python scripts/split.py ...` writes the FULL train: `data/train/train.json`
   + `meta_train.json` (605 conversations).
2. `python scripts/split_validation.py` reads those and REPLACES them with:
   - `conv_train.json` / `meta_train.json` -- the ~82% slice the pipeline
     actually trains/scores on during tuning (`meta_train.json` is overwritten;
     it's conv_train's meta now, not the full train's).
   - `conv_val.json` / `meta_val.json` -- the ~18% slice used to tune/check
     against. `train.json` is deleted in this step.

Because of step 2, **the production pipeline currently runs on conv_train
(495 conversations), not the full 605** -- correct for tuning, but before the
FINAL run (the one whose numbers ship), re-run step 1 to regenerate the full
train.json/meta_train.json first.

**Cutoff:** `start_date >= 2026-03-22`, giving the two most recent (non-always_on)
campaigns in train:

- Eid Gifting Premium (2026-03-22, Sales)
- Post-Eid Lookalike Test (2026-04-05, Leads)

**Result:** 110 / 605 train conversations (18.2%) held out as `validation`,
495 (81.8%) kept as `inner_train`. Chosen over the single-campaign cutoff
(`2026-04-05`, 63 conversations, Leads only) because it lands closer to a
representative fraction and covers two objectives instead of one.

**Stated limitations — read before trusting a number tuned on this set:**

- **No Awareness coverage.** Every train campaign with `objective =
  OUTCOME_AWARENESS` starts before the cutoff, so validation only covers Sales
  and Leads. A threshold tuned here is unvalidated for Awareness campaigns —
  say so explicitly if `PROBABILITY_THRESHOLD` ships as one number for all three.
- **Thin ad/adset coverage.** Validation only touches 5 ads across 4 adsets (of
  20/12 in inner_train). This set is fit for conversation-level calibration
  (bucket all 110 conversations by their ad's predicted probability, check
  against observed outcome) — it is NOT fit for judging any single ad's score,
  there's just not enough of them.
- **Customer overlap, same shape as holdout's.** 28 of 110 validation
  conversations (25.5%) belong to a customer who also appears in inner_train —
  close to holdout's own 29%. Report whatever gets tuned here twice: on all 110,
  and on the 82 conversations from customers inner_train never saw. If the two
  agree, the overlap doesn't matter for this feature set; if they diverge, it does.

**Rule going forward:** validation can be touched as often as needed while
tuning — that's what it's for. The moment a threshold is picked, it gets run
against holdout exactly once, and that holdout number is the one that ships in
the write-up.
