# CTWA Marketing Intelligence Layer — Architecture, Explained Simply

This is the plain-language walkthrough of how the pipeline works, layer by
layer, from the raw JSON files at the bottom to the merchant-facing app at
the top. Each layer only talks to the layer directly below it, which is why
the file tree reads as a stack.

## 1. Ingestion layer — turn raw JSON into typed objects

Files: `conversation_loader.py`, `meta_loader.py`, `joiner.py`, `io_utils.py`

Loads `conv_train.json` (conversations) and `meta_train.json` (campaigns,
adsets, ads, creatives, daily insight rows) into typed dataclasses, with
enum validation on the way in (`platform`, `outcome.type`) so a bad value
fails loudly here instead of silently later. `joiner.py` matches each
conversation to its ad/adset/campaign through `source.ad_id` — only
`meta_ctwa` conversations carry that link; organic and direct conversations
have no ad metadata and are excluded from anything ad-level.
`group_by_level()` walks the joined list once and buckets it three ways (by
ad, by adset, by campaign) so downstream code doesn't re-walk the same list
repeatedly.

## 2. Scoring layer — turn conversations into a rate, then a calibrated score

Files: `classifier.py`, `amounts.py`, `aggregator.py`, `corrector.py`, `revenue.py`

**Classification.** `amounts.py` reads each conversation's outcome and
computes its net amount: `delivered` → the order total, `refunded` → total
minus refunded amount, `ghosted`/`cancelled` → 0, and `stuck_pending` /
`active` / `adversarial` → excluded (unknown yet, not counted either way).
`classifier.py` calls a conversation a success if that net amount clears the
cheapest product's price.

**Aggregation.** `aggregator.py` turns each id's classified conversations
into `RawCounts` (successes, failures, n, excluded, raw_rate).

**Correction — the core statistical idea.** A raw rate from a handful of
conversations is noisy (3/4 is not really "75%"). `corrector.py` treats
each id's true rate as a Beta-Binomial posterior: it starts from a prior
centered on the parent level's own score (ad ← adset ← campaign; campaign
uses an uninformative Jeffreys prior, since it has no parent) and updates it
with that id's own successes/failures. The result is the *score* — a rate
pulled toward its parent in proportion to how little data it has — plus a
90% credible interval.

How hard to pull is not hardcoded: `estimate_prior_strength()` fits it from
the data itself each run, by asking how much of the rate variation between
sibling ids is real versus just sampling noise. Little real variation → pull
harder (be more skeptical of small samples); a lot of real variation → pull
less. This is the one piece of "learning" inside an otherwise fixed
statistical formula, and it is what lets the system get sharper as more
cycles of data accumulate without anyone touching the code.

**Revenue.** `revenue.py` sums net revenue per id, independent of the score
math — used later by the audit layer, not by scoring itself.

## 3. Decision layer — turn a score into scale / hold / kill, then a budget split

Files: `outcome_decision.py`, `guardrails.py`, `action.py`, `allocation.py`,
`explore_selection.py`, `stop_rules.py`, `new_tests.py`

**The core call.** `outcome_decision.py:decide()` compares an id's posterior
against its baseline (the parent's score) using the Beta CDF, getting
P(better) and P(worse). Below `MIN_N_FOR_ACTION` real conversations it
always holds, however confident the math looks — with a fixed prior the
posterior can look "precise" even at n=0, so n itself is the honest gate,
not the interval width. Otherwise: P(better) over the threshold → scale,
P(worse) over the threshold → kill, else hold.

**Guardrails, one-way only.** `guardrails.py` checks Meta-only signals never
seen by `decide()`: audience fatigue (frequency ≥ threshold and CTR down
versus the ad's own first week) and CPA blowing past a multiple of the
account's baseline CPA. `action.py:resolve_action()` can only push a
"scale" down to "hold" when one of these fires — it can never upgrade a
hold to a scale or turn anything into a kill. Meta data explains *why* the
budget didn't grow; it never becomes evidence that something is working.
That separation keeps the score meaning one thing (how often chats end in a
sale) and keeps Meta's noisier, easier-to-game signals from ever being able
to manufacture a "scale" on their own.

**Allocation — the 70/30 split.** `allocation.py` splits ids three ways:
scale-candidates share ~70% of the next budget weighted by
`P(better) × shrunk ROAS`; kill-candidates get nothing; everything else
(holds, fatigue-vetoed scales, CPA-vetoed scales) becomes explore-pool
candidates.

**Explore selection — a separate question.** `explore_selection.py` decides
which explore-pool candidates actually get one of the ~2-3 test slots and a
budget share, writes each one's hypothesis, and calls `stop_rules.py` for
its stop criterion. This is deliberately split from classification above:
classification decides *what kind of candidate* something is; selection
decides *which candidates are worth spending the limited explore budget on*
and *what each test is actually asking*.

**Stop rules.** `stop_rules.py` simulates adding future conversations at the
currently observed rate to the Beta posterior until confidence crosses a
threshold, then converts that into a days estimate against the campaign's
typical horizon. A candidate that can't resolve within that horizon is
marked unresolvable — genuinely indistinguishable from its baseline, not
worth testing further.

**Never-run combinations.** `new_tests.py` proposes audience × creative
pairings that have never been tried, ranked by a naive independent-effects
estimate (never a funding decision — an explicit "this is a guess," per the
docstring).

## 4. Audit layer — check the decision against the money, without touching it

Files: `audit.py`, `findings.py`

`audit.py` computes ROAS (revenue ÷ spend) per id and shrinks it toward the
portfolio mean using the same Bayesian-averaging idea as the score
corrector, so a handful of orders can't swing it to an extreme. This shrunk
ROAS feeds the exploit weighting and the CPA guardrail above — but never the
score or the decide() call itself; the probability decisions are read
directly from WhatsApp outcomes, and money is a second, independent check on
those decisions rather than an input to them.

`findings.py` compares the finished action/bucket against that same shrunk
ROAS and flags three kinds of disagreement: funded to scale but losing
money, marked to stop but still profitable, or a top earner that isn't
being scaled. This is read-only, surfaced to the merchant, and deliberately
never looped back into automated decisions — see the reasoning below.

## 5. Reasoning layer — turn the finished decision into merchant language

Files: `llm.py`, `reasoning.py`, `hypothesis.py`

This layer writes; it never decides. Everything it produces is built from
`DecisionFacts` — a fixed, narrow set of values already computed upstream
(score, n, action, bucket, ROAS, budget share, …) — so nothing here can
invent a number. `reasoning.py` writes the one-to-two-sentence merchant
explanation for every row. `hypothesis.py` rewrites the explore-row
hypothesis in plain language, and separately rewrites the hypothesis for
never-run proposed tests (with its own system prompt, since those have no
observed results to describe). Every call goes through `llm.py`'s shared
cache (keyed on the facts + prompt, so unchanged data reproduces the exact
same text) and a deterministic template fallback (so the pipeline runs, and
produces every column, fully offline). Known limitation, stated plainly:
nothing currently verifies that a generated sentence's wording is accurate
beyond the numbers it was given — an earlier number-matching check was
removed because it rejected correct sentences and caught none of the actual
wording errors observed.

## 6. Orchestration + output layer — wire it together, then write it out

Files: `pipeline.py`, `reporting.py`

`pipeline.py` has no decision logic of its own. Its `__main__` block is the
literal execution order — load → join → fit prior strengths → posteriors
bottom-up (campaign → adset → ad) → revenue/spend/ROAS → allocation plans →
scoreboards → reasoning → print/write — every line a call into a layer
explained above. `build_scoreboard()` is where a decision actually gets
computed for a row (calling `decide()` and `resolve_action()`), but the
logic for *how* to decide lives entirely in the decision layer.

`reporting.py` turns finished rows into the three outputs: `write_csv()`
(the brief's deliverable — one row per id), `write_plan()` (a companion JSON
with what has no id to sit on: totals, proposed tests, findings — this is
what the app reads), and `print_scoreboard()` (console only).

## 7. App layer — the merchant-facing read

File: `app.py`

Reads only `outputs/plan_*.json` — no pipeline coupling. Two pages: a
**Scoreboard** page where every row is a self-contained card carrying the
decision, the short evidence trail, and the full reasoning sentence inline
(only the statistical fine print — confidence interval, open conversations
— sits behind an optional "More detail" expander), plus the Exploration
plan and the "Worth a second look" (findings) sections; and a separate
**Numbers** page with spend/revenue/ROAS, kept apart so the main page stays
a recommendation to act on rather than an accounting sheet.

## The one design rule that runs through all of it

Meta / money signals can inform *why* a decision looks the way it does, and
can only ever pull a decision down (veto a scale, flag a disagreement) —
never up. The score itself is computed from WhatsApp outcomes alone. This
is what keeps the score calibratable and reproducible: if ROAS could
upgrade a decision, the score would depend on the very budget-shifted spend
its own recommendation would go on to influence, and it would stop meaning
one fixed thing across runs.
