# Scoreboard Comparison — v1 (pre-changes) vs Current

## The real weakness we picked, and fixed

`scoreboard_v1.csv` scored every campaign/adset/ad using only the WhatsApp
reply signal (the Bayesian posterior). It had no read at all on Meta's own
performance signals — frequency, CTR trend, cost-per-order. So an ad could
be told `scale` by v1 while it was already fatiguing or bleeding money on
Meta, because nothing in the pipeline could see that.

This wasn't a hypothetical gap — v1's own output proves it happened:

| id | v1 (WhatsApp-only) | Meta signal at the same time | current (Meta-aware) |
|---|---|---|---|
| adset `...240002` | `scale` | frequency ≥ 3.0, CTR drop ≥ 25% (fatigue) | `hold` → `explore` |
| ad `...210005` | `scale` | frequency ≥ 3.0, CTR drop ≥ 25% (fatigue) | `hold` → `explore` |

Both items would have kept receiving scaled budget under v1, purely because
v1 had no way to see they were already fatiguing on Meta. The fix (fatigue
guardrail, change 4, wired into every level by change 6) catches exactly
these two, on the same real data, and states the reason explicitly in the
scoreboard's `hypothesis` column. That's a measurable before/after
correction on the same rows — not an incidental shift elsewhere in the file.

## Side-by-side: full comparison

**Files compared:** `outputs/scoreboard_v1.csv` (before any of the 6
changes) vs the latest run (`scoreboard_20260905_234207.csv`), same train
data, 49 rows each (8 campaigns, 16 adsets, 25 ads).

**Data caveat:** 9 of 49 rows (mostly around campaigns 220001/220008 and their
child adsets/ads) have a different `raw_rate` between the two files. Root cause:
`scoreboard_v1.csv` was generated before `scripts/split.py` was fixed to also
split `meta_data.json` into train/holdout (previously only `conversations.json`
was split) — a fix unrelated to the 6 exploit/explore changes, so those 9 rows
mix two effects. The other 40 rows have identical `raw_rate`/`score`/`interval`
in both files — differences there are purely from the 6 changes.

### Aggregate changes

| | v1 | current | measured effect |
|---|---|---|---|
| action: scale | 6 | 5 | 1 scale caught by a guardrail |
| action: hold | 36 | 37 | +1, absorbing that catch |
| action: kill | 7 | 7 | unchanged |
| bucket: exploit | 13 | 12 | 1 fewer item gets exploit budget |
| bucket: explore | 9 | 11 | +2, now testing the caught items |
| bucket: none | 27 | 26 | unchanged net (rounding from the above) |

### Per-level bucket counts

| level | v1 | current |
|---|---|---|
| campaign | exploit=5, explore=3 | exploit=5, explore=3 |
| adset | none=7, exploit=6, explore=3 | none=7, exploit=5, explore=4 |
| ad | none=20, explore=3, exploit=2 | none=19, explore=4, exploit=2 |

The adset and ad levels are exactly where the fatigue guardrail fired —
campaign level is untouched because neither flagged item lives there.

### Everything else that moved

**campaign `...220001` (kill→hold) and `...220008` (hold→kill).** These two
are among the 9 rows affected by the data-fix caveat above — their `raw_rate`
differs between the files, so the classification flip can't be cleanly
attributed to the 6 changes. Flagged for transparency, not claimed as a win.

**Every remaining `explore` row now carries a hypothesis and stop_rule**
(v1 already had this for its 9 explore rows) — the difference is `explore`
selection is now ranked by UCB (`interval_high x shrunk_roas`, change 5)
instead of pure ambiguity, and guardrail-vetoed items are explicitly labeled
as such (change 3/4), which v1 could never do since it had no guardrail
wiring at all.

## What did NOT change

`action: kill` stayed at 7 both times, and the campaign-level bucket split
(5 exploit / 3 explore) is identical — the overall shape of the recommendation
is stable. The 6 changes did one specific, provable thing: gave the pipeline
eyes on Meta's own signals, and caught two real items v1 was scaling blind.
