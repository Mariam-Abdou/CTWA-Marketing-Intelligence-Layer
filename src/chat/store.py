"""Read-only access to the stored decisions (outputs/trace.db) for the chat."""

import json
import sqlite3
from contextlib import closing

from ..config import load_config

DB = load_config()["chat"]["db_path"]
BAR = load_config()["decision"]["probability_threshold"]   # same bar the pipeline decides with
ACTION_TEXT = {
    "scale": "scale it (gets the main share of the next budget)",
    "hold": "keep it as is",
    "kill": "stop spending on it",
}
BUCKET_TEXT = {"exploit": "exploit (proven)", "explore": "explore (funded test)",
               "kill": "no budget", "none": "no budget change"}


def stored_decisions(ids: list[str], db_path: str = DB) -> list[dict]:
    if not ids:
        return []
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"SELECT level, entity_id, name, action, bucket, budget_share, held_back_by, score, "
            f"interval_low, interval_high, successes, conversations, why_json, stop_rule "
            f"FROM current_decisions WHERE entity_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["why"] = json.loads(d.pop("why_json") or "[]")
        out.append(d)
    return out


def decision_summary(d: dict) -> str:
    """One deterministic line per entity."""
    line = (f"**{d['name']}** ({d['level']}): {ACTION_TEXT.get(d['action'], d['action'])}"
            f" · {BUCKET_TEXT.get(d['bucket'], d['bucket'])}")
    if d["budget_share"]:
        line += f" · {d['budget_share']:.0%} of the next budget"
    if d["held_back_by"]:
        line += f" · held back by {d['held_back_by']}"
    return line


# ---------------------------------------------------------------------------
# get_entity: compact, token-budgeted context for the answer step
# ---------------------------------------------------------------------------

# Keys that cost tokens without helping an explanation.
_DROP_KEYS = {"facts_given", "hypothesis_prompt", "rule_based_hypothesis", "min_group_n", "min_groups",
              "floor", "ceiling", "fallback", "mean_group_n", "credible_level", "precision"}


def _compact(v):
    if isinstance(v, float):
        # rates/probabilities: 4 significant digits
        # money and counts: 2 decimals
        return round(v, 2) if abs(v) >= 100 else float(f"{v:.4g}")
    if isinstance(v, dict):
        return {k: _compact(x) for k, x in v.items()
                if k not in _DROP_KEYS and x is not None and x != {} and x != []}
    if isinstance(v, list):
        return [_compact(x) for x in v]
    return v


def entity_context(entity_id: str, db_path: str = DB) -> dict | None:
    """Everything needed to explain one id's decision, compact: who it is, its parent and children (one line each),
       the final decision, and the 12 steps (inputs, outputs, rule). Conversations are summarised, not listed."""
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        d = db.execute("SELECT * FROM current_decisions WHERE entity_id = ?", (entity_id,)).fetchone()
        if d is None:
            return None
        d = dict(d)
        run_id = d["run_id"]
        steps = db.execute(
            "SELECT step_no, step_key, applied, changed_outcome, rule, inputs_json, outputs_json "
            "FROM decision_steps WHERE run_id = ? AND entity_id = ? ORDER BY step_no",
            (run_id, entity_id)).fetchall()
        rel = lambda where, *p: [dict(r) for r in db.execute(
            "SELECT entity_id, level, name, action, bucket, budget_share FROM current_decisions WHERE " + where, p)]
        parent = rel("entity_id = ?", d["parent_id"]) if d["parent_id"] else []
        children = rel("parent_id = ?", entity_id)
        bundle = json.loads(db.execute(
            "SELECT bundle_json FROM entity_bundles WHERE run_id = ? AND entity_id = ?",
            (run_id, entity_id)).fetchone()[0])

    convs = bundle["conversations"]
    return _compact({
        "entity": {"id": d["entity_id"], "level": d["level"], "name": d["name"], "detail": d["detail"]},
        "decision": {k: d[k] for k in ("action", "raw_action", "bucket", "budget_share", "held_back_by",
                                       "score", "interval_low", "interval_high", "explore_outcome",
                                       "hypothesis", "stop_rule", "reasoning")},
        "decision_path": decision_path(d),
        "why": json.loads(d["why_json"] or "[]"),
        "findings": [f["finding"] for f in json.loads(d["findings_json"] or "[]")],
        "parent": parent[0] if parent else None,
        "children": children,
        "conversations_summary": {
            "count": convs["count"], "outcome_types": convs["outcome_types"],
            "top_products": [{"name": p["product"].get("name"), "mentions": p["mentions"]}
                             for p in convs["products_mentioned"][:5]],
        },
        "steps": [_step_view(s) for s in steps if s["step_key"] not in _SKIP_STEPS],
        "how_steps_work": "call glossary('steps') for the generic rule of each step",
    })


# explanation/audit repeat what "decision"/"why"/"findings" already carry
_SKIP_STEPS = {"explanation", "audit"}
# steps whose rule text is specific to THIS id (the rest are generic -> glossary)
_SPECIFIC_RULE = {"decision", "conversations"}


def _step_view(s) -> dict:
    out = {"step": s["step_no"], "key": s["step_key"]}
    if not s["applied"]:
        out["applied"] = False
    if s["changed_outcome"]:
        out["changed_outcome"] = True
    if s["step_key"] in _SPECIFIC_RULE and s["step_key"] != "conversations":
        out["rule"] = s["rule"]
    inputs, outputs = json.loads(s["inputs_json"]), json.loads(s["outputs_json"])
    if s["step_key"] == "prior":
        fit = inputs.pop("prior_strength_fit", {}) or {}
        inputs["prior_strength_source"] = fit.get("source")
    if s["step_key"] == "test_plan" and not s["applied"]:
        return out
    if s["step_key"] in ("fatigue_guardrail", "cpa_guardrail") and not s["applied"]:
        # The numbers are computed for every id, but the guardrail only RUNS when the raw action is scale.
        # Showing "fatigued: true" made the model say it is flagged as fatigued, it was never checked.
        out["note"] = "NOT CHECKED: this guardrail only runs when the raw action is scale"
        return out
    out["in"], out["out"] = inputs, outputs
    return out


def _pct(x):
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "n/a"


def decision_path(d: dict) -> list[str]:
    """The decision as an ordered chain of plain sentences."""
    path = [f"Evidence: {d['successes']} sales out of {d['successes'] + d['failures']} resolved conversations"
            f" ({d['excluded']} excluded, outcome not known yet)."]
    if d["score"] is None:
        path.append("No resolved conversations: no score, so no decision from the numbers.")
    else:
        path.append(f"Score {_pct(d['score'])} (90% interval {_pct(d['interval_low'])} to "
                    f"{_pct(d['interval_high'])}) compared with a baseline of {_pct(d['baseline'])} "
                    f"({d['baseline_source']}).")
        path.append(f"P(better than baseline) = {_pct(d['p_better'])}, P(worse) = {_pct(d['p_worse'])}; "
                    f"the bar to act is {_pct(BAR)} -> the numbers alone say {d['raw_action'].upper()}.")
    if d.get("spend") is not None:
        money = (f"Money (context, not part of the score): spend {d['spend']:,.0f} EGP, revenue "
                 f"{(d.get('revenue') or 0):,.0f} EGP, {d.get('sales') or 0} sales")
        if d.get("cost_per_sale") is not None:
            money += f", cost per sale {d['cost_per_sale']:,.0f} EGP"
        if d.get("baseline_cost_per_sale") is not None:
            money += f" (level baseline {d['baseline_cost_per_sale']:,.0f} EGP)"
        path.append(money + ".")
    if d["raw_action"] == "scale":
        if d["fatigued"]:
            path.append("Fatigue guardrail checked and FIRED (frequency high and CTR fell): scale became hold.")
        elif d["underperforming"]:
            path.append("Cost-per-sale guardrail checked and FIRED: scale became hold.")
        else:
            path.append("Guardrails (fatigue, cost per sale) checked and passed.")
    else:
        path.append("Guardrails (fatigue, cost per sale) were NOT checked: they only run when the numbers say "
                    "scale, so frequency, CTR and cost per sale did not affect this decision.")
    path.append(f"Final action: {d['action'].upper()}.")
    bucket = {"exploit": f"gets {_pct(d['budget_share'])} of the next budget (exploit pool)",
              "explore": f"funded as a test with {_pct(d['budget_share'])} of the next budget",
              "kill": "no budget", "none": "no budget change"}[d["bucket"]]
    extra = f" ({d['explore_outcome']})" if d.get("explore_outcome") and d["bucket"] == "none" else ""
    path.append(f"Budget: {bucket}{extra}.")
    return path


# ---------------------------------------------------------------------------
# run_sql: read-only queries over the CURRENT run only
# ---------------------------------------------------------------------------

SQL_VIEWS = {
    "decisions": "SELECT * FROM decisions WHERE run_id = (SELECT run_id FROM runs WHERE is_current = 1)",
    "level_totals": "SELECT * FROM level_totals WHERE run_id = (SELECT run_id FROM runs WHERE is_current = 1)",
    "proposed_tests": "SELECT level, test_id, name, audience_type, theme, budget_share, expected_rate, "
                      "hypothesis, stop_rule FROM proposed_tests "
                      "WHERE run_id = (SELECT run_id FROM runs WHERE is_current = 1)",
}
SQL_MAX_ROWS = 50


def sql_schema(db_path: str = DB) -> str:
    with closing(sqlite3.connect(db_path)) as db:
        cols = {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")] for t in ("decisions", "level_totals")}
    cols["decisions"] = [c for c in cols["decisions"] if c not in ("run_id",)]
    cols["level_totals"] = [c for c in cols["level_totals"] if c != "run_id"]
    return (f"cur_decisions({', '.join(cols['decisions'])})\n"
            f"cur_level_totals({', '.join(cols['level_totals'])})\n"
            "cur_proposed_tests(level, test_id, name, audience_type, theme, budget_share, expected_rate, "
            "hypothesis, stop_rule)")


def run_sql(query: str, db_path: str = DB) -> dict:
    """SELECT only, one statement, over cur_* views of the current run, capped rows.
    The connection is opened read-only AND an authorizer denies anything but reads."""
    q = query.strip().rstrip(";")
    if ";" in q:
        return {"error": "one statement only"}
    if not q.lower().startswith(("select", "with")):
        return {"error": "only SELECT queries are allowed"}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for name, body in SQL_VIEWS.items():
            conn.execute(f"CREATE TEMP VIEW cur_{name} AS {body}")
        def authorizer(action, arg1, arg2, dbname, source):
            # A read is allowed only when it happens INSIDE one of the cur_* views. Reading a base table directly has no view
            # as source and is denied. (e.g. runs, raw decisions of other runs, conversations with customer phone numbers, etc.)
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and (
                    (dbname == "temp" and arg1.startswith("cur_"))      # the view itself
                    or (source and source.startswith("cur_"))):        # tables read by the view
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        conn.set_authorizer(authorizer)
        cur = conn.execute(q)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(SQL_MAX_ROWS + 1)
        return {"columns": cols, "rows": [[_compact(x) for x in r] for r in rows[:SQL_MAX_ROWS]],
                "truncated": len(rows) > SQL_MAX_ROWS}
    except sqlite3.Error as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()
