# CTWA Marketing Intelligence Layer

Meta ads send customers into WhatsApp. Meta sees the click and stops there.
Everything after it — the chat, the order, the refund, the silence — is
first-party data sitting unused in the logs. This turns that into a decision
for every campaign, audience and creative: **scale, hold, or stop**, plus a
split of the next budget between what is proven and what is worth finding out.

```
python3 -m pip install -r requirements.txt     # Python 3.10+
python3 -m scripts.split                       # data/original → data/train + data/holdout
python3 -m src.pipeline                        # scores everything, writes outputs/
streamlit run app.py                           # merchant app; reads the newest run
```

`data/` and `outputs/` are not in the repo. Put `conversations.json`,
`meta_data.json` and `products.json` in `data/original/` first.

The pipeline needs no API key. A Groq key in `.env` (`GROQ_API_KEY`) only adds
written explanations and the chat; without it both fall back to templates.

---

## What comes out

Every run writes three things to `outputs/`.

| file | what it is |
| --- | --- |
| `scoreboard_<run>.csv` | one row per id, all three levels. The deliverable |
| `plan_<run>.json` | the same rows plus what has no id to sit on: budget totals, proposed tests, held-back items, audit findings |
| `trace.db` | SQLite. Every input, 12-step trail and decision per id. **The app and the chat read this** |

Scoreboard fields:

| field | meaning |
| --- | --- |
| `level`, `id`, `name`, `detail` | which thing this is (goal / audience / creative) |
| `raw_rate` | sales ÷ conversations that reached an outcome |
| `score` | that rate pulled toward its parent when the data is thin. **Decisions use this** |
| `interval_low/high` | 90% range for the true rate |
| `action` | `scale` · `hold` · `kill` |
| `bucket` | `exploit` · `explore` · `kill` · `none` — where the money goes |
| `budget_share` | share of the next budget |
| `why` | the evidence trail, including the Meta signal |
| `reasoning` | one plain sentence: what to do and why |
| `hypothesis`, `stop_rule` | explore rows only |

`trace.db` keeps the last `trace_db.keep_runs` runs (default 10). The CSV and
JSON files are never pruned.

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
                       scoreboard.csv · plan.json · trace.db ─► app.py · chat
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

Every threshold lives in `config.yaml`. Nothing is hardcoded in `src/`.

---

## Layers

Each layer talks only to the one below it. The full diagram is in [`ARCHITECTURE.md`](ARCHITECTURE.md) ([image](ARCHITECTURE.png)).

---

## The pieces

```
src/
  config.py            loads config.yaml, the single source of truth
  ingestion/           loading and joining; conv.source.ad_id → ad → adset → campaign
  scoring/
    classifier.py      did this conversation end in a sale?
    aggregator.py      successes / failures / still-open, per id
    corrector.py       hierarchical Beta-Binomial shrinkage; fits its own prior strength
    amounts.py         net revenue per conversation
    revenue.py         money per level
  decision/
    outcome_decision.py  decide(): P(rate > baseline) vs a threshold, gated on real n
    guardrails.py        fatigue and cost-per-order checks, and the Meta signal text
    action.py            applies the guardrail vetoes to a raw decision
    allocation.py        the 70/30 split
    explore_selection.py which candidates get the limited explore slots
    stop_rules.py        spend and days before a test can be called
    new_tests.py         proposes audience × creative pairings never run
  auditing/
    audit.py           spend, ROAS, baseline CPA. Never feeds a decision
    findings.py        where the decision and the money disagree
  insights/            read from the chats by rules, no LLM; never feed a decision
    signals.py         why a chat did not end in a sale, with the exact message as evidence
    customers.py       customer segments across cycles, by customer id only
    money.py           revenue against spend, with the "only these chats" caveat
  reasoning/           text only
    llm.py             shared client, cache, fallback
    reasoning.py       one sentence per decision
    hypothesis.py      what each explore test is really asking
  chat/                the merchant chatbot (see below)
  storage/trace_db.py  trace.db schema and writer
  trace.py             hands finished results to trace_db; decides nothing
  reporting.py         writes the CSV and JSON, prints the scoreboard
  ui/                  data loaders, pages and chat panel for the app
  pipeline.py          runs all of it
app.py                 wires the four pages; computes nothing
```

---

## The app

`streamlit run app.py` reads `outputs/trace.db`. Re-running the pipeline
refreshes it without a restart. Nothing in the app computes a decision.

| page | what it is for |
| --- | --- |
| Dashboard | decisions only: what to scale, hold, stop |
| Explore | filter and search every id |
| Report | one id, every step behind its decision, chat on the side |
| Ask | general chat |

---

## The chat

Answers a merchant's questions about decisions that already exist. It explains;
it never makes a new decision or a prediction.

```
question
   │
   ▼
entities.py   which campaign / adset / ad is this about?   (no AI)
   │
   ▼
gate.py       what kind of question?   answer · refuse · clarify · not yet
   │
   ▼
answer.py     LLM with read-only tools:
   │            get_entity · run_sql · get_conversations · get_customers · glossary
   ▼
guard.py      numbers, actions, no advice or predictions   (code, no AI)
   │            fail → one rewrite → fail → deterministic reply from the stored decision
   ▼
reply         every step logged to outputs/chat_log.db
```

`budget.py` tracks tokens per minute per model and says "busy, try again in
N s" before calling, rather than failing halfway. With no key, the gate becomes
keyword matching and replies come from stored facts (`chat/fallback/`).

---

## Splitting the data

Never randomly, never by conversation — repeat customers would land on both
sides. Everything splits by campaign start date.

```
all campaigns
   ├── holdout      start_date >= split.holdout_cutoff_date     touched once, at the very end
   └── train        (data/train/full_train.json)
        ├── conv_val    start_date >= split.validation_cutoff_date   tuning lives here
        └── conv_train                                              everything fits on this
```

```
python3 -m scripts.split             # original → train / holdout
python3 -m scripts.split_validation  # train → conv_train / conv_val
```

`python3 -m src.pipeline` scores `full_train` (conv_train + conv_val), the
holdout is never read by it (see `data:` in `config.yaml`).

`always_on` campaigns are excluded from both cutoffs: they run the whole
timeline, so ordering them by start date would put them in the earliest bucket
regardless of when their conversations happened. Organic and direct
conversations carry no ad id, can never be scored, and are never eligible.

`data/` and `outputs/` are git-ignored. They hold customer names and phones, and chat logs.

---

## Evaluation

`eval_gate` and `eval_answers` read the labelled questions from `data/chat_eval/gate_questions.json`, which stays local like the rest of `data/`.

All offline unless noted. None of them touch the holdout, except where a
script says `--split test`, which is run once at the end.

| script | checks |
| --- | --- |
| `scripts.tune_threshold` | the scale/kill probability threshold, on conv_val |
| `scripts.time_split_brier` | does shrinkage beat the raw rate on what an ad does next (first half of its chats → second half) |
| `scripts.eval_gate` | chat gate: intent accuracy, unsafe answers, over-refusals, confidence calibration |
| `scripts.eval_answers` | the chat's written answers end to end: routing, guard pass rate, fallback rate |
| `scripts.eval_guard` | the answer guard as a classifier, on injected errors. No key needed |
| `scripts.check_llm` | health check when the chat is slow or falls back |
| `scripts.inspect` | one id's full chain, for debugging |

---

## The reasoning layer

An LLM writes the explanation and the test hypotheses. It never decides
anything: `action`, `bucket`, `score` and the budget arrive finished and it
only puts them into words. Delete `src/reasoning/` and the scoreboard is
identical.

Without `GROQ_API_KEY` every row falls back to a deterministic template and the
pipeline runs offline, unchanged. Each run prints where its sentences came from:

```
Reasoning: 14 llm, 24 template:not_funded
```

Generations are cached on a hash of (facts, prompt, model), so unchanged data
re-uses its sentence and an edited prompt invalidates itself. Provider, model
and rate limits are under `reasoning:` and `chat:` in `config.yaml`. Any
OpenAI-compatible endpoint works with a `base_url` and `model` change.

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
- Revenue and ROAS cover only the chats in the data, not the merchant's full return.
- 8 of 9 running explore tests cannot reach a verdict at this account's
  conversation rate — the entities sit 1–3 points apart and would need hundreds
  of days to separate. The stop rules say so per test.
- `decision.probability_threshold = 0.75` is not yet validated against holdout.
- A proposed test is a margins estimate, not a prediction. The interaction
  between an audience and a creative is precisely what it is buying.
- The chat answer guard checks that every number, quote and action appears in
  the stored facts. It cannot tell that a real number belongs to the wrong
  field (e.g. 12 ghosted chats written as 12 resolved chats). On injected errors
  it catches 538 of 539 (`scripts/eval_guard.py`) but 0 of 48 such swaps.
  Answers are also not checked for completeness or tone. Faithfulness of the
  written answers was graded by hand on a sample (`scripts/eval_answers.py`).
- The gate's self-reported confidence is not assumed calibrated;
  `scripts/eval_gate.py` measures how well it tracks accuracy.
