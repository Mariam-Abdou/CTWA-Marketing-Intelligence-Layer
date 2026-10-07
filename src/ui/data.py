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
def customers_said(entity_id, v=0) -> dict:
    from ..chat.store import get_conversations
    return get_conversations(entity_id, limit=5)


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
