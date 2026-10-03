"""
Chat log: one row per question -- what was asked, how it was routed, which
tools ran, what the guard said, what the user saw, and their feedback.
Separate file from trace.db (that one is rebuilt by every pipeline run).
Contains user questions: keep it out of git.
"""

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from ..config import load_config

_cfg = load_config()["chat"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, session_id TEXT, page TEXT, run_id TEXT, selected_id TEXT,
    question TEXT, history_turns INTEGER,
    route TEXT, intent TEXT, confidence REAL, gate_source TEXT, language TEXT,
    entity_ids_json TEXT, standalone_question TEXT, options_json TEXT,
    reply TEXT, fallback TEXT, guard_ok INTEGER, guard_issues_json TEXT,
    first_attempt_issues_json TEXT, tools_json TEXT,
    gate_tokens INTEGER, answer_tokens INTEGER, latency_ms INTEGER, error TEXT,
    feedback INTEGER, feedback_note TEXT
)"""


def _connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=TRUNCATE")   # works on folders that forbid deletes
    db.execute(SCHEMA)
    return db


def _current_run(trace_db):
    try:
        with closing(sqlite3.connect(trace_db)) as db:
            row = db.execute("SELECT run_id FROM runs WHERE is_current = 1").fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def write(reply, *, question, session_id=None, page=None, selected_id=None, history_turns=0,
          path=None) -> int | None:
    """Never raises: logging must not break the chat."""
    path = path or _cfg.get("log_path", "outputs/chat_log.db")
    g, a = reply.gate, reply.answer_meta or {}
    gm = g.get("meta", {})
    tokens = lambda m: (m.get("input_tokens") or 0) + (m.get("output_tokens") or 0)
    errors = [e for e in (gm.get("error"), a.get("error")) if e]
    try:
        with closing(_connect(path)) as db, db:
            cur = db.execute(
                "INSERT INTO chat_log (ts, session_id, page, run_id, selected_id, question, history_turns, "
                "route, intent, confidence, gate_source, language, entity_ids_json, standalone_question, "
                "options_json, reply, fallback, guard_ok, guard_issues_json, first_attempt_issues_json, "
                "tools_json, gate_tokens, answer_tokens, latency_ms, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), session_id, page, _current_run(_cfg["db_path"]),
                 selected_id, question, history_turns, reply.route, g.get("intent"), g.get("confidence"),
                 g.get("source"), g.get("language"), json.dumps(g.get("entity_ids", [])),
                 g.get("standalone_question"), json.dumps(reply.options), reply.text, reply.fallback,
                 None if not reply.guard else int(reply.guard.get("ok", False)),
                 json.dumps((reply.guard or {}).get("issues", []), ensure_ascii=False),
                 json.dumps(a.get("first_issues", []), ensure_ascii=False),
                 json.dumps([{"tool": f["tool"], "args": f["args"]} for f in reply.facts], ensure_ascii=False),
                 tokens(gm), tokens(a) + tokens(a.get("retry", {})), reply.latency_ms,
                 " | ".join(errors) or None))
            return cur.lastrowid
    except Exception:
        return None


def feedback(log_id: int, value: int, note: str | None = None, path=None) -> None:
    """value: 1 = helpful, -1 = not helpful."""
    path = path or _cfg.get("log_path", "outputs/chat_log.db")
    try:
        with closing(_connect(path)) as db, db:
            db.execute("UPDATE chat_log SET feedback = ?, feedback_note = ? WHERE id = ?", (value, note, log_id))
    except Exception:
        pass
