"""
Decision-trace database: everything behind every decision, stored by id.

Written once at the end of each pipeline run (src/pipeline.py). Nothing here
computes a decision -- every number arrives already computed by the pipeline
and is only stored. The app and the chatbot read this file; they never
re-derive anything.

Layout (one SQLite file, every table keyed by run_id so runs never mix):

    runs                  one row per run: data paths, config snapshot,
                          run-level numbers (thresholds, fitted priors,
                          baselines), is_current flag
    entities              every campaign / adset / ad, with its parents
    conversations         every conversation in the input file + how it was
                          classified (outcome, net amount, success/excluded)
    entity_conversations  which conversations count toward which id
    meta_objects          raw campaign / adset / ad / creative records
    entity_meta           which meta records relate to which id, and how
    daily_insights        raw Meta daily rows
    entity_daily_series   the exact day-by-day series the guardrails read
                          for that id (adset/campaign = impression-weighted
                          roll-up of their ads)
    products              the product catalog
    decision_steps        one row per step per id: inputs, outputs, the rule
                          that fired, whether the step changed the outcome
    decisions             the final row per id (what the scoreboard shows)
    proposed_tests        never-run audience x creative tests (no id yet)
    entity_bundles        everything above for one id, as one JSON document:
                          {entity, conversations, meta, steps, decision}

Read helpers at the bottom: current_run_id(), get_bundle(), list_entities().
"""

import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, generated_at TEXT, is_current INTEGER,
    conversations_path TEXT, meta_path TEXT, products_path TEXT,
    scoreboard_csv TEXT, plan_json TEXT,
    config_json TEXT, run_params_json TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    run_id TEXT, level TEXT, entity_id TEXT, name TEXT, detail TEXT,
    campaign_id TEXT, adset_id TEXT, parent_id TEXT, scored INTEGER,
    PRIMARY KEY (run_id, entity_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    run_id TEXT, conv_id TEXT, customer_id TEXT, started_at TEXT, cycle INTEGER,
    platform TEXT, ad_id TEXT, adset_id TEXT, campaign_id TEXT, join_status TEXT,
    outcome_type TEXT, net_amount REAL, success INTEGER, classification TEXT,
    -- read from the messages by rules (src/insights/signals.py)
    gross_amount REAL, refunded_amount REAL, city TEXT,
    message_count INTEGER, last_sender TEXT, unanswered_question INTEGER,
    reason TEXT, reason_evidence TEXT, ordered_products_json TEXT,
    raw_json TEXT,
    PRIMARY KEY (run_id, conv_id)
);
CREATE TABLE IF NOT EXISTS entity_conversations (
    run_id TEXT, entity_id TEXT, conv_id TEXT,
    PRIMARY KEY (run_id, entity_id, conv_id)
);
CREATE TABLE IF NOT EXISTS meta_objects (
    run_id TEXT, kind TEXT, object_id TEXT, raw_json TEXT,
    PRIMARY KEY (run_id, kind, object_id)
);
CREATE TABLE IF NOT EXISTS entity_meta (
    run_id TEXT, entity_id TEXT, kind TEXT, object_id TEXT, relation TEXT
);
CREATE TABLE IF NOT EXISTS daily_insights (
    run_id TEXT, ad_id TEXT, adset_id TEXT, campaign_id TEXT, date TEXT,
    impressions INTEGER, clicks INTEGER, spend REAL, frequency REAL, ctr REAL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS entity_daily_series (
    run_id TEXT, entity_id TEXT, date TEXT,
    impressions INTEGER, spend REAL, frequency REAL, ctr REAL
);
CREATE TABLE IF NOT EXISTS products (
    run_id TEXT, product_id TEXT, name TEXT, category TEXT, price REAL, raw_json TEXT,
    PRIMARY KEY (run_id, product_id)
);
CREATE TABLE IF NOT EXISTS decision_steps (
    run_id TEXT, entity_id TEXT, step_no INTEGER, step_key TEXT, title TEXT,
    applied INTEGER, changed_outcome INTEGER, rule TEXT,
    inputs_json TEXT, outputs_json TEXT,
    PRIMARY KEY (run_id, entity_id, step_no)
);
CREATE TABLE IF NOT EXISTS decisions (
    -- One flat row per scored id: the final decision AND the key numbers
    -- behind it. Every value is copied from decision_steps (never
    -- recomputed), so this table and the steps cannot disagree.
    run_id TEXT, level TEXT, entity_id TEXT, name TEXT, detail TEXT,
    parent_id TEXT, campaign_id TEXT, adset_id TEXT,
    -- step 1: evidence
    conversations INTEGER, successes INTEGER, failures INTEGER, excluded INTEGER, raw_rate REAL,
    -- steps 2-4: score and statistical decision
    baseline REAL, baseline_source TEXT, prior_strength REAL,
    score REAL, interval_low REAL, interval_high REAL,
    p_better REAL, p_worse REAL, decision_rule TEXT, raw_action TEXT,
    -- step 5: money
    spend REAL, revenue REAL, sales INTEGER, resolved_conversations INTEGER,
    roas_raw REAL, roas_shrunk REAL, cost_per_sale REAL,
    baseline_cost_per_sale REAL, level_median_roas REAL,
    -- steps 6-8: guardrails and final action
    recent_frequency REAL, ctr_drop REAL,
    fatigue_checked INTEGER, fatigued INTEGER, frequency_warning INTEGER,
    cpa_checked INTEGER, underperforming INTEGER,
    action TEXT, held_back_by TEXT,
    -- step 9: allocation
    bucket TEXT, budget_share REAL, explore_outcome TEXT, explore_rank INTEGER,
    -- steps 10-12: text
    why_json TEXT, reasoning TEXT, reasoning_source TEXT,
    hypothesis TEXT, hypothesis_source TEXT, stop_rule TEXT,
    finding_count INTEGER, findings_json TEXT,
    PRIMARY KEY (run_id, entity_id)
);
CREATE TABLE IF NOT EXISTS level_totals (
    -- Sums over the decisions table, per level. Totals across a level count
    -- each conversation once (campaign level = the whole account).
    run_id TEXT, level TEXT, entities INTEGER,
    spend REAL, revenue REAL, roas REAL, sales INTEGER, resolved_conversations INTEGER,
    cost_per_sale REAL, scale_count INTEGER, hold_count INTEGER, kill_count INTEGER,
    exploit_share REAL, explore_share REAL,
    PRIMARY KEY (run_id, level)
);
CREATE TABLE IF NOT EXISTS proposed_tests (
    run_id TEXT, level TEXT, test_id TEXT, name TEXT, audience_type TEXT, theme TEXT,
    budget_share REAL, expected_rate REAL, hypothesis TEXT, hypothesis_source TEXT,
    stop_rule TEXT, details_json TEXT,
    PRIMARY KEY (run_id, test_id)
);
CREATE TABLE IF NOT EXISTS entity_bundles (
    run_id TEXT, level TEXT, entity_id TEXT, bundle_json TEXT,
    PRIMARY KEY (run_id, entity_id)
);
CREATE INDEX IF NOT EXISTS ix_ec ON entity_conversations (run_id, entity_id);
CREATE INDEX IF NOT EXISTS ix_em ON entity_meta (run_id, entity_id);
CREATE INDEX IF NOT EXISTS ix_eds ON entity_daily_series (run_id, entity_id);
CREATE INDEX IF NOT EXISTS ix_di ON daily_insights (run_id, ad_id);
CREATE VIEW IF NOT EXISTS current_decisions AS
    SELECT d.* FROM decisions d JOIN runs r USING (run_id) WHERE r.is_current = 1;
CREATE VIEW IF NOT EXISTS current_steps AS
    SELECT s.* FROM decision_steps s JOIN runs r USING (run_id) WHERE r.is_current = 1;
"""

# Bump when a table's columns change: an older database is rebuilt from
# scratch on the next run instead of failing on a missing column.
SCHEMA_VERSION = 3

PER_RUN_TABLES = [
    "level_totals", "entities", "conversations", "entity_conversations", "meta_objects", "entity_meta",
    "daily_insights", "entity_daily_series", "products", "decision_steps", "decisions",
    "proposed_tests", "entity_bundles", "runs",
]


def _connect(db_path):
    """TRUNCATE journal: SQLite empties its journal file instead of deleting
    it after each commit. Same safety as the default, and it also works on
    folders where deleting files is not allowed (synced / sandboxed folders)."""
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=TRUNCATE")
    return db


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_default)


def _default(o):
    if hasattr(o, "__dataclass_fields__"):
        return {k: getattr(o, k) for k in o.__dataclass_fields__}
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _conversation_products(raw: dict) -> list[str]:
    ids = [li.get("product_id") for li in (raw.get("outcome") or {}).get("line_items", []) or []]
    for m in raw.get("messages", []) or []:
        ids.extend(m.get("products") or [])
    return [i for i in ids if i]


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def _step(no, key, title, *, applied=True, changed=False, rule="", inputs=None, outputs=None):
    return {"step_no": no, "step_key": key, "title": title, "applied": applied,
            "changed_outcome": changed, "rule": rule,
            "inputs": inputs or {}, "outputs": outputs or {}}


def build_steps(*, level, entity_id, row, rate, posterior, prior_info, plan_trace,
                roas_detail, money_context, findings, conv_counter, min_success_amount):
    """The ordered decision trail for one id. Every value comes from the
    objects the pipeline used; nothing is recomputed here."""
    t = plan_trace or {}
    steps = []

    # 1. conversations -> successes / failures / excluded
    steps.append(_step(
        1, "conversations", "Count and classify this id's conversations",
        rule=(f"A conversation is a success if its net amount (total minus refunds) is at least "
              f"the cheapest product price ({min_success_amount}). stuck_pending / active / "
              f"adversarial are excluded: their outcome is not known yet."),
        inputs={"conversations_linked": sum(conv_counter.values()),
                "outcome_types": dict(conv_counter),
                "min_success_amount": min_success_amount},
        outputs={"successes": rate.successes, "failures": rate.failures,
                 "resolved_n": rate.n, "excluded": rate.excluded, "raw_rate": rate.raw_rate},
    ))

    # 2. prior
    if level == "campaign":
        steps.append(_step(
            2, "prior", "Choose the prior (what we believe before this id's own data)",
            rule="Top level has no parent to borrow from: uninformative Jeffreys prior Beta(0.5, 0.5).",
            inputs={"parent": None},
            outputs={"alpha_prior": 0.5, "beta_prior": 0.5, "prior_strength": 1.0},
        ))
    else:
        strength = prior_info["prior_strength_details"]
        steps.append(_step(
            2, "prior", "Choose the prior (borrow strength from the parent)",
            rule=("Prior mean = parent's posterior score; prior strength fitted from how much "
                  f"{level}s really differ beyond sampling noise (method of moments)."),
            inputs={"parent_level": prior_info["parent_level"], "parent_id": prior_info["parent_id"],
                    "parent_score": prior_info["parent_score"],
                    "prior_strength_fit": strength},
            outputs={"prior_strength": strength["prior_strength"],
                     "alpha_prior": posterior.alpha_prior if posterior else None,
                     "beta_prior": posterior.beta_prior if posterior else None},
        ))

    # 3. posterior
    if posterior is None:
        steps.append(_step(3, "posterior", "Combine prior and data into a score", applied=False,
                           rule="No resolved conversations: no posterior, no score.",
                           outputs={"score": None}))
    else:
        steps.append(_step(
            3, "posterior", "Combine prior and data into a score",
            rule="Beta-Binomial update: alpha_post = alpha_prior + successes, beta_post = beta_prior + failures. "
                 "Score = posterior mean; 90% credible interval.",
            inputs={"alpha_prior": posterior.alpha_prior, "beta_prior": posterior.beta_prior,
                    "successes": rate.successes, "failures": rate.failures},
            outputs={"alpha_post": posterior.alpha_post, "beta_post": posterior.beta_post,
                     "score": posterior.score, "interval_low": posterior.interval_low,
                     "interval_high": posterior.interval_high, "credible_level": 0.90,
                     "precision": posterior.precision},
        ))

    # 4. statistical decision vs baseline
    steps.append(_step(
        4, "decision", "Compare against the baseline: scale / hold / kill",
        applied=posterior is not None,
        rule=t.get("rule", ""),
        inputs={"baseline": t.get("baseline"), "baseline_source": prior_info["baseline_source"],
                "n": t.get("n"), "probability_threshold": money_context["probability_threshold"],
                "min_n_for_action": money_context["min_n_for_action"]},
        outputs={"p_better": t.get("p_better"), "p_worse": t.get("p_worse"),
                 "raw_action": t.get("raw_action")},
    ))

    # 5. money
    steps.append(_step(
        5, "money", "Money: spend, revenue, ROAS, cost per sale",
        rule=("Context and guardrail input, never part of the score. ROAS is shrunk toward the "
              "portfolio mean so a few conversations cannot swing it."),
        inputs={"spend": (roas_detail or {}).get("spend"), "revenue": (roas_detail or {}).get("revenue"),
                "sales": (roas_detail or {}).get("sales")},
        outputs={**(roas_detail or {}),
                 "cost_per_sale": (t.get("cpa") or {}).get("cost_per_sale"),
                 "baseline_cost_per_sale": money_context["baseline_cpa"],
                 "level_median_roas": money_context["median_roas"]},
    ))

    # 6. fatigue guardrail
    fat = t.get("fatigue") or {}
    steps.append(_step(
        6, "fatigue_guardrail", "Fatigue guardrail (Meta frequency + CTR trend)",
        applied=t.get("fatigue_applied", False),
        changed=bool(t.get("fatigue_applied") and fat.get("fatigued")),
        rule=("Only checked when the raw action is scale. Vetoes scale if recent frequency >= "
              f"{fat.get('frequency_threshold')} AND CTR dropped >= {fat.get('ctr_drop_threshold')} "
              "vs the first week."),
        inputs={k: fat.get(k) for k in ("days", "window_days", "recent_avg_frequency",
                                        "first_window_avg_ctr", "last_window_avg_ctr",
                                        "frequency_threshold", "warning_frequency_threshold",
                                        "ctr_drop_threshold")},
        outputs={"ctr_drop": fat.get("ctr_drop"), "fatigued": fat.get("fatigued"),
                 "frequency_warning": fat.get("frequency_warning"), "reason": fat.get("reason")},
    ))

    # 7. CPA guardrail
    cpa = t.get("cpa") or {}
    fatigue_vetoed = bool(t.get("fatigue_applied") and fat.get("fatigued"))
    steps.append(_step(
        7, "cpa_guardrail", "Cost-per-sale guardrail",
        applied=t.get("cpa_applied", False),
        changed=bool(t.get("cpa_applied") and cpa.get("underperforming")),
        rule=("Only checked when the raw action is scale and fatigue did not already veto it. "
              "Once spend passes the judging line, vetoes scale if there are zero sales or "
              "the cost per sale is above the stop line (stop_multiplier x the level's baseline cost per sale)."
              + (" Skipped: fatigue already vetoed." if fatigue_vetoed else "")),
        inputs={k: cpa.get(k) for k in ("spend", "sales", "baseline_cost_per_sale", "min_spend_multiplier",
                                        "judge_line", "stop_multiplier", "stop_line")},
        outputs={"cost_per_sale": cpa.get("cost_per_sale"), "underperforming": cpa.get("underperforming"),
                 "reason": cpa.get("reason")},
    ))

    # 8. final action
    raw_action = row.get("raw_action")
    steps.append(_step(
        8, "final_action", "Final action after guardrails",
        changed=raw_action != row["action"],
        rule="Guardrails can only turn scale into hold; they never create a scale or a kill.",
        inputs={"raw_action": raw_action, "fatigued": row.get("is_fatigued"),
                "underperforming": row.get("is_underperforming")},
        outputs={"action": row["action"], "held_back_by": row.get("held_back_by")},
    ))

    # 9. allocation
    alloc_inputs = {"route": t.get("route")}
    if "exploit_weight" in t:
        alloc_inputs["exploit_weight"] = t["exploit_weight"]
    if "explore_selection" in t:
        alloc_inputs["explore_selection"] = t["explore_selection"]
    steps.append(_step(
        9, "allocation", "Budget bucket and share (70/30 split)",
        rule=("Exploit share is split across scaled ids by P(better) x shrunk ROAS. Test-pool ids "
              "must be judgeable within one campaign length, are ranked by interval_high x shrunk "
              "ROAS, and only the top max_explore_tests get an equal share of the explore budget."),
        inputs=alloc_inputs,
        outputs={"bucket": row["bucket"], "budget_share": row["budget_share"],
                 "explore_outcome": (t.get("explore_selection") or {}).get("outcome")},
    ))

    # 10. test plan
    is_test = row["bucket"] == "explore"
    steps.append(_step(
        10, "test_plan", "Test hypothesis and stop rule",
        applied=is_test,
        rule="Only for ids funded as tests. Stop rule is expressed in spend and days, gated on the CPA lines.",
        inputs={"rule_based_hypothesis": row.get("hypothesis_rule_based"),
                "hypothesis_prompt": row.get("hypothesis_prompt")},
        outputs={"hypothesis": row.get("hypothesis"), "hypothesis_source": row.get("hypothesis_source"),
                 "stop_rule": row.get("stop_rule")},
    ))

    # 11. explanation
    steps.append(_step(
        11, "explanation", "Merchant-readable explanation",
        rule=("Written after the decision is final; cannot change it. source = llm | cache | "
              "template:<reason>."),
        inputs={"facts_given": row.get("reasoning_prompt")},
        outputs={"why": row["why"], "reasoning": row.get("reasoning"),
                 "reasoning_source": row.get("reasoning_source")},
    ))

    # 12. audit
    steps.append(_step(
        12, "audit", "Audit: does the decision agree with the money?",
        rule="Flags scale-but-losing-money, kill-but-profitable, and top-quarter earners not being scaled.",
        outputs={"findings": findings},
    ))
    return steps


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def write_run(db_path, *, run_id, generated_at, paths, config, run_params,
              raw_conversations, raw_meta, raw_products, conv_class, conv_links,
              entities, levels, proposed, keep_runs=10):
    """
    paths:          {conversations, meta, products, scoreboard_csv, plan_json}
    conv_class:     conv_id -> {outcome_type, net_amount, success, classification,
                                join_status, ad_id, adset_id, campaign_id}
    conv_links:     entity_id -> [conv_id]
    entities:       list of {level, id, name, detail, campaign_id, adset_id, parent_id, scored}
    levels:         level -> {rows, steps(id->list), series(id->[DailyInsight]), findings(id->list)}
    proposed:       list of dicts
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(db_path)) as db, db:
        _ensure_schema(db)
        db.execute("UPDATE runs SET is_current = 0")
        db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", (
            run_id, generated_at, 1, paths["conversations"], paths["meta"], paths["products"],
            paths["scoreboard_csv"], paths["plan_json"], _j(config), _j(run_params)))

        # raw inputs
        for raw in raw_conversations:
            c = conv_class[raw["id"]]
            sg = c.get("signals") or {}
            db.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, raw["id"], (raw.get("customer") or {}).get("id"), raw.get("started_at"),
                raw.get("cycle"), (raw.get("source") or {}).get("platform"),
                c["ad_id"], c["adset_id"], c["campaign_id"], c["join_status"],
                c["outcome_type"], c["net_amount"],
                None if c["success"] is None else int(c["success"]), c["classification"],
                sg.get("gross_amount"), sg.get("refunded_amount"), sg.get("city"),
                sg.get("message_count"), sg.get("last_sender"),
                None if sg.get("unanswered_question") is None else int(sg["unanswered_question"]),
                sg.get("reason"), sg.get("reason_evidence"), _j(sg.get("ordered_products") or []),
                _j(raw)))
        for kind, key in (("campaign", "campaigns"), ("adset", "adsets"), ("ad", "ads"), ("creative", "creatives")):
            db.executemany("INSERT INTO meta_objects VALUES (?,?,?,?)",
                           [(run_id, kind, o["id"], _j(o)) for o in raw_meta.get(key, [])])
        db.executemany("INSERT INTO daily_insights VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            (run_id, r.get("ad_id"), r.get("adset_id"), r.get("campaign_id"), r.get("date_start"),
             _int(r.get("impressions")), _int(r.get("clicks")), _num(r.get("spend")),
             _num(r.get("frequency")), _num(r.get("ctr")), _j(r))
            for r in raw_meta.get("insights", [])])
        db.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", [
            (run_id, p["id"], p.get("name"), p.get("category"), _num(p.get("price")), _j(p))
            for p in raw_products])

        # per entity
        meta_by_id = {}
        for kind, key in (("campaign", "campaigns"), ("adset", "adsets"), ("ad", "ads"), ("creative", "creatives")):
            for o in raw_meta.get(key, []):
                meta_by_id[(kind, o["id"])] = o
        products_by_id = {p["id"]: p for p in raw_products}
        raw_conv_by_id = {r["id"]: r for r in raw_conversations}

        for e in entities:
            eid, level = e["id"], e["level"]
            db.execute("INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?)", (
                run_id, level, eid, e["name"], e["detail"], e["campaign_id"], e["adset_id"],
                e["parent_id"], int(e["scored"])))

            conv_ids = conv_links.get(eid, [])
            db.executemany("INSERT INTO entity_conversations VALUES (?,?,?)",
                           [(run_id, eid, cid) for cid in conv_ids])

            relations = _meta_relations(level, eid, raw_meta)
            db.executemany("INSERT INTO entity_meta VALUES (?,?,?,?,?)",
                           [(run_id, eid, k, oid, rel) for k, oid, rel in relations])

            lv = levels[level]
            series = lv["series"].get(eid, [])
            db.executemany("INSERT INTO entity_daily_series VALUES (?,?,?,?,?,?,?)", [
                (run_id, eid, i.date_start, i.impressions, i.spend, i.frequency, i.ctr) for i in series])

            row = lv["rows_by_id"].get(eid)
            steps = lv["steps"].get(eid, [])
            db.executemany("INSERT INTO decision_steps VALUES (?,?,?,?,?,?,?,?,?,?)", [
                (run_id, eid, s["step_no"], s["step_key"], s["title"], int(s["applied"]),
                 int(s["changed_outcome"]), s["rule"], _j(s["inputs"]), _j(s["outputs"]))
                for s in steps])

            decision = None
            if row is not None:
                decision = {
                    "score": row["score"],
                    "interval_low": row["interval"][0] if row["interval"] else None,
                    "interval_high": row["interval"][1] if row["interval"] else None,
                    "raw_action": row.get("raw_action"), "action": row["action"],
                    "bucket": row["bucket"], "budget_share": row["budget_share"],
                    "held_back_by": row.get("held_back_by"), "why": row["why"],
                    "reasoning": row.get("reasoning"), "reasoning_source": row.get("reasoning_source"),
                    "hypothesis": row.get("hypothesis"), "hypothesis_source": row.get("hypothesis_source"),
                    "stop_rule": row.get("stop_rule"),
                    "findings": lv["findings"].get(eid, []),
                }
                flat = {"run_id": run_id, **_flat(e, steps, decision)}
                db.execute(f"INSERT INTO decisions ({','.join(flat)}) VALUES ({','.join('?' * len(flat))})",
                           list(flat.values()))

            # bundle: everything about this id in one document
            convs = []
            product_counter = Counter()
            for cid in conv_ids:
                raw = raw_conv_by_id[cid]
                product_counter.update(_conversation_products(raw))
                convs.append({"classification": conv_class[cid], "record": raw})
            outcome_counts = Counter(conv_class[c]["outcome_type"] for c in conv_ids)
            reason_counts = Counter((conv_class[c].get("signals") or {}).get("reason")
                                    for c in conv_ids if (conv_class[c].get("signals") or {}).get("reason"))
            bundle = {
                "entity": {**e, "run_id": run_id},
                "conversations": {
                    "count": len(conv_ids),
                    "outcome_types": dict(outcome_counts),
                    "no_sale_reasons": dict(reason_counts.most_common()),
                    "products_mentioned": [
                        {"product": products_by_id.get(pid, {"id": pid}), "mentions": n}
                        for pid, n in product_counter.most_common()],
                    "items": convs,
                },
                "meta": {
                    "objects": [{"kind": k, "relation": rel, "record": meta_by_id.get((k, oid))}
                                for k, oid, rel in relations],
                    "daily_insights_raw": [r for r in raw_meta.get("insights", [])
                                           if r.get(f"{level}_id") == eid],
                    "daily_series_used_by_guardrails": [
                        {"date": i.date_start, "impressions": i.impressions, "spend": i.spend,
                         "frequency": i.frequency, "ctr": i.ctr} for i in series],
                },
                "steps": steps,
                "decision": decision,
            }
            db.execute("INSERT INTO entity_bundles VALUES (?,?,?,?)", (run_id, level, eid, _j(bundle)))

        for p in proposed:
            db.execute("INSERT INTO proposed_tests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, p["level"], p["id"], p["name"], p["audience_type"], p["theme"],
                p["budget_share"], p["expected_rate"], p["hypothesis"], p.get("hypothesis_source"),
                p["stop_rule"], _j(p)))

        _write_level_totals(db, run_id, run_params)
        _prune(db, keep_runs)
    with closing(_connect(db_path)) as db:
        db.execute("VACUUM")


def _meta_relations(level, eid, raw_meta):
    """(kind, object_id, relation) for every meta record tied to this id."""
    ads, adsets, campaigns = raw_meta["ads"], raw_meta["adsets"], raw_meta["campaigns"]
    adset_by_id = {a["id"]: a for a in adsets}
    out = []
    if level == "ad":
        ad = next((a for a in ads if a["id"] == eid), None)
        if ad:
            out.append(("ad", eid, "self"))
            if (ad.get("creative") or {}).get("id"):
                out.append(("creative", ad["creative"]["id"], "creative"))
            out.append(("adset", ad["adset_id"], "parent"))
            out.append(("campaign", ad["campaign_id"], "grandparent"))
    elif level == "adset":
        adset = adset_by_id.get(eid)
        if adset:
            out.append(("adset", eid, "self"))
            out.append(("campaign", adset["campaign_id"], "parent"))
            for a in ads:
                if a.get("adset_id") == eid:
                    out.append(("ad", a["id"], "child"))
                    if (a.get("creative") or {}).get("id"):
                        out.append(("creative", a["creative"]["id"], "child creative"))
    elif level == "campaign":
        if any(c["id"] == eid for c in campaigns):
            out.append(("campaign", eid, "self"))
            for s in adsets:
                if s.get("campaign_id") == eid:
                    out.append(("adset", s["id"], "child"))
            for a in ads:
                if a.get("campaign_id") == eid:
                    out.append(("ad", a["id"], "grandchild"))
                    if (a.get("creative") or {}).get("id"):
                        out.append(("creative", a["creative"]["id"], "grandchild creative"))
    # de-duplicate (two ads can share a creative)
    seen, uniq = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _ensure_schema(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        for kind, name in db.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%'").fetchall():
            db.execute(f"DROP {kind.upper()} IF EXISTS {name}")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.executescript(SCHEMA)


def _flat(e, steps, decision):
    """decisions-table row for one id, read straight out of its steps."""
    s = {st["step_key"]: st for st in steps}
    o = lambda k: s[k]["outputs"] if k in s else {}
    i = lambda k: s[k]["inputs"] if k in s else {}
    sel = (i("allocation").get("explore_selection") or {})
    return {
        "level": e["level"], "entity_id": e["id"], "name": e["name"], "detail": e["detail"],
        "parent_id": e["parent_id"], "campaign_id": e["campaign_id"], "adset_id": e["adset_id"],
        "conversations": i("conversations").get("conversations_linked"),
        "successes": o("conversations").get("successes"), "failures": o("conversations").get("failures"),
        "excluded": o("conversations").get("excluded"), "raw_rate": o("conversations").get("raw_rate"),
        "baseline": i("decision").get("baseline"), "baseline_source": i("decision").get("baseline_source"),
        "prior_strength": o("prior").get("prior_strength"),
        "score": decision["score"], "interval_low": decision["interval_low"],
        "interval_high": decision["interval_high"],
        "p_better": o("decision").get("p_better"), "p_worse": o("decision").get("p_worse"),
        "decision_rule": s["decision"]["rule"] if "decision" in s else None,
        "raw_action": decision["raw_action"],
        "spend": o("money").get("spend"), "revenue": o("money").get("revenue"),
        "sales": o("money").get("sales"),
        "resolved_conversations": o("money").get("resolved_conversations"),
        "roas_raw": o("money").get("raw_roas"), "roas_shrunk": o("money").get("shrunk_roas"),
        "cost_per_sale": o("money").get("cost_per_sale"),
        "baseline_cost_per_sale": o("money").get("baseline_cost_per_sale"),
        "level_median_roas": o("money").get("level_median_roas"),
        "recent_frequency": i("fatigue_guardrail").get("recent_avg_frequency"),
        "ctr_drop": o("fatigue_guardrail").get("ctr_drop"),
        "fatigue_checked": int(bool(s.get("fatigue_guardrail", {}).get("applied"))),
        "fatigued": _b(o("fatigue_guardrail").get("fatigued")),
        "frequency_warning": _b(o("fatigue_guardrail").get("frequency_warning")),
        "cpa_checked": int(bool(s.get("cpa_guardrail", {}).get("applied"))),
        "underperforming": _b(o("cpa_guardrail").get("underperforming")),
        "action": decision["action"], "held_back_by": decision["held_back_by"],
        "bucket": decision["bucket"], "budget_share": decision["budget_share"],
        "explore_outcome": o("allocation").get("explore_outcome"),
        "explore_rank": (sel.get("priority") or {}).get("rank"),
        "why_json": _j(decision["why"]), "reasoning": decision["reasoning"],
        "reasoning_source": decision["reasoning_source"],
        "hypothesis": decision["hypothesis"], "hypothesis_source": decision["hypothesis_source"],
        "stop_rule": decision["stop_rule"],
        "finding_count": len(decision["findings"]), "findings_json": _j(decision["findings"]),
    }


def _b(v):
    return None if v is None else int(bool(v))


def _write_level_totals(db, run_id, run_params):
    db.execute("""
        INSERT INTO level_totals
        SELECT run_id, level, COUNT(*), SUM(spend), SUM(revenue),
               CASE WHEN SUM(spend) > 0 THEN SUM(revenue) / SUM(spend) END,
               SUM(sales), SUM(resolved_conversations),
               CASE WHEN SUM(sales) > 0 THEN SUM(spend) / SUM(sales) END,
               SUM(action = 'scale'), SUM(action = 'hold'), SUM(action = 'kill'), ?, ?
        FROM decisions WHERE run_id = ? GROUP BY level""",
        (run_params["exploit_share"], run_params["explore_share"], run_id))


def _prune(db, keep_runs):
    old = [r[0] for r in db.execute(
        "SELECT run_id FROM runs ORDER BY generated_at DESC, run_id DESC LIMIT -1 OFFSET ?", (keep_runs,))]
    for run_id in old:
        for table in PER_RUN_TABLES:
            db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


# ---------------------------------------------------------------------------
# read helpers (for the app / chatbot)
# ---------------------------------------------------------------------------

def current_run_id(db_path) -> str | None:
    with closing(_connect(db_path)) as db:
        row = db.execute("SELECT run_id FROM runs WHERE is_current = 1").fetchone()
        return row[0] if row else None


def get_bundle(db_path, entity_id: str, run_id: str | None = None) -> dict | None:
    run_id = run_id or current_run_id(db_path)
    with closing(_connect(db_path)) as db:
        row = db.execute("SELECT bundle_json FROM entity_bundles WHERE run_id = ? AND entity_id = ?",
                         (run_id, entity_id)).fetchone()
        return json.loads(row[0]) if row else None


def list_entities(db_path, run_id: str | None = None) -> list[dict]:
    run_id = run_id or current_run_id(db_path)
    with closing(_connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            "SELECT e.*, d.action, d.bucket, d.score, d.budget_share FROM entities e "
            "LEFT JOIN decisions d USING (run_id, entity_id) WHERE e.run_id = ? "
            "ORDER BY CASE e.level WHEN 'campaign' THEN 0 WHEN 'adset' THEN 1 ELSE 2 END, e.name",
            (run_id,))]
