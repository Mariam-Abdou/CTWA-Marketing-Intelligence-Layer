# CTWA Marketing Intelligence Layer

Meta ads send customers into WhatsApp. Meta sees the click and stops there.
Everything after it — the chat, the order, the refund, the silence — is
first-party data sitting unused in the logs. This turns that into a decision
for every campaign, audience and creative: **scale, hold, or stop**, plus a
split of the next budget between what is proven and what is worth finding out.

```
python3 -m pip install -r requirements.txt
python3 -m src.pipeline      # writes outputs/scoreboard_*.csv and outputs/plan_*.json
streamlit run app.py         # reads the newest run
```

---

## What comes out

**`outputs/scoreboard_<run>.csv`** — one row per id, at all three levels.

| field | meaning |
| --- | --- |
| `level`, `id`, `name`, `detail` | which thing this is, and what kind |
| `raw_rate` | sales ÷ conversations that reached an outcome |
| `score` | that rate pulled toward its parent when the data is thin. **Decisions use this** |
| `interval_low/high` | 90% range for the true rate |
| `action` | `scale` · `hold` · `kill` |
| `bucket` | `exploit` · `explore` · `kill` · `none` — where the money goes |
| `budget_share` | share of the next budget |
| `why` | the evidence trail, including the Meta signal |
| `reasoning` | one plain sentence: what to do and why |
| `hypothesis`, `stop_rule` | explore rows only |

**`outputs/plan_<run>.json`** — the same rows plus what has no id to sit on:
budget totals, proposed tests, what got held back and why, and the audit findings.

---

## How a decision is made

```
conversations.json ─┐
                    ├─► joiner ─► classifier ─► aggregator ─► corrector ─► decide()
meta_data.json ─────┘              (sale?)      (counts)     (shrinkage)   (scale/hold/kill)
                                                                              │
                                        ┌─────────────────────────────────────┤
                                        ▼                                     ▼
                                  guardrails                            allocation
                              fatigue · cost-per-order                  70% exploit
                              (can only VETO a scale)                   30% explore
                                        │                                     │
                                        └──────────────┬──────────────────────┘
                                                       ▼
                                            reasoning layer (LLM)
                                          explains, never decides
                                                       ▼
                                             scoreboard.csv · plan.json · app.py
```

### The boundary that never moves

The score and the scale/hold/kill call come from WhatsApp outcomes **only**.
Meta data can do two things and no more: **veto** a scale (fatigue, cost per
order), and **weight** the budget split by ROAS. It can never raise a decision,
and never enters the probability.

### Static skeleton, dynamic muscle

Built once and unchanged: the ingest and join, the decision rules, the 70/30
split, the output schema, the evaluation method.

Re-fit every run from all data so far: the shrinkage prior strength
(`corrector.estimate_prior_strength`, method of moments on that level's own
rates), the baselines, the ROAS benchmarks, the test horizon.

The contract between them is a calibrated score plus an interval. Because that
shape never changes, a predictor can be retrained or swapped without touching
anything downstream.

---

## The pieces

```
src/
  config.py          config.yaml is the single source of truth for every threshold
  ingestion/         loading and joining; conv.source.ad_id → ad → adset → campaign
  scoring/
    classifier.py    did this conversation end in a sale?
    aggregator.py    successes / failures / still-open, per id
    corrector.py     hierarchical Beta-Binomial shrinkage; fits its own prior strength
    revenue.py       what each conversation was worth
  decision/
    conversation.py  decide(): P(rate > baseline) vs a threshold, gated on real n
    meta.py          fatigue and cost-per-order guardrails, and the Meta signal text
    allocation.py    the 70/30 split, per-test stop rules, explore ranking
    new_tests.py     proposes audience × creative pairings never run
    action.py        applies the guardrail vetoes to a raw decision
  auditing/
    audit.py         spend, ROAS, baseline CPA
    findings.py      where the decision and the money disagree
  reasoning/
    llm.py           shared client, cache, and fallback
    narrator.py      one sentence per decision
    hypothesis.py    what each explore test is really asking
  pipeline.py        runs all of it
app.py               reads the newest run; computes nothing
```

---

## Splitting the data

Never randomly, never by conversation — repeat customers would land on both
sides. Everything splits by campaign start date.

```
all campaigns
   ├── holdout      start_date >= split.holdout_cutoff_date     touched once, at the very end
   └── train
        ├── conv_val    start_date >= split.validation_cutoff_date   tuning lives here
        └── conv_train                                              everything fits on this
```

```
python3 -m scripts.split             # original → train / holdout
python3 -m scripts.split_validation  # train → conv_train / conv_val
```

`always_on` campaigns are excluded from both cutoffs: they run the whole
timeline, so ordering them by start date would put them in the earliest bucket
regardless of when their conversations happened. Organic and direct
conversations carry no ad id, can never be scored, and are never eligible.

---

## The reasoning layer

An LLM writes the explanation and the test hypotheses. It never decides
anything: `action`, `bucket`, `score` and the budget arrive finished and it
only puts them into words. Delete `src/reasoning/` and the scoreboard is
identical.

Set `GROQ_API_KEY` in `.env`. Without it every row falls back to a deterministic
template and the pipeline runs offline, unchanged. Each run prints where its
sentences came from:

```
Reasoning: 14 llm, 24 template:not_funded
```

Generations are cached on a hash of (facts, prompt, model), so unchanged data
re-uses its sentence and an edited prompt invalidates itself.

**Known limitation.** An earlier version checked every number in the output
against the facts. It rejected four correct sentences and caught no invented
one, and each fix to it was another exception; meanwhile every wrong claim we
actually saw contained no number at all. It was removed. Nothing now stops a
fabricated figure reaching the `reasoning` column. The `why` column beside it is
computed and safe.

---

## What this does not know

- One merchant, 180 days, three seasonal cycles. Numbers are upper bounds.
- Nothing about stock, deliveries, walk-ins, or anything outside the chat.
- 8 of 9 running explore tests cannot reach a verdict at this account's
  conversation rate — the entities sit 1–3 points apart and would need hundreds
  of days to separate. The stop rules say so per test.
- `PROBABILITY_THRESHOLD = 0.75` is not yet validated against holdout.
- A proposed test is a margins estimate, not a prediction. The interaction
  between an audience and a creative is precisely what it is buying.
- The chat answer guard checks that every number, quote and action appears in
  the stored facts. It cannot tell that a real number belongs to the wrong
  field (e.g. 12 ghosted chats written as 12 resolved chats). On injected errors
  it catches 538 of 539 (`scripts/eval_guard.py`) but 0 of 48 such swaps.
  Answers are also not checked for completeness or tone. Faithfulness of the
  written answers was graded by hand on a sample (`scripts/eval_answers.py`).
