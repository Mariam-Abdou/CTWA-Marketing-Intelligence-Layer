# CTWA Marketing Intelligence Layer

Meta ads send customers into WhatsApp. Meta sees the click and stops there.
Everything after it (the chat, the order, the refund, the reply) is
first-party data sitting unused in the logs. This turns that into a decision
for every campaign, audience and creative: **scale, hold, or stop**, plus a
split of the next budget between what is proven and what is worth finding out.

---

## Outputs

| file | what it is |
| --- | --- |
| `scoreboard.csv` | one row per id, all three levels. The deliverable |
| `plan.json` | budget totals, proposed tests, held-back items, audit findings |
| `trace.db` | SQLite. Every input, all steps trail and decision per id. |

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
                                                                        30% explore
                                        │                                     │
                                        └──────────────┬──────────────────────┘
                                                       ▼
                                            reasoning layer (LLM)
                                          explains, never decides
                                                       ▼
                       scoreboard.csv · plan.json · trace.db ─► app.py · chat
```

---

## Architecture

The full diagram is in [`ARCHITECTURE.md`](ARCHITECTURE.md) ([image](https://github.com/Mariam-Abdou/CTWA-Marketing-Intelligence-Layer/blob/main/ARCHITECTURE.png)).

---

## Project structure

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

## Evaluation

`eval_gate` and `eval_answers` read the labelled questions from `data/chat_eval/gate_questions.json`, which stays local like the rest of `data/`.

All offline unless noted. None of them touch the holdout, except where a
script says `--split test`, which is run once at the end.

| script | checks |
| --- | --- |
| `scripts.eval_gate` | chat gate: intent accuracy, unsafe answers, over-refusals, confidence calibration |
| `scripts.eval_answers` | the chat's written answers end to end: routing, guard pass rate, fallback rate |
| `scripts.eval_guard` | the answer guard as a classifier, on injected errors. No key needed |

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
