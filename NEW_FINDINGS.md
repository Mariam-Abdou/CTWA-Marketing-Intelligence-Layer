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

---

## 5. Meta Learning Phase — Supporting Context (Teammate's Research)

Teammate logged notes on Meta's ad-delivery learning phase (source: easyinsights.ai, not an official Meta doc). Key points and how they relate to our project:

- Meta targets ~50 optimization events per ad set per week to exit learning phase; below that, an ad set can be labeled "Learning Limited." **This is Meta's own operational threshold — not imported into our system.**
- **Relevance 1 (data interpretation, not a rule):** an ad set still in Meta's learning phase during the cycle we're scoring may show volatile CPA/outcomes for reasons unrelated to ad quality — useful context for Kill-decision explanations, not a scoring input.
- **Relevance 2 (validates existing design):** Meta's "Learning Limited" label is a parallel case of the same principle behind our confidence bucketing — insufficient volume → unreliable signal. Supports, doesn't change, the percentile-based bucketing already locked.
- **Relevance 3 (explore/exploit at cycle transitions):** a big change (budget/creative/audience) can put an adset back into Meta's learning phase — an additional reason (beyond seasonal context) to temporarily raise explore allocation at cycle transitions.
- Meta's learning also happens at the ad-set level rather than requiring every individual ad to hit the volume threshold independently, and industry guidance favors concentrating limited conversion volume over splitting it across many small ad sets — a possible supporting argument for the explore/exploit scoping question (new creatives vs. reallocating to low-confidence existing ads), leaning toward concentration over fragmentation.

**Correction logged:** teammate's part-2 notes framed small-sample handling as still-open exploration ("understand the core idea... rather than over-engineering the MVP"). This project has already completed that research (Milestone 3, approved) and made a directional decision: the prior is computed from our own dataset, not an external population — relevant because Wilson/EB/beta-binomial are traditionally designed to draw a prior from a larger external population.

---

## 6. Comparable Projects / Prior Art Search

**Search conducted specifically for:** any published research, open-source project, or product doing WhatsApp-conversation-based marketing analysis comparable to ours.

**Finding: no directly comparable published system found.** Nothing combines (WhatsApp conversation outcomes) + (CTWA attribution) + (small-sample confidence estimation) + (Scale/Hold/Kill decision layer) in one pipeline. This supports that the project fills a real gap rather than duplicating existing work.

**Closest product analog — TBit** (commercial SaaS, ~$45/mo):
- Tags every WhatsApp conversation with campaign/adset/creative/audience from the start (attribution tagging).
- Tracks the conversation through to outcome (qualified lead/appointment/sale) and attributes revenue back to the originating ad.
- Explicitly targets the same problem we're solving: avoiding cutting a low-volume/high-conversion campaign or scaling a high-volume/low-conversion one.
- Gap: marketing material only, no visible handling of statistical confidence or small-sample bias — presents raw attribution numbers as equally reliable regardless of volume.
- Mechanism (industry-standard, not confirmed as TBit's exact implementation): `ctwa_clid` captured from the referral object in the first inbound webhook message → stored against the conversation record → on outcome (purchase/qualified lead), event + `ctwa_clid` sent to Meta via Conversions API (CAPI) with `action_source: business_messaging` (a separate dataset from the standard website pixel) → Meta matches the click and closes the attribution loop.
- **Actionable finding:** attribution should use a hierarchy/tiering rather than a single "source" field — native ad referral with `ctwa_clid` is deterministic; manually-set links/codes are weaker "operational" attribution; a missing referral should be classified as unknown/non-deterministic, **not automatically folded into organic**. This is a candidate refinement to our `meta_ctwa` / `organic` / `direct` split — not yet applied, needs checking against the actual dataset for any "weak attribution but not truly organic" cases.

**Closest methodological analog — Bayesian marketing attribution (GitHub repo):**
- Ranks channels by conversion-rate estimate *and* credible interval width, downgrading channels with wide intervals even when the point estimate looks good.
- Same philosophy as our confidence bucketing, applied to ~9 marketing channels rather than 40 ads — smaller scale than our problem.
- Uses Bayesian regression specifically to handle small-data challenges by encoding a prior in the coefficients.

**Industry chat-signal extraction patterns found (PollyReach, BizAI, general CTWA/CAPI writeups):**
- Rule-based point scoring with bypass logic (a signal can route straight to a decision regardless of overall score) — maps to our Rule Classifier.
- LLM/NLP-based intent extraction from unstructured conversation text (urgency, budget, timeline) — maps to our LLM Classifier.
- Gap confirmed across all sources: none combine either approach with sample-size-based confidence adjustment.

---

## 7. Hierarchical Partial Pooling / Shrinkage — Research & Decision

**Mechanism:** Rather than computing each ad's rate in isolation (no pooling) or collapsing all ads into one global rate (complete pooling), partial pooling shrinks each ad's estimate toward its parent group's (adset's) mean, with shrinkage strength inversely proportional to the ad's own sample size. Small-n ads shrink heavily toward the adset mean; large-n ads are barely affected. This is a well-established Bayesian hierarchical-modeling result (documented across baseball batting averages, hospital death rates, movie ratings, and other small-group-rate use cases).

**Formula (closed-form, no MCMC needed):** the binomial-rate case (James-Stein / empirical Bayes shrinkage) computes a pooled variance across sibling ads under the same adset, then shrinks each ad's rate by a factor derived from that pooled variance vs. its individual variance — implementable as direct arithmetic, not an iterative model fit.

**Caveat found:** James-Stein-style shrinkage assumes sibling groups are genuinely similar; it's not appropriate for pooling across ads with fundamentally different underlying rates (e.g., our 3 REACH adsets vs. 23 CONVERSATIONS adsets) — reinforces the already-locked rule that confidence bucketing (and now shrinkage) must segment by `optimization_goal` before any pooling occurs.

**Decision leaning:** adopt 2-level hierarchical shrinkage (ad ← adset) as a simplified empirical Bayes / James-Stein estimator (closed-form arithmetic, not full MCMC), computed upstream as the actual ad-level score — not as an optional add-on layer. This is intended to **replace** the previously-candidate single-level Empirical Bayes (Robinson-style, pooled against the whole 40-ad dataset), since adset-level pooling is more relevant than pooling against a heterogeneous full dataset. Wilson Score remains a possible candidate only if an interval (not just a point estimate) is later judged necessary.

**Point estimate vs. interval — resolved:** a corrected point estimate (post-shrinkage) plus a separate discrete confidence bucket is sufficient for this system's decision layer, which already acts on discrete Low/Medium/High confidence rather than a continuous interval range. Research supports that intervals and point-estimate-plus-label approaches converge on the same practical decision in most cases; the added complexity of a full posterior/interval (Wilson Score or MCMC-based) is not currently justified unless the decision layer's design changes to require interval width directly.

**Confidence layer's role redefined:** confidence bucketing no longer needs to judge whether a score is trustworthy (shrinkage now corrects the score itself pre-emptively) — its role shifts to judging how aggressively the decision layer should act on the (already-corrected) score, since even a shrunk estimate from n=3 remains more volatile cycle-to-cycle than one from n=50.

**Open, not yet resolved:**
- Whether 2-level (ad←adset) is sufficient or whether campaign-level pooling should be added later — leaning toward 2-level for MVP given limited practical gain from a 3rd level at current data volume (40 ads / 26 adsets / 12 campaigns).
- Interaction between shrinkage and the per-cycle dynamic active weight recomputation — not yet researched or resolved; no useful cross-cycle shrinkage precedent was found at our data scale (existing literature on time-varying shrinkage targets much richer time series than 3 cycles). Current lean: recompute shrinkage fresh each cycle from that cycle's own group stats, same as the active weight already does — not import a time-aware shrinkage model.
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

