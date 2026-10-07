"""Read-only data for the app pages, from the current run in trace.db.
Every loader takes `v` = the database's modification time, so re-running
the pipeline changes the cache key and the app refreshes without a restart."""

import json
import os
import sqlite3
from contextlib import closing

import pandas as pd
import streamlit as st

from ..config import load_config
from ..chat.store import decision_path
from ..config import load_config as _lc  # noqa: F401

DB = load_config()["chat"]["db_path"]
LEVELS = [("campaign", "Campaigns", "the goal"), ("adset", "Audiences", "who sees it"),
          ("ad", "Creatives", "what they see")]
LEVEL_NOUN = {"campaign": "campaign", "adset": "audience", "ad": "creative"}


def db_version() -> float:
    return os.path.getmtime(DB) if os.path.exists(DB) else 0.0


def _q(sql, *params) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as db:
        return pd.read_sql_query(sql, db, params=params)


@st.cache_data
def run_info(v=0) -> dict | None:
    df = _q("SELECT run_id, generated_at, conversations_path, run_params_json FROM runs WHERE is_current = 1")
    if df.empty:
        return None
    r = df.iloc[0].to_dict()
    r["params"] = json.loads(r.pop("run_params_json"))
    return r


@st.cache_data
def decisions(v=0) -> pd.DataFrame:
    df = _q("SELECT * FROM current_decisions")
    names = dict(zip(df.entity_id, df.name))
    df["parent_name"] = df.parent_id.map(names)
    df["campaign_name"] = df.campaign_id.map(names)
    return df


@st.cache_data
def level_totals(v=0) -> pd.DataFrame:
    return _q("SELECT * FROM level_totals WHERE run_id = (SELECT run_id FROM runs WHERE is_current = 1)")


@st.cache_data
def proposed_tests(v=0) -> pd.DataFrame:
    return _q("SELECT * FROM proposed_tests WHERE run_id = (SELECT run_id FROM runs WHERE is_current = 1)")


@st.cache_data
def steps(entity_id, v=0) -> list[dict]:
    df = _q("SELECT step_no, step_key, title, applied, changed_outcome, rule, inputs_json, outputs_json "
            "FROM current_steps WHERE entity_id = ? ORDER BY step_no", entity_id)
    return [{**r, "inputs": json.loads(r.pop("inputs_json")), "outputs": json.loads(r.pop("outputs_json"))}
            for r in df.to_dict("records")]


@st.cache_data
def daily_series(entity_id, v=0) -> pd.DataFrame:
    return _q("SELECT date, impressions, spend, frequency, ctr FROM entity_daily_series "
              "WHERE run_id = (SELECT run_id FROM runs WHERE is_current = 1) AND entity_id = ? ORDER BY date",
              entity_id)


@st.cache_data
def conversation_summary(entity_id, v=0) -> dict:
    df = _q("SELECT bundle_json FROM entity_bundles WHERE run_id = (SELECT run_id FROM runs WHERE "
            "is_current = 1) AND entity_id = ?", entity_id)
    if df.empty:
        return {}
    c = json.loads(df.iloc[0, 0])["conversations"]
    return {"count": c["count"], "outcome_types": c["outcome_types"],
            "products": [(p["product"].get("name", p["product"].get("id")), p["mentions"])
                         for p in c["products_mentioned"][:8]]}


@st.cache_data
def customers_of(entity_id, v=0) -> dict:
    from ..chat.store import get_customers
    return get_customers(entity_id, limit=5)


@st.cache_data
def customers_said(entity_id, v=0) -> dict:
    from ..chat.store import get_conversations
    return get_conversations(entity_id, limit=5)


_dec = load_config()["decision"]
BAR, MIN_N = _dec["probability_threshold"], _dec["min_n_for_action"]


def pct_vs_bar(x) -> str:
    """One decimal, or two when one decimal would make it look equal to the bar."""
    if x is None:
        return "—"
    return f"{x * 100:.2f}%" if abs(x - BAR) < 0.0005 or round(x * 100, 1) == round(BAR * 100, 1) else pct(x)


def how_sure(r: dict) -> tuple[str, str]:
    """(short label, one plain sentence) -- from the stored numbers, by code."""
    n = (r.get("successes") or 0) + (r.get("failures") or 0)
    if r.get("score") is None or n == 0:
        return "No data yet", "No chats with a known outcome yet."
    if n < MIN_N:
        return "Not sure — too few chats", (f"Only {n} chats with a known outcome; the system does not act "
                                            f"on fewer than {MIN_N}.")
    pb, pw, raw = r.get("p_better") or 0, r.get("p_worse") or 0, r.get("raw_action")
    base = f"{pct(r.get('baseline'))} ({r.get('baseline_source')})"
    if raw in ("scale", "kill"):
        p = pb if raw == "scale" else pw
        word = "Very sure" if p >= 0.95 else "Sure" if p >= 0.85 else "Fairly sure"
        side = "better" if raw == "scale" else "worse"
        text = f"{pct_vs_bar(p)} chance its true sale rate is {side} than {base}; the bar to act is {pct(BAR)}."
        if r.get("action") != raw:
            return f"{word} — but held back", text + f" It was held back by {r.get('held_back_by')}."
        return word, text
    return "Not sure yet — too close to call", (f"{pct_vs_bar(pb)} chance it is better and {pct_vs_bar(pw)} that it is worse "
                                                 f"than {base}; neither reaches the {pct(BAR)} bar.")


def clean(row) -> dict:
    """pandas row -> plain dict with None instead of NaN."""
    return {k: (None if (not isinstance(v, (list, dict, str)) and pd.isna(v)) else v) for k, v in dict(row).items()}


def path_for(row: dict) -> list[str]:
    return decision_path(clean(row))


# ---- formatting -------------------------------------------------------------

def pct(x, d=1):
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{d}f}%"


def egp(x):
    return "—" if x is None or pd.isna(x) else f"{x:,.0f} EGP"


def funding(r) -> str:
    if r["bucket"] == "exploit":
        return f"Main budget · {pct(r['budget_share'], 0)}"
    if r["bucket"] == "explore":
        return f"Test budget · {pct(r['budget_share'], 0)}"
    if r["bucket"] == "kill":
        return "Stop spending"
    if r.get("held_back_by"):
        return f"Held back — {r['held_back_by']}"
    if r.get("explore_outcome") and "lost" in str(r["explore_outcome"]):
        return "No budget — lost the test slot"
    if r.get("explore_outcome") and "not worth" in str(r["explore_outcome"]):
        return "No budget — too alike to test"
    return "No change"


ACTION_BADGE = {"scale": ":green-background[**▲ scale**]", "hold": ":orange-background[**■ hold**]",
                "kill": ":red-background[**▼ stop**]"}
