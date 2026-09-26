# Marketing Intelligence Layer — Findings Log
 
*Project: Continuous-learning ad scoring system for a premium-food merchant in Cairo (WhatsApp + Meta CTWA data). Deadline: Aug 28, 2026.*
 
---
 
## 1. Manual Scoring Walkthrough — "Post-Eid Lookalike Test" Campaign
 
Manually scored the ad with the most conversations (38) to validate the scoring logic end-to-end before building the pipeline.
 
### Ad: "10% LAL test creative"
Part of an experimental campaign testing a new 10% lookalike audience (built from delivered customers) with a "premium heritage" creative.
 
| Outcome | Count | Scored | Notes |
|---|---|---|---|
| Delivered | 16 | 16/16 | Full success |
| Ghosted | 10 | 0/10 | Full failure |
| Cancelled | 2 | excluded (0/0) | 1 wrong-product mixup (not ad's fault); 1 no stated reason |
| Active | 6 | 3/6 | Split by last-message tone: 3 polite brush-offs → treated as ghosted; 3 real engagement → treated as likely win |
| Stuck_pending | 2 | 2/2 | Customer ready to buy; failure was operational, not ad's fault |
| Refunded | 2 | 1/1 (1 excluded) | 1 no stated reason → excluded; 1 service/quality issue → counted (not bad targeting) |
 
**Final score: 22/35 = 63% → Hold.** Not high enough for scale, not low enough for kill. Experimental test, not a proven audience — keep as an option next cycle without extra budget.
 
### Adset: "10% Lookalike experimental"
Targets Cairo, Alexandria, Mansoura, Tanta, ages 25–55. Contains only the one ad above → adset score = ad score = **63% → Hold**.
 
> Note: with only one ad under the adset, ad-level and adset-level results are identical — audience quality and creative quality can't be separated. This only becomes separable when an adset has multiple ads (then scores are combined weighted by conversation count).
 
Interpretation: this was a *scale test* ("if we widen slightly beyond our current customers, do we still find people who buy at the same quality?"). 63% says: not strong enough to widen further right now.
 
### Ad/Adset: "5% LAL test creative" / "5% Lookalike experimental"
 
| Outcome | Count | Scored | Notes |
|---|---|---|---|
| Delivered | 11 | 11/11 | Full success |
| Refunded | 2 | 1/1 (1 excluded) | 1 no stated reason → excluded; 1 not ad's fault → counted |
| Active | 1 | 1/1 | Still open, leaning positive |
| Cancelled | 5 | 0/2 (3 excluded) | Excluded: 1 modification, 1 no stated reason, 1 unrelated external cause. Counted as real failures: 2 lost to competitor at decision point |
| Stuck_pending | 2 | 2/2 | Operational failure only |
| Adversarial | 2 | excluded | — |
| Ghosted | 2 | 0/2 | No purchase |
 
**Final score: 15/19 = 79% → Scale.** Single ad under the adset → adset score = ad score = 79% → Scale (moderate confidence, small sample — shouldn't be treated as high-confidence until more data confirms the pattern).
 
Stronger than the 10% lookalike ad — consistent with expectation that a tighter (5%) lookalike audience matches best customers more closely than a wider (10%) one.
 
### Campaign: "Post-Eid Lookalike Test"
 
| Adset | Score | Decision | Conversations |
|---|---|---|---|
| 10% Lookalike experimental | 63% | Hold | 35 |
| 5% Lookalike experimental | 79% | Scale | 19 |
 
Weighted campaign score: (35×63% + 19×79%) / 54 = **68.6%**
 
**Key finding:** the single rolled-up number (68%) reads as "moderate, hold" and hides the real signal — the narrower audience (5%) clearly outperformed the wider one (10%). A single score tells the merchant the strategy is "okay" without saying *which version* worked.
 
**Recommendation:** the lookalike-audience concept works. Next cycle should scale the **5% approach specifically**, not the campaign as a whole.
 
**System implication:** a single aggregated score at the campaign level is not sufficient output on its own — the system must surface the reasoning and sub-level breakdown (which adset/creative drove the result), not just the final rolled-up number. (This validates the Reasoning Generator design already locked at each level.)
 
---
 
## 2. Evaluation & Explore/Exploit Design Notes
 
- **Holdout split:** split by campaign start date (time-based), reserving ~20% of the data. *(Not yet implemented.)*
- **Explore (~30%) scope — RESOLVED:** explore includes both (a) genuinely new, untested hypotheses, and (b) reallocating budget to existing low-confidence ads/adsets specifically to gather enough data for a confident decision. The locked design's original definition ("genuinely new, untested hypotheses only") is now widened to cover both cases — this is not a separate third mechanism.
---
 
## 3. Small-Sample Statistics Research
 
**Why small samples need different handling:**
- Raw percentages are unreliable with few observations — a small sample can easily show an extreme rate (0% or 100%).
- Standard statistics need volume to distinguish real signal from noise.
**Three candidate methods:**
 
1. **Wilson Score Interval** — calculates a confidence lower-bound that accounts for sample size. Penalizes small samples for uncertainty, not performance (1 vote at 100% → low cautious score; 500 votes at 98% → score close to 98%).
2. **Empirical Bayes / Shrinkage** — pulls a small sample's estimate toward the average of a larger, similar population, weighted by how little evidence the individual sample has ("extraordinary outliers require extraordinary evidence"). Small samples shrink heavily toward the average; large samples barely move.
3. **Beta-binomial regression** — same shrinkage idea, but the prior itself adjusts based on sample size instead of staying fixed; also checks whether sample size itself correlates with performance before shrinking.
**Link to locked design:** the threshold formula `required_score_for_scale = base_threshold + margin × (1 - confidence)` already reflects this logic — confidence stretches the bar higher for low-confidence ads, mirroring the shrinkage principle of not trusting extreme scores from small samples without more evidence.
 
**Gap surfaced:** confidence bucketing must also account for **group comparability** — ads should only be compared within groups optimized for the same goal. (This matches the already-locked rule that confidence bucketing must segment by `optimization_goal`, keeping the 3 REACH adsets separate from the 23 CONVERSATIONS adsets.)
 
---
 
## 4. Meta Advertising Domain Research
 
**Meta advertising performance:**
- Meta uses different campaign objectives depending on business goal.
- Meta provides multiple performance/delivery measures (impressions, frequency, CTR, …) — these describe different aspects, not one universal success measure.
- Meta data describes activity *before/around the click*; conversation data holds *downstream customer outcomes*.
**Meta advertising experimentation & budget allocation:**
- **Incrementality testing (holdout/control groups):** split audience into statistically matched treatment (sees ads) and control/holdout (doesn't see, ~5–10%) groups; the outcome gap is the true causal lift, not just correlation.
- **A/B testing:** Meta recommends test-and-learn — "fixed split, then winner takes all."
- **Multi-armed bandits (dynamic budget allocation):** distribute budget across ad sets, shifting toward better performers while retaining ongoing exploration.
- Meta recommends simplifying similar ad sets — running many similar ad sets simultaneously gives each fewer opportunities/results.
**Link to locked design:** the project's 70/30 explore/exploit split is a *static simplification* of what a bandit treats as a dynamic, continuously-adjusted ratio.
 
---
 
## 5. System Design Evolution (V1 → V5)
 
The architecture was built incrementally, each version resolving one gap in the previous one.
 
### V1 — Split Manual / Automated
A rule-based **router** sorts conversations by outcome type.
- **Cheap outcomes** (delivered, ghosted, adversarial) are scored instantly by a fixed rule.
- **Expensive outcomes** (stuck_pending, refunded, cancelled, active) still require a human to read the conversation text and decide cause, fault, and whether it counts.
### V2 — Fully Automated
Same router, same split. The human reader is replaced by an **LLM step**: it reads the conversation text and answers the same three questions a human was answering by hand — what caused this outcome, whose fault is it, and does it count.
 
### V3 — Weighted Outcomes (not all successes count the same)
- A conversation goes to the **Rule Classifier** if its outcome is fully determined by structured fields alone (`outcome.type`, `status_history`) with no variance requiring interpretation.
- It goes to the **LLM Classifier** only if the correct decision (exclude / success / failure) genuinely depends on reading the conversation text — because the same outcome label covers multiple different real situations.
- The output is no longer binary. Each outcome gets a **weight** reflecting how clean the success or failure actually was.
- **Confidence** now depends on two things: how many conversations back the score, and how certain the automated read step was. A score built mostly from clean Rule Classifier outputs is more trustworthy than the same score built mostly from uncertain LLM reads.
### V4 — Reasoning Added at Every Level
Each rollup step (Ad → Adset → Campaign) is followed by a **reasoning component** that explains the score instead of leaving it as a bare number. All three reasoning components are LLM calls doing synthesis and comparison over the results.
 
### V5 — Decision Threshold Added at Every Level *(current)*
 
**Full pipeline, left to right:**
 
`Conversations → Outcome Router → [Rule Classifier | LLM Classifier] → Ad-level Aggregator → Decision Threshold + Ad-level Reasoning Generator → Adset-level Aggregator → Decision Threshold + Adset-level Reasoning Generator → Campaign Aggregator → Decision Threshold + Campaign-level Reasoning Generator → Output`
 
**Outcome Router → Rule Classifier** (structured fields only, no interpretation needed):
| Outcome | Weight |
|---|---|
| delivered | 1 |
| ghosted | 0 |
| stuck_pending | 0.9 |
| adversarial | excluded |
 
**Outcome Router → LLM Classifier** (weight depends on reading the conversation text):
| Outcome | Prompt question | Weight |
|---|---|---|
| active | Does tone lean toward purchase or disengagement? | 0.7 / 0.1 |
| refunded | Is fault product/service-related, ad/offer-related, or no reason stated? | 1 (product/service fault) / 1 − (refunded/total) (ad/offer fault) / excluded (no reason) |
| cancelled | Lost to a competitor, order modification, external/unrelated cause, or no reason stated? | 0 (competitor) / excluded (all other cases) |
 
> **Note carried on diagram:** the active-outcome score weights (0.7 / 0.1) are currently a fixed estimate, not derived from data. Ideally this should be a **learned conversion rate** — the % of past "active" conversations that resolved to delivered once their outcome became known in a later cycle. *(Matches the already-logged "Active weight reclassified as dynamic predictor" decision — flagged here again as a known v1 limitation on the diagram itself.)*
 
**Aggregation, at each of the three levels (Ad → Adset → Campaign):**
- **Aggregator** rolls up the score (weighted average by conversation count, per already-locked design).
- **Reasoning Generator** (LLM call) runs in parallel off the same score:
  - *Ad-level:* summarizes causes already tagged by the classifiers → outputs explanation, recommendation.
  - *Adset-level:* compares ads within the adset, identifies which ad drove the result → outputs explanation, recommendation.
  - *Campaign-level:* compares adsets, explains score differences, identifies the likely driving factor → outputs explanation, recommendation.
- **Decision Threshold** (new in V5) computes that level's confidence, then checks the score against confidence-adjusted thresholds:
  ```
  required_score_for_scale = base_threshold + margin × (1 - confidence)
  if score > required_score  → Scale
  if score < kill_threshold  → Kill
  else                       → Hold
  ```
  - Diagram annotation: *the base_threshold applies when confidence is high (≈1); the margin stretches upward as confidence drops.*
  - Low confidence makes **both** Scale and Kill harder to reach — uncertain cases default to Hold rather than being pushed to an extreme in either direction.
**Final Output object** (produced once, after the Campaign-level Decision Threshold):
```
Score: %
Confidence: %
Action: Scale / Hold / Kill
Explanation: text
Key_factor: text
Recommendation: text
```
## 6. Mentor Phase Requirements — Pivot from Confidence Bucketing to Range-Based Decisions

Mentor instructions for this phase required:
- Every ad/adset/campaign score must **not fully trust a raw rate when sample is small** — lean toward the group's typical number, less so as sample grows (shrinkage).
- A **range shown, not just one number** — decision made from that range, not the point score.
- 70/30 explore/exploit: exploit weighted by confidence; explore turned into **named tests with a hypothesis and a stop rule**.
- Deliverable: the pipeline actually running on real data, with reasoning entries, raw rate next to corrected score, and final decisions — logged daily.

**Direct conflict with prior decision:** earlier research (Wilson Score vs. point-estimate-plus-confidence-label) had concluded a shrunk point estimate + a separate confidence bucket was sufficient, explicitly rejecting the need for an interval. The mentor's requirement overturned this — decisions must be interval-based, not point-based. This forced a return to interval methods.

**Resolution — Beta-Binomial hierarchical shrinkage confirmed as the method** (over Wilson Score alone, single-level Empirical Bayes, and point-only James-Stein): it produces a corrected score *and* a credible interval from **one** posterior calculation, satisfying both the shrinkage and range requirements simultaneously. Reasoning logged in `docs/method_selection_reasoning.md`.

**Confidence bucketing's role redefined:** no longer judges whether a score is trustworthy (shrinkage now does that upstream) — its role shifts to judging *how aggressively to act* on the already-corrected score.

**Point estimate vs. full interval — resolved via research:** confirmed that a corrected point + discrete confidence label converges on the same practical decision as a full interval in most cases — but the *mentor's explicit requirement* overrides this preference, so interval-based decisions are mandatory regardless.

---

## 7. Outcome Scoring — Major Correction (Dropped LLM Fault-Classification)

**Original plan (now abandoned):** use an LLM to classify refund/cancellation causes into fault categories (ad-fault vs. product-fault vs. customer-fault vs. no-reason), assigning weights accordingly.

**What went wrong, confirmed by both data inspection and industry research:**
- Manually inspecting real `refunded` conversations (56 cases) showed the invented categories didn't fit the actual reasons (buyer's remorse/over-ordering appeared repeatedly; zero cases blamed the ad itself; some "reasons" were photo-only, unreadable from text; several sampled rows weren't refund-reason messages at all).
- Industry research (GA4, ROAS literature) showed refunds are never fault-classified in practice — they're treated as a **net revenue adjustment**: the refunded amount reduces the original conversion's value, it doesn't reclassify the conversion. Partial refunds reduce value proportionally, not by cause.
- General conclusion: we were trying to score *intent* (why something happened); the industry scores *proven outcomes* (what actually happened, in net value terms).

**Corrected, industry-grounded scoring schema (locked):**

| Outcome | Value | Scoring result |
|---|---|---|
| `delivered` | 1.0 | success |
| `ghosted` | 0.0 | failure |
| `refunded` | `1 - refunded_amount/total` (computed raw first) | binarized by threshold → success/failure |
| `cancelled` | 0.0 | failure (same treatment as a full refund) |
| `active` | — | excluded (unresolved, not proven either way) |
| `stuck_pending` | — | excluded (unresolved — corrected from an earlier weight of 0.9; "confidence in classification" was wrongly conflated with "finality of outcome") |
| `adversarial` | — | excluded (not a real commercial transaction) |

**Refund binarization threshold — grounded, not invented:** inspected the actual `refunded_amount/total` ratio distribution across all 56 refunded conversations in the dataset. Found a natural gap between 0.878 and 1.0 (29 full refunds cluster exactly at 1.0; 27 partial refunds spread 0.18–0.88). **Threshold set at 0.5** — near the midpoint of the partial-refund spread, confirmed as standard practice (compute continuous value first, binarize right before it enters the model — validated via search on general ML/statistics practice, not marketing-specific).

**Insight/diagnosis generation (the "why") is a separate concern from scoring** — deferred to the reasoning-layer step (per ad/adset/campaign), not per-conversation during classification. `llm_classifier.py` and a Gemini API client (`llm_client.py`, `config.py`) were built, tested with real prompts, then **removed** once scoring became fully rule-based — to be redesigned when the reasoning/diagnostics step is actually reached. Model choice research concluded `gemini-2.5-flash-lite` is the right cost/performance fit for this kind of simple classification task, should this be rebuilt later.

**Net effect: scoring no longer requires any LLM call.** Fully rule-based, deterministic, testable.

---

## 8. Beta-Binomial Prior Strength — Research and Fixed-Constant Decision

**Ideal method (Robinson-style method-of-moments, deriving prior strength from sibling-group variance) confirmed not viable at our data scale:** checked ads-per-adset distribution — 13 of 26 adsets have only 1 ad, 12 have 2, 1 has 3. Can't compute meaningful variance from 1-2 data points. Same problem one level up (adsets-per-campaign).

**Fallback validated:** pool variance across **all** ads (or all adsets) in the dataset rather than per-sibling-group, keeping the *shrinkage center* specific to each item's direct parent, but making the *shrinkage strength* a dataset-wide derived constant.

**Computed on train.json (605 conversations, 25 ads / 16 adsets):**
- Ad-level: mean=0.572, variance=0.023 → prior strength ≈ **9.64**
- Adset-level: mean=0.556, variance=0.017 → prior strength ≈ **13.15**

**Stability check (leave-one-campaign-out):** ad-level prior strength ranged 8.23–11.57 depending on which single campaign was excluded — confirms the number is grounded but not perfectly stable; would shift with a different train/holdout split.

**REACH-segment edge case surfaced and left unresolved:** only 1 REACH-goal ad exists in train — zero variance computable, no valid same-goal sibling pool. Current implementation pools REACH in with CONVERSATIONS-goal ads by default, which technically violates the already-locked "must segment by optimization_goal" rule. Flagged, not fixed.

**Final decision:** rather than maintain two separate constants, tested using a single `PRIOR_STRENGTH = 10` for both levels — found the practical difference vs. two separate values (10 vs. 13) was negligible on real examples. **Locked: `PRIOR_STRENGTH = 10`, applied uniformly at both ad and adset levels.** Documented as a simplification, explicitly revisitable.

**Update: no longer a fixed constant.** `corrector.estimate_prior_strength()` now fits this from the data each run (method-of-moments on that level's own raw rates), separately for ad and adset level, instead of the hardcoded 10 above.

**Out-of-sample validation (does shrinkage actually help?):** for each ad, split its own conversations chronologically (early half fits a score, late half — never seen by the score — checks it). 22/25 ads had enough data on both sides. Brier score on the 192 held-out late conversations: **shrunk score = 0.239 vs. raw rate = 0.277** — shrinkage predicts an ad's own future conversations ~14% better than trusting its raw rate. (`scripts/time_split_brier.py`.)

---

## 9. Codebase Built This Session (verified against real data)

Final structure after several rounds of renaming to match an agent-noun convention (loader, joiner, classifier, aggregator, corrector):

```
marketing_intelligence_layer/
├── data/ (train.json, holdout.json, meta_data.json, conversations.json)
├── src/
│   ├── config.py
│   ├── ingestion/
│   │   ├── conversation_loader.py   # parses conversations.json, validates known outcome types/platforms
│   │   ├── meta_loader.py            # parses meta_data.json, validates optimization_goal
│   │   ├── joiner.py                 # links conversations to ad/adset/campaign; separates organic/direct and unmatched_ctwa
│   │   └── io_utils.py               # shared JSON load + enum validation
│   └── scoring/
│       ├── classifier.py             # classify() -> ClassificationResult{value: float|None, success: bool|None}
│       ├── aggregator.py             # raw_rates() -> independent RawCounts per ad/adset/campaign (no circularity)
│       └── corrector.py              # compute_posterior() -> Beta-Binomial shrunk score + 90% credible interval
├── tests/
│   └── demo_pipeline.py              # runs full chain on one real campaign→adset→ad, prints trace at every stage
└── docs/
    ├── method_selection_reasoning.md
    ├── outcome_scoring_reasoning.md
    └── demo_pipeline_output.md
```

**Verified end-to-end on real train.json data.** Example run (ad with n=5, demonstrating shrinkage clearly):

| Level | n | Raw rate | Shrunk score | 90% interval |
|---|---|---|---|---|
| Campaign | 42 | 0.548 | — (top level, no shrinkage) | — |
| Adset | 15 | 0.600 | 0.579 | [0.416, 0.735] |
| Ad | 5 | 0.800 | 0.653 | [0.445, 0.836] |

Visual confirmation also produced (Beta posterior curves at small/medium/large n) showing interval width shrinking as n grows (0.391 → 0.319 → 0.250), validating the mechanism behaves as intended.

**Two bugs caught and fixed during this build:**
- `join.py`/`joiner.py` had a stale import referencing a since-renamed module (`load_conversations` → `conversation_loader`).
- An early `aggregator.py` draft called `classify()` twice per conversation unnecessarily (fixed with a walrus-operator single call).

---

## 10. Revised Architecture (V6, supersedes the V5 diagram in Section 5)

```
Data (conversations + meta) 
  → Classification (fully rule-based now — no LLM at scoring stage) 
  → Beta-Binomial posterior (point + credible interval, per level, hierarchical: campaign → adset → ad) 
  → Decision + reasoning (must be derived from interval bounds, not point score — NOT YET DESIGNED) 
  → Explore/exploit allocation (70/30, exploit weighted by confidence, explore as named tests with hypothesis + stop rule — NOT YET DESIGNED) 
  → Scoreboard output (score, interval, decision, reasoning — NOT YET BUILT)
```

Key structural change from V5: the Rule/LLM classifier split at the outcome-router level is gone — classification is now fully deterministic/rule-based, and LLM involvement (if any) moves entirely to the reasoning/insight stage, applied per ad/adset/campaign rather than per conversation.

---

## 11. Immediate Next Step (where this session left off)

**Decision layer — Scale/Hold/Kill from interval bounds.** Open design question, not yet resolved:
- **Lower-bound-only approach:** conservative — decision driven by "how bad could this realistically be" (the interval's lower bound alone vs. thresholds).
- **Full-interval-vs-threshold comparison:** where does the *entire* interval sit relative to Scale/Hold/Kill cutoffs (e.g., whole interval above a cutoff → Scale; straddles a cutoff → Hold).

This must be resolved (by research or explicit decision) before writing `decision.py`. After that: explore/exploit allocation, then reasoning/insight generation (deferred from Section 7), then final scoreboard assembly.