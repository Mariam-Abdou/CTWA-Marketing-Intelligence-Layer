---
phase: CTWA-Marketing-Intelligence-Layer
reviewed: 2026-10-07T23:50:00+03:00
depth: deep
reviewer: Claude, using the gsd-code-reviewer rules from open-gsd/gsd-core
files_reviewed: 52
findings:
  critical: 4
  warning: 14
  info: 8
  total: 26
status: issues_found
---

# Code Review: CTWA Marketing Intelligence Layer

**Scope:** `CTWA-Marketing-Intelligence-Layer/` (src, scripts, app.py, config.yaml, README, git history).
**Read in full:** pipeline, ingestion, scoring, decision, auditing, reasoning, trace, storage/trace_db (first 260 lines + schema code), chat (answer, guard, bot, gate, entities, store, llm, budget, log), insights, ui/chat, ui/data, all scripts.
**Pattern-scanned only:** ui/pages.py, chat/glossary.py, chat/fallback/*, gate_playground.py, app_legacy.py, scripts/inspect.py.
**Not reviewed:** the older copy `6am-llm-club-cappy-project/` and the loose scripts in the parent folder (EDA.py, SPLIT.py, action1.py, decision.py, explore_exploit.py).
**Checked by running:** `py_compile` (passes), git history, data counts, and a real import of `scripts.split`.

Things that are fine: no secrets in code or history (`.env` was never committed), no `eval`/`exec`/shell calls, SQL uses placeholders (f-string SQL only uses internal constants), `run_sql` is read-only with an authorizer, and the LLM never changes a decision.

---

## Critical Issues

### CR-01: Customer data is on a public GitHub repo, including in history
**Files:** `data/original/conversations.json`, `data/train/*.json`, `data/holdout/*.json`, `outputs/trace.db` (history), `.git`
**Issue:** `origin` is `github.com/Mariam-Abdou/CTWA-Marketing-Intelligence-Layer` and the repo page says Public. These data files are tracked on `origin/main`, and every conversation has `customer.first_name`, `last_name`, `phone` (checked). `outputs/trace.db` stores the full raw conversation, including name and phone, for every chat (checked in the local db). It was committed in 5 commits (`0df8a2b` to `51def67`). Commit `c0ae667 "remove customer data"` only deletes it from the latest version. The history still has it. Local branches `backup-before-*` also keep it. Your own `.gitignore` says these files contain customer names and phones. Even if the data is synthetic, you are also publishing the challenge data and your holdout.
**Fix:**
```bash
# 1. make the repo private now
# 2. remove from ALL history
git filter-repo --invert-paths --path outputs/trace.db --path data/
git branch -D backup-before-notes backup-before-rewrite backup-before-tmp-removal
git push --force origin main
# 3. stop tracking generated files and data
printf 'data/\noutputs/\n' >> .gitignore
git rm -r --cached data outputs
```
If this is real merchant data, treat it as already leaked.

### CR-02: There is no held-out evaluation, and the threshold is untuned
**Files:** `scripts/` (no eval script), `config.yaml:41-42`, `README.md:171`
**Issue:** Nothing in `src/` or `scripts/` reads `data/holdout/`. The brief requires an honest holdout evaluation with calibration and simulated business impact. Today there is no calibration check (reliability bins or ECE) for the core scores or `p_better`. Only the chat gate has ECE (`eval_gate.py`). `probability_threshold: 0.75` is marked "Temporary -- not yet tuned" in config, and the README says the same. This is the main missing deliverable.
**Fix:** Add `scripts/eval_holdout.py` that fits on `full_train` only, scores the holdout campaigns, and reports Brier + ECE (10 bins) and a simulated spend/revenue replay. Run it once, at the end. Write the numbers to a file the write-up can quote.

### CR-03: Repeat customers leak across train and holdout
**Files:** `scripts/split.py:43-69`, `scripts/split_lib.py:90-96`, `scripts/time_split_brier.py:45-48`
**Issue:** The split is by campaign start date, which is allowed. But the leak the brief warns about is not handled. I checked: 51 customers appear in both train and holdout, and 53 of 183 holdout conversations (29%) belong to a customer already seen in train. `split.py` never calls `customer_overlap`; only `split_validation.py:88-103` does, and it says the overlap is "REAL" and must be reported both ways. `time_split_brier.py` also splits one ad's chats by time with the same customers on both sides.
**Fix:** Print the overlap in `split.py`, flag or drop overlapping customers' holdout conversations, and report every metric with and without them.

### CR-04: The pipeline crashes when an id has no resolved conversations
**Files:** `src/reasoning/reasoning.py:47`, `src/reasoning/hypothesis.py:71,90`, `src/reporting.py:111`, `src/pipeline.py:119`
**Issue:** `raw_rate` is `None` when an id has chats but none with a known outcome (only active, stuck_pending or adversarial). Then `f"{f.raw_rate:.0%}"` and `f"{r['raw_rate']:.3f}"` raise `TypeError` and stop the whole run. It does not happen on today's data (0 of 25 ads), so it is latent. But 79 of 788 chats here are unresolved, and the grader runs on fresh conversations.
**Fix:**
```python
rate = f"{f.raw_rate:.0%}" if f.raw_rate is not None else "n/a"
```
Apply in all four places, and add a test with an all-unresolved ad.

---

## Warnings

### WR-01: Unknown values in the data abort the whole run
**Files:** `src/ingestion/meta_loader.py:6,83`, `src/ingestion/conversation_loader.py:6-10,44,56`, `src/ingestion/io_utils.py:10-13`, `src/scoring/amounts.py:16,24`
**Issue:** `KNOWN_OPTIMIZATION_GOALS = {"CONVERSATIONS", "REACH"}`, the outcome types and platforms are hard allow-lists that raise `ValueError`. One new value in the unseen data kills the run. In `amounts.py`, `outcome.get("total", 0)` returns `None` if the key exists with a null, and `total - refunded_amount` then raises. A missing `total` on a delivered order silently becomes 0 (a "failure").
**Fix:** Quarantine unknown rows into a report (count + ids) and carry on. Treat a delivered order with no total as excluded, not as a failed sale.

### WR-02: Each score is compared against a baseline that contains its own data
**Files:** `src/pipeline.py:218-241`, `src/scoring/corrector.py:67-76`
**Issue:** An adset's baseline and prior are the campaign posterior, built from data that includes that adset. Same for ads and adsets, and the campaign baseline (`overall_baseline`) includes the campaign. A child with most of its parent's chats is mostly compared with itself, so `p_better` is pulled toward 0.5. It can almost never reach "scale" or "kill", and its data is counted twice (in the prior and in the update).
**Fix:** Use a leave-one-out parent: parent counts minus the child's counts, for both the prior mean and the baseline.

### WR-03: Prior strength is under-estimated when group sizes differ
**File:** `src/scoring/corrector.py:99-110`
**Issue:** The expected noise uses `p̄(1-p̄)/mean_n`. The correct average is `mean(p̄(1-p̄)/n_i)`, which is larger when sizes differ. So the between-group variance is overstated, and the prior strength comes out too small (too little shrinkage). The raw-rate variance is also not weighted by `n`.
**Fix:** Weight by `n_i`, or fit the Beta-Binomial by maximum likelihood with `scipy.optimize`.

### WR-04: The "tuning" evidence for the threshold is circular
**File:** `scripts/tune_threshold.py:85-100,107-133`
**Issue:** "Bad calls" use ROAS from the same conversations that produced the success rates, so the check is not independent. `conv_val` has 2 campaigns and 5 ads (the docstring says so). Ids from three nested levels are counted together, so the same chats count up to three times. ROAS < 1 is treated as "loses money", which ignores product cost.
**Fix:** Call 0.75 a policy choice, or pick it by calibration/regret on a later time window.

### WR-05: ROAS shrinkage is inconsistent, and "loses money" is overstated
**Files:** `src/auditing/audit.py:47,66-84`, `src/auditing/findings.py:38-47`
**Issue:** ROAS shrinkage uses a fixed `PRIOR_STRENGTH=10` while the pipeline fits about 25 and 21 for rates (the `findings.py` docstring describes this exact drift). It mixes units: the pull is weighted by the number of chats but the value is revenue/spend. Findings then tell the merchant a scaled item "loses money" when shrunk revenue/spend is below 1, which ignores margin.
**Fix:** Reword to "returns less than it costs in order value". Pass the fitted strength in or remove the shrinkage.

### WR-06: Invented numbers can reach the merchant, and the chat guard is too loose
**Files:** `src/reasoning/llm.py:12-31`, `src/chat/guard.py:50-53,71,86`, `src/pipeline.py:170-186`
**Issue:** The number check on LLM text was removed, as `llm.py` admits. That text fills `reasoning` and replaces `hypothesis` in the delivered CSV. In the chat guard, `decimals == 0 and abs(c - value) <= 0.5` accepts any whole number within 0.5 of any number in the tool results. `known` includes every digit in ids and names. A hallucinated figure usually matches something. Any number with `[` in the 6 characters before it is skipped. The docstring "Catches invented or computed figures" is too strong. Without a key or cache, the text columns also differ on every run.
**Fix:** Compare exact values only (or the rounded form of a specific field), drop the `<= 0.5` rule, and say in the write-up that text columns are not verified.

### WR-07: The reasoning LLM client has no timeout
**File:** `src/reasoning/llm.py:132-136`
**Issue:** `OpenAI(api_key=..., base_url=...)` uses the default 600 s timeout and 2 retries, per row, one after another. A flaky network can hang the pipeline for a very long time. The chat client sets `timeout=20` (`src/chat/llm.py:22`).
**Fix:** `OpenAI(..., timeout=20, max_retries=1)`.

### WR-08: `run_sql` has no execution limit
**File:** `src/chat/store.py:223-254`
**Issue:** `WITH RECURSIVE` is allowed and there is no `set_progress_handler`. A model-written query can run forever and freeze the Streamlit process. Customer chat text goes into the same model's prompt, so it can try to steer this. The check `";" in q` also rejects a semicolon inside a string. The authorizer itself is good.
**Fix:** `conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)` with a 2 s deadline.

### WR-09: Customer text goes to a third-party LLM with only phone numbers hidden
**Files:** `src/insights/signals.py:59-63,86`, `src/chat/store.py:272-280`
**Issue:** `redact` masks phone numbers only. `_excerpt` sends the last 4 messages of real chats to Groq. The code comment at `signals.py:86` says the last message is often just a name and address.
**Fix:** Mask names and addresses, or send only the matched keyword line. Or state this in the write-up.

### WR-10: Chats still in progress are counted as "no sale" in the chat answers
**Files:** `src/chat/store.py:314,319-323`, `src/insights/signals.py:98`
**Issue:** `reasons` is built from `no_sales + unknown`. "Unknown" means active/stuck_pending/adversarial, which scoring rightly excludes. Counts like "5 of 23 chats without a sale mention price" therefore include open chats.
**Fix:** Use `no_sales` only for the "why no sale" counts, and show unknown separately.

### WR-11: The reason tags have substring false positives
**File:** `src/insights/signals.py:32,40-42,47-49`
**Issue:** `r"ريح"` also matches "مريح" and "تريح" (comfortable), so positive chats get tagged as quality complaints. Any mention of "cancel" or "refund" becomes `changed_mind`, even in a complaint. `review` and `later` in THINKING are very broad. The counts are quoted to the merchant as numbers.
**Fix:** Use word boundaries or a word list for Arabic, and test the rules against 30 labelled chats.

### WR-12: Budget can go unallocated, and 70% can land on one entity
**Files:** `src/decision/allocation.py:141-160`, `src/decision/explore_selection.py:152-155`
**Issue:** If there are no scale candidates, the 70% exploit share is never given to anyone. With no tests and no proposals, the 30% is dropped (`if total_tests else 0.0`). If every weight is 0 (`total_weight == 0`), no share is assigned. Shares then silently sum to less than 100%. Today's run sums to exactly 1.00 (checked), so it is latent. That same run puts 70% of the budget on one adset and one ad, with no cap.
**Fix:** Give an empty bucket's share to the other bucket (or to a "reserve" line) and write a note. Add a per-entity cap.

### WR-13: The documented commands fail, and the pipeline input can't be rebuilt
**Files:** `scripts/split.py:20-28`, `scripts/split_validation.py:30`, `README.md:126-127`, `config.yaml:10-11`
**Issue:** I ran an import of `scripts.split` and it fails with `ModuleNotFoundError: No module named 'split_lib'`. The README says `python3 -m scripts.split`. `split.py` also needs four required arguments the README does not show. `config.yaml` reads `data/train/full_train.json` and `meta_full_train.json`, and no script creates them. `split.py` writes `train.json` and `split_validation.py` deletes it in place. So the pipeline's input cannot be regenerated from the original data with the documented steps.
**Fix:** Use `from scripts.split_lib import ...` (or relative imports), add a `scripts/make_full_train.py` (or a flag), and make `split_validation.py` write to new files instead of overwriting.

### WR-14: The "out-of-sample" Brier check still leaks
**File:** `scripts/time_split_brier.py:66-67,74,95`
**Issue:** The docstring admits the parent baseline uses all data. It does not say that the prior strengths (`estimate_prior_strength` on all ads and adsets, lines 66-67) also use the late halves, and the same customers appear in both halves. "Honest, OUT-OF-SAMPLE" is too strong.
**Fix:** Fit everything on the early part only, or call it "mostly out-of-sample".

---

## Info

### IN-01: Almost no tests
**File:** `tests/scoring_pipeline_demo.py`
Only a print demo with no assertions. Nothing tests the split, the decision rules, the guard, or the crash cases above.

### IN-02: The whole pipeline is a script body
**Files:** `src/pipeline.py:34-39,188-389`, `src/scoring/classifier.py:9-18`, `src/scoring/corrector.py:8`, `src/decision/*.py`
All logic sits under `if __name__ == "__main__":` and the output paths use the import time. About ten modules read config at import. `classifier.py` opens `products.json` at import. This is hard to test and to re-run with another config. Move it into `run(config_path)`.

### IN-03: Code placed after the `__main__` block
**Files:** `src/decision/guardrails.py:122-176`, `src/ingestion/meta_loader.py:226-253`
`MetaSignals`, `meta_signals`, `describe_signals` and `typical_campaign_days` are defined below the `if __name__` block, so running those files directly fails. `meta_signals` repeats the CTR logic in `fatigue_check`.

### IN-04: Dead and duplicate code
`stop_rules.resolves_in_time` (57-72) duplicates `resolution_check`. `build_stop_rule` computes `cpa` twice (93, 99). The config keys `allocation.stop_rule.max_days` and `confidence` (`config.yaml:69-74`) are never read. `gate.py:192-198` has a "not_yet" reply saying it can't read chats, but it now can. `ui/data.py:15` has an unused duplicate import. `audit.py:11-14` imports loaders only for its `__main__` block. `app_legacy.py` (369 lines) duplicates the old app.

### IN-05: Inconsistent imports
`joiner.py:3-4`, `meta_loader.py:4`, `conversation_loader.py:4` use `from src....`, the rest use relative imports. Private names are imported across modules (`_summarize` in `new_tests.py:25`, `_detect_language` in `bot.py:26`).

### IN-06: Magic values and stale numbers
The fallback baseline `0.5` appears four times in `pipeline.py` (220, 226, 237, 241) and in `tune_threshold.py`. The city map is hard-coded (`customers.py:16-21`). `new_tests.py:10-12` states "14 of those 25 combinations", which will go stale.

### IN-07: File handling
`eval_gate.py:39,173` leave files open. `reporting.py:99,126` and `reasoning/llm.py:119,127` open text files with no `encoding`, which breaks Arabic text on Windows defaults.

### IN-08: Repo hygiene and startup errors
Tracked but ignored: 4 `.DS_Store`, 3 `outputs/gate_eval_*.json`, `trace.db-journal`. About 24 `outputs/plan_*.json` are tracked. `_ensure_schema` (`trace_db.py:599-605`) drops all tables on a schema bump, so run history disappears despite `keep_runs`. `EntityIndex.from_db` (`entities.py:71`) and `ui/data._q` crash with a raw traceback if the pipeline has not run. Db paths are relative to the working directory, so `streamlit run` from another folder breaks.

---

_Reviewed: 2026-10-07_
_Depth: deep (read in full for most files; see Scope)_
