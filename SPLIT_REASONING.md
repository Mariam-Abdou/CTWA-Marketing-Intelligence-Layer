# Holdout Split — Reasoning

**Method:** structured split by campaign `start_date` (not random, not customer-based).

**Why not random:** the brief prohibits random splitting at the conversation level.

**Why not customer-based:** investigated whether repeat customers (123 of 643,
34% of conversations) require a customer-atomic split to avoid leakage. Found no —
every conversation carries its own unique `order_id` (no order/conversation is ever
shared or re-referenced across records), and the current classifier design scores
each conversation independently with no customer-level feature or cross-conversation
reference. No mechanism exists for information to leak from one customer's train-side
conversation to their holdout-side conversation under this design.

**Why campaign start_date:** cycles and months don't align cleanly with the
Sales/Leads/Awareness structure the system is graded on, and a pure random or
per-conversation split was ruled out. Ordering whole campaigns by `start_date` and
cutting at the most recent group is a structural, reproducible, non-random method
that also mirrors real deployment: a merchant would decide next-cycle budget based on
the most recently launched campaigns' results.

**Cutoff:** `start_date >= 2026-04-29`. This is the point where campaign start-dates
naturally cluster into a ~20-30% holdout, and it lands exactly on a shared start-date
between two campaigns (Summer Premium Launch, Lookalike Scale Cycle 3) — a clean
boundary, since splitting a tied pair would require an arbitrary tie-break.

**Result:**
- Holdout: 4 campaigns (Summer Premium Launch, Lookalike Scale Cycle 3, Mid-Year
  Sale, Summer Retention Push) = 183 ad-attributed conversations = **29.7% of the
  617 scoreable (ad-attributed) conversations**, or 23.2% of the full 788.
- Train: the remaining 8 campaigns (incl. Always-On, which spans the full timeline
  but starts earliest, 2025-12-30 — so it falls into train by the same ordering
  logic, no special-case needed) = 434 ad-attributed conversations.
- Holdout covers all 3 real objective types present in the data (Sales, Awareness,
  Leads), which was the deciding factor over a smaller, less objective-diverse
  cutoff that landed closer to a literal 20%.

**Organic/direct (171 conversations, 21.7% of 788):** always placed in train, never
eligible for holdout. They carry no `ad_id`/`adset_id`/`campaign_id`, so they cannot
produce a row in the `{level, id}` scoreboard output and cannot be evaluated for
calibration. Kept in train as optional context (e.g. incrementality baseline,
organic outcome-rate comparison) since including them costs nothing and may be
useful; they are structurally distinct from ad-attributed data and will need their
own code path if used, not a shared feature pipeline.

**Denominator note for reporting:** the true holdout percentage should be reported
against 617 (scoreable conversations), not 788 (which includes organic/direct that
were never eligible for holdout in the first place). Reporting against 788 would
understate the holdout's actual size.

**Stated limitation:** 29.7% is above the 20% target. This was a deliberate
size-vs-representativeness-vs-objective-diversity tradeoff -- smaller cutoffs closer
to 20% either skewed further from the overall outcome distribution or dropped the
Leads objective entirely. Given the dataset's small size, erring toward a larger
holdout was judged safer than a smaller, noisier one.
