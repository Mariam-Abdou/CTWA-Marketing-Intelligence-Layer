"""The four app pages. app.py wires them into st.navigation."""

import json

import pandas as pd
import streamlit as st

from . import data as D
from .chat import chat_panel

NAV = {}   # filled by app.py: {"report": st.Page, ...}


def open_report(entity_id: str):
    st.session_state["report_id"] = entity_id
    st.switch_page(NAV["report"])


def _clickable_table(df, columns, config, key, height=None):
    """Clicking any cell opens that row's report. Only acts on a NEW click, so
    coming back to the page doesn't bounce straight to the report again."""
    extra = {"height": height} if height else {}
    sel = st.dataframe(df[columns], column_config=config, hide_index=True, width="stretch",
                       on_select="rerun", selection_mode="single-cell", key=key, **extra)
    cells = sel.selection.cells if sel and sel.selection else []
    if cells:
        row = cells[0][0]
        marker = (key, row, len(df))
        if st.session_state.get(f"last_click_{key}") != marker:
            st.session_state[f"last_click_{key}"] = marker
            open_report(df.iloc[row]["entity_id"])
    else:
        st.session_state.pop(f"last_click_{key}", None)


def _need_run():
    info = D.run_info(D.db_version())
    if info is None:
        st.error("No decisions found. Run `python3 -m src.pipeline` first.")
        st.stop()
    return info


# ---------------------------------------------------------------------------
# Dashboard: decisions only
# ---------------------------------------------------------------------------

def dashboard():
    info = _need_run()
    v = D.db_version()
    df = D.decisions(v)
    p = info["params"]
    st.title("Dashboard")
    st.caption(f"Decisions from the run of {info['generated_at'].replace('T', ' at ')} · "
               f"next budget split {p['exploit_share']:.0%} proven / {p['explore_share']:.0%} tests · "
               "click any row for its full report")

    for tab, (level, title, sub) in zip(st.tabs([t for _, t, _ in D.LEVELS]), D.LEVELS):
        with tab:
            rows = df[df.level == level].copy()
            c = st.columns(4)
            c[0].metric("Scale", int((rows.action == "scale").sum()))
            c[1].metric("Test", int((rows.bucket == "explore").sum()))
            c[2].metric("Stop", int((rows.action == "kill").sum()))
            c[3].metric("No change", int((rows.bucket == "none").sum()))
            order = {"exploit": 0, "explore": 1, "kill": 2, "none": 3}
            rows = rows.assign(_o=rows.bucket.map(order)).sort_values(["_o", "budget_share", "score"],
                                                                     ascending=[True, False, False])
            rows["Decision"] = rows.action.map({"scale": "▲ scale", "hold": "■ hold", "kill": "▼ stop"})
            rows["Budget"] = [D.funding(D.clean(r)) for _, r in rows.iterrows()]
            rows["Next budget"] = rows.budget_share.fillna(0) * 100
            rows["Score"] = rows.score * 100
            rows["Likely range"] = [f"{D.pct(a, 0)} – {D.pct(b, 0)}" for a, b in zip(rows.interval_low, rows.interval_high)]
            rows["Evidence"] = [f"{s} sales / {s + f} chats" for s, f in zip(rows.successes, rows.failures)]
            _clickable_table(rows.reset_index(drop=True),
                             ["name", "Decision", "Budget", "Next budget", "Score", "Likely range", "Evidence"],
                             {"name": st.column_config.TextColumn(D.LEVEL_NOUN[level].title(), width="large"),
                              "Next budget": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
                              "Score": st.column_config.NumberColumn(format="%.1f%%",
                                                                      help="Chance a chat ends in a sale")},
                             key=f"dash_{level}")

    tests = D.proposed_tests(v)
    if not tests.empty:
        st.subheader("Proposed new tests")
        for _, t in tests.iterrows():
            with st.container(border=True):
                st.markdown(f"**{t['name']}** · {D.pct(t['budget_share'], 0)} of the next budget")
                st.caption(t["hypothesis"])


# ---------------------------------------------------------------------------
# Explorer: every campaign / audience / creative with filters
# ---------------------------------------------------------------------------

def explorer():
    _need_run()
    df = D.decisions(D.db_version())
    st.title("Explore")
    st.caption("Every campaign, audience and creative. Filter, search, click a row for its report.")
    f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    levels = f1.multiselect("Level", ["campaign", "adset", "ad"], default=["campaign", "adset", "ad"],
                            format_func=lambda l: {"campaign": "Campaign", "adset": "Audience", "ad": "Creative"}[l])
    actions = f2.multiselect("Decision", ["scale", "hold", "kill"], default=["scale", "hold", "kill"])
    camps = sorted(df.loc[df.level == "campaign", "name"])
    camp = f3.selectbox("Campaign", ["All"] + camps)
    search = f4.text_input("Search name", placeholder="e.g. Eid, lookalike, Iftar")
    g1, g2, _ = st.columns([2, 2, 5])
    only_tests = g1.toggle("Only funded tests")
    only_flags = g2.toggle("Only flagged by audit")

    rows = df[df.level.isin(levels) & df.action.isin(actions)]
    if camp != "All":
        rows = rows[rows.campaign_name == camp]
    if search:
        rows = rows[rows.name.str.contains(search, case=False, regex=False)]
    if only_tests:
        rows = rows[rows.bucket == "explore"]
    if only_flags:
        rows = rows[rows.finding_count > 0]

    rows = rows.copy()
    rows["Level"] = rows.level.map({"campaign": "Campaign", "adset": "Audience", "ad": "Creative"})
    rows["Decision"] = rows.action.map({"scale": "▲ scale", "hold": "■ hold", "kill": "▼ stop"})
    rows["Budget"] = [D.funding(D.clean(r)) for _, r in rows.iterrows()]
    rows["Score"] = rows.score * 100
    rows["Sales / chats"] = [f"{s} / {s + f}" for s, f in zip(rows.successes, rows.failures)]
    rows["Flag"] = ["⚑" if n else "" for n in rows.finding_count]
    st.caption(f"{len(rows)} of {len(df)} shown")
    _clickable_table(rows.reset_index(drop=True),
                     ["Level", "name", "parent_name", "Decision", "Budget", "Score", "Sales / chats",
                      "spend", "revenue", "roas_raw", "cost_per_sale", "Flag"],
                     {"name": st.column_config.TextColumn("Name", width="large"),
                      "parent_name": st.column_config.TextColumn("Belongs to"),
                      "Score": st.column_config.NumberColumn(format="%.1f%%"),
                      "spend": st.column_config.NumberColumn("Spend", format="%,.0f"),
                      "revenue": st.column_config.NumberColumn("Revenue", format="%,.0f"),
                      "roas_raw": st.column_config.NumberColumn("ROAS", format="%.2f"),
                      "cost_per_sale": st.column_config.NumberColumn("Cost / sale", format="%,.0f"),
                      "Flag": st.column_config.TextColumn(width="small", help="Decision and money disagree")},
                     key="explore_table", height=560)


# ---------------------------------------------------------------------------
# Report: one id, everything behind it, chat on the side
# ---------------------------------------------------------------------------

def report():
    _need_run()
    v = D.db_version()
    df = D.decisions(v)
    order = {"campaign": 0, "adset": 1, "ad": 2}
    df = df.assign(_o=df.level.map(order)).sort_values(["_o", "name"])
    ids = list(df.entity_id)
    label = dict(zip(df.entity_id, [f"{n} ({D.LEVEL_NOUN[l]})" for n, l in zip(df.name, df.level)]))
    qp = st.query_params.get("id")
    current = st.session_state.get("report_id") or (qp if qp in ids else ids[0])
    eid = st.selectbox("Report for", ids, index=ids.index(current) if current in ids else 0,
                       format_func=label.get)
    st.session_state["report_id"] = eid
    st.query_params["id"] = eid
    r = D.clean(df[df.entity_id == eid].iloc[0])

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.title(r["name"])
        st.markdown(f"{D.ACTION_BADGE[r['action']]} &nbsp; **{D.funding(r)}** &nbsp; · &nbsp; "
                    f"{D.LEVEL_NOUN[r['level']]} · {r['detail'].split('=', 1)[-1].replace('_', ' ')}")
        if r.get("reasoning"):
            st.markdown(f"#### {r['reasoning']}")
        for f in json.loads(r.get("findings_json") or "[]"):
            st.warning(f"**Worth a second look:** {f['finding']}")

        m = st.columns(4)
        m[0].metric("Score", D.pct(r["score"]), help="Chance a chat from this ends in a sale")
        m[1].metric("Likely range", f"{D.pct(r['interval_low'], 0)}–{D.pct(r['interval_high'], 0)}",
                    help="90% interval: wide = few chats")
        m[2].metric("Sales / resolved chats", f"{r['successes']} / {r['successes'] + r['failures']}")
        m[3].metric("Compared with", D.pct(r["baseline"]), help=r.get("baseline_source"))
        m = st.columns(4)
        m[0].metric("Spend", D.egp(r["spend"]))
        m[1].metric("Revenue", D.egp(r["revenue"]))
        m[2].metric("ROAS", "—" if r["roas_raw"] is None else f"{r['roas_raw']:.2f}")
        m[3].metric("Cost per sale", D.egp(r["cost_per_sale"]),
                    help=f"Level baseline {D.egp(r['baseline_cost_per_sale'])}")

        st.subheader("How this decision was made")
        for i, line in enumerate(D.path_for(r), 1):
            st.markdown(f"{i}. {line}")

        if r["bucket"] == "explore":
            st.subheader("The test")
            st.markdown(f"**What it asks:** {r['hypothesis']}")
            st.markdown(f"**When to stop:** {r['stop_rule']}")

        rel = df[(df.entity_id == r["parent_id"]) | (df.parent_id == eid)]
        if not rel.empty:
            st.subheader("Related")
            for _, x in rel.iterrows():
                kind = "Part of" if x.entity_id == r["parent_id"] else "Contains"
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"{kind}: **{x['name']}** ({D.LEVEL_NOUN[x['level']]}) — "
                            f"{x['action']} · {D.funding(D.clean(x))}")
                if c2.button("Open", key=f"rel_{x.entity_id}"):
                    open_report(x.entity_id)

        conv = D.conversation_summary(eid, v)
        if conv:
            st.subheader(f"Conversations ({conv['count']})")
            c1, c2 = st.columns(2)
            c1.bar_chart(pd.Series(conv["outcome_types"], name="chats"), horizontal=True, height=220)
            if conv["products"]:
                c2.dataframe(pd.DataFrame(conv["products"], columns=["Product mentioned", "Mentions"]),
                             hide_index=True, width="stretch")

        series = D.daily_series(eid, v)
        if not series.empty:
            with st.expander("Meta delivery over time (frequency, CTR, spend)"):
                s = series.set_index("date")
                st.line_chart(s[["frequency", "ctr"]], height=220)
                st.bar_chart(s["spend"], height=160)

        with st.expander("All 12 steps (inputs, outputs, rule)"):
            for stp in D.steps(eid, v):
                tag = "" if stp["applied"] else " · not applied"
                tag += " · changed the outcome" if stp["changed_outcome"] else ""
                st.markdown(f"**{stp['step_no']}. {stp['title']}**{tag}")
                st.caption(stp["rule"])
                st.json({"inputs": stp["inputs"], "outputs": stp["outputs"]}, expanded=False)

    with right:
        st.subheader("Ask about this")
        chat_panel(f"report_{eid}", selected_id=eid, page="report", height=560,
                   placeholder=f"Ask about {r['name']}…",
                   starters=["Why this decision?", "How sure is the system about it?",
                             "How much has it spent and earned?"])


# ---------------------------------------------------------------------------
# Chat: general questions
# ---------------------------------------------------------------------------

def chat():
    info = _need_run()
    st.title("Ask")
    st.caption("Ask about any campaign, audience or creative, totals and comparisons, or how the system "
               "works. The assistant explains the stored decisions; it does not make new ones.")
    chat_panel("general", selected_id=None, page="chat",
               starters=["Which campaigns are being scaled and why?",
                         "Which audience has the best cost per sale?",
                         "What does the 70/30 split mean?",
                         "Which ads are earning well but not being scaled?"])
