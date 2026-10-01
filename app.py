"""
Two pages built from one pipeline run: the Scoreboard (every field the brief
asks for, plus every plain-language column the reasoning layer writes) and
Numbers (spend/revenue/ROAS, for whoever wants to check the money behind a
decision).

The scoreboard TABLE is the deliverable, and it carries everything on the
row itself: id, score, action, bucket, why, budget share, and the
reasoning sentence -- the brief's own example row is {id, score, action,
bucket, why}; this has all of that plus what this project computes beyond
it. A long cell (the reasoning sentence) can read truncated in the grid --
click the row underneath the table for the full, un-truncated text plus the
statistical fine print (interval, hypothesis, stop rule) that doesn't need
to compete for space in the grid itself. Money is deliberately a separate
page: a merchant reading the scoreboard is reading a recommendation, not an
accounting sheet, and mixing the two makes both harder to read.

Reads outputs/plan_*.json and nothing else. Every number and sentence here
came from `python3 -m src.pipeline`; this file only arranges them.

    python3 -m src.pipeline      # produces the run
    streamlit run app.py         # shows it

No hand-written HTML or hardcoded colours: Streamlit's own components follow
the viewer's theme, and an earlier version that styled its own cards was
unreadable in dark mode.
"""

import glob
import json
import os

import pandas as pd
import streamlit as st

LEVELS = [
    ("campaign", "Campaigns", "the goal"),
    ("adset", "Audiences", "who sees it"),
    ("ad", "Creatives", "what they see"),
]
PARENT_OF = {"adset": "Campaign", "ad": "Audience"}
# Detail view still wants an icon/colour per action -- table cells show the raw word.
ACTION = {"scale": ("scale", "▲", "green"), "kill": ("kill", "▼", "red"), "hold": ("hold", "■", "orange")}
BUCKET_LABEL = {"exploit": "exploit", "explore": "explore", "kill": "stop", "none": "none"}


@st.cache_data
def load_run():
    files = sorted(glob.glob("outputs/plan_*.json"), key=os.path.getmtime)
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f), files[-1]


def why_parts(row):
    """`why` is a list in the plan JSON and a joined string in the CSV."""
    why = row.get("why") or []
    return why if isinstance(why, list) else [p for p in why.split("; ") if p]


def readable(detail):
    return (detail.split("=", 1)[-1].replace("OUTCOME_", "")
            .replace("_", " ").replace("/", " · ").lower())


def funding(row):
    """One plain phrase covering both where the money goes and what stopped it
    going there. "exploit"/"explore"/"none" say nothing to a merchant on their
    own, and a row reading "Scale" with no budget needs its reason on the same
    line -- this is what the Budget column of the table shows."""
    if row["bucket"] == "exploit":
        return "Main budget"
    if row["bucket"] == "explore":
        return "Test budget"
    if row["bucket"] == "kill":
        return "Stop spending"
    if row["held_back_by"]:
        return f"Held back — {row['held_back_by']}"
    if row["not_worth_testing"]:
        return "No budget — too alike to test"
    if row["lost_test_slot"]:
        return "No budget — lost the test slot"
    return "No change"


def as_table(rows):
    # Percentages are stored 0-1 but ProgressColumn formats the raw number, so
    # 0.69 would render as "1%". Scale here; max_value matches.
    return pd.DataFrame([
        {
            "ID": r["id"][-8:],
            "Name": r["name"],
            "Score": r["score"] * 100 if r["score"] is not None else None,
            "Do": ACTION.get(r["action"], ACTION["hold"])[0],
            "Bucket": BUCKET_LABEL.get(r["bucket"], r["bucket"]),
            "Budget": funding(r),
            "Next budget": (r["budget_share"] or 0.0) * 100,
            "Why": "; ".join(why_parts(r)),
            "Reasoning": r["reasoning"],
        }
        for r in rows
    ])


COLUMNS = {
    "ID": st.column_config.TextColumn(width="small", help="Last 8 digits of the Meta id for this row."),
    "Name": st.column_config.TextColumn(width="medium"),
    "Score": st.column_config.NumberColumn(
        "Score", format="%.0f%%",
        help="How often these chats end in a sale, pulled toward the average when there is "
             "little data behind it. This is the number decisions are made on. Blank means "
             "no data yet -- not a score of zero."),
    "Do": st.column_config.TextColumn(width="small", help="scale / hold / stop"),
    "Bucket": st.column_config.TextColumn(
        width="small", help="exploit = main budget, explore = test budget, kill = stop, "
                             "none = no budget change"),
    "Budget": st.column_config.TextColumn(
        width="medium", help="Where this sits in the next budget, and what stopped it "
                             "going further if anything did"),
    "Next budget": st.column_config.ProgressColumn(
        "Next budget", format="%.0f%%", min_value=0, max_value=100),
    "Why": st.column_config.TextColumn(
        width="large", help="The short evidence trail behind the score -- the brief's own "
                             "'3 sales / 18 clicks, CTR stable' example row."),
    "Reasoning": st.column_config.TextColumn(
        width="large", help="The full recommendation in plain language. Click the row below "
                             "the table if this reads cut off here."),
}

HELD_BACK_DETAIL = {
    "audience fatigue": (
        "The numbers said scale, but the same people keep seeing this and have stopped "
        "clicking. Spending more would buy the same tired audience again."
    ),
    "cost per sale": (
        "The numbers said scale, but each sale is costing far more than this shop "
        "normally pays, so more budget would buy expensive sales."
    ),
}


def show_detail(row, level):
    """The un-truncated version of what the table already shows, plus the
    statistical fine print that doesn't belong in the grid: full reasoning,
    hypothesis/stop rule, interval, and why a row was held back."""
    label, mark, colour = ACTION.get(row["action"], ACTION["hold"])
    with st.container(border=True):
        st.markdown(f":{colour}-background[:{colour}[**{mark} {label}**]] &nbsp; **{row['name']}**")
        trail = [readable(row["detail"])]
        if row.get("parent"):
            trail.append(f"{PARENT_OF.get(level, 'Parent')}: {row['parent']}")
        st.caption(" · ".join(trail))

        st.markdown(f"### {row['reasoning']}")

        if row["held_back_by"]:
            st.warning(f"**Held back — {row['held_back_by']}.** "
                       f"{HELD_BACK_DETAIL.get(row['held_back_by'], '')}")
        elif row["not_worth_testing"]:
            st.info(
                "**No test budget.** This performs so close to everything else that no "
                "realistic amount of spending would prove it better or worse. Choose "
                "between it and its neighbours on cost, not by testing."
            )
        elif row["lost_test_slot"]:
            st.info(
                "**No test budget.** Worth testing, but other tests ranked higher and "
                "only a few run at once. It is first in line next time."
            )
        elif row["frequency_warning"]:
            st.warning(
                "**Watch the frequency.** People are starting to see this often. Not "
                "enough to hold it back yet, but worth a look next cycle."
            )

        st.markdown(":gray[**What this is based on**]")
        for part in why_parts(row):
            st.markdown(f"- {part}")
        if row["excluded"]:
            st.markdown(
                f"- {row['excluded']} conversations are still open, so they count "
                "neither as a sale nor as a loss"
            )
        if row["interval_low"] is not None:
            st.markdown(
                f"- On this much data the true sale rate is probably between "
                f"**{row['interval_low']:.0%}** and **{row['interval_high']:.0%}** — "
                "a wide span means few conversations, not a bad ad"
            )

        if row["bucket"] == "explore" and row["hypothesis"]:
            st.markdown(":gray[**What the test is asking**]")
            st.markdown(row["hypothesis"])
            st.markdown(f":gray[**When to stop**] &nbsp; {row['stop_rule']}")


def show_test(title, budget, hypothesis, stop_rule, note=None):
    with st.container(border=True):
        heading, share = st.columns([5, 1])
        heading.markdown(f"**{title}**")
        share.markdown(f":blue[**{budget:.0%}**]")
        if note:
            st.caption(note)
        st.markdown(":gray[**What the test is asking**]")
        st.markdown(hypothesis)
        st.markdown(":gray[**When to stop**]")
        st.markdown(stop_rule)


def money_row(r, spend, revenue, sales, roas, cost_per_sale):
    return {
        "Name": r["name"],
        "Spend (EGP)": spend,
        "Revenue (EGP)": revenue,
        "Sales": sales,
        "ROAS": roas,
        "Cost / sale (EGP)": cost_per_sale,
    }


st.set_page_config(page_title="CTWA scoreboard", layout="wide")
run, path = load_run()

if run is None:
    st.error("No run found. Run `python3 -m src.pipeline` first.")
    st.stop()

page = st.sidebar.radio("View", ["Scoreboard", "Numbers"])
st.sidebar.caption(
    "**Scoreboard** — the decision, why, and what to do next.\n\n"
    "**Numbers** — spend, revenue and ROAS behind each decision."
)

if page == "Scoreboard":
    st.title("Scoreboard")
    st.caption(
        f"Run of {run['generated_at'].replace('T', ' at ')} · test horizon "
        f"{run['horizon_days']:.0f} days · confidence threshold {run['probability_threshold']:.0%}"
    )

    for tab, (key, title, subtitle) in zip(st.tabs([t for _, t, _ in LEVELS]), LEVELS):
        with tab:
            rows = run["levels"][key]["rows"]
            counts = {
                "to scale": sum(1 for r in rows if r["bucket"] == "exploit"),
                "to test": sum(1 for r in rows if r["bucket"] == "explore") + len(run["levels"][key]["proposed"]),
                "to stop": sum(1 for r in rows if r["bucket"] == "kill"),
                "left alone": sum(1 for r in rows if r["bucket"] == "none"),
            }
            st.caption(
                f"{title} — {subtitle}. &nbsp;·&nbsp; "
                + " &nbsp;·&nbsp; ".join(f"**{v}** {k}" for k, v in counts.items() if v)
            )

            table = as_table(rows)
            order = list(range(len(rows)))

            # single-cell, not single-row: row selection adds a checkbox column
            # that cannot be hidden. Clicking anywhere in a row selects that row.
            selection = st.dataframe(
                table, column_config=COLUMNS, hide_index=True,
                width='stretch', on_select="rerun",
                selection_mode="single-cell", key=f"table_{key}",
            )

            cells = selection.selection.cells if selection and selection.selection else []
            position = cells[0][0] if cells else 0
            if not cells:
                st.caption("Click any row for the full reading behind it -- the table above can "
                           "cut off a long Why or Reasoning cell.")
            show_detail(rows[order[position]], key)

    st.divider()
    st.title("Exploration plan")
    st.caption("Where the testing budget goes, what each test asks, and when to stop.")

    for tab, (key, title, _) in zip(st.tabs([t for _, t, _ in LEVELS]), LEVELS):
        with tab:
            level = run["levels"][key]
            running = [r for r in level["rows"] if r["bucket"] == "explore"]
            proposed = level["proposed"]

            if not running and not proposed:
                st.info("No tests at this level in this run.")
            if running:
                st.subheader("Running now")
                for row in running:
                    show_test(row["name"], row["budget_share"] or 0,
                              row["hypothesis"], row["stop_rule"])
            if proposed:
                st.subheader("Never run before")
                for t in proposed:
                    show_test(t["name"], t["budget_share"], t["hypothesis"], t["stop_rule"],
                              note="No data on this combination yet — a proposal, not a result.")

            if level["unresolvable"]:
                by_id = {r["id"]: r for r in level["rows"]}
                with st.expander(f"{len(level['unresolvable'])} got no test budget — too alike to tell apart"):
                    st.caption(
                        "These sit so close to everything else that no realistic amount of "
                        "spending would separate them. Choose between them on cost, not by testing."
                    )
                    for id_ in level["unresolvable"]:
                        row = by_id.get(id_)
                        if row:
                            st.markdown(f"- **{row['name']}** — {row['reasoning']}")

    st.divider()
    st.title("Worth a second look")
    st.caption(
        "Where our recommendation and the money point different ways. These are not "
        "errors — the score reads how often a chat ends in a sale, and that is not the "
        "same as how much each sale is worth. Somewhere one of the two is telling you "
        "something the other cannot see. Check the Numbers page for the money itself."
    )

    for tab, (key, title, _) in zip(st.tabs([t for _, t, _ in LEVELS]), LEVELS):
        with tab:
            findings = run["levels"][key].get("findings", [])
            if not findings:
                st.success(f"Every {title[:-1].lower()} here: the decision and the money agree.")
                continue
            by_id = {r["id"]: r for r in run["levels"][key]["rows"]}
            for f in findings:
                with st.container(border=True):
                    st.markdown(f"**{f['name']}**")
                    st.markdown(f["finding"])
                    row = by_id.get(f["id"])
                    if row:
                        st.caption(f"We said: {row['reasoning']}")

    st.caption(f"Source: {path}")

else:
    st.title("Numbers")
    st.caption(
        f"Run of {run['generated_at'].replace('T', ' at ')}. Spend, revenue and ROAS "
        "behind each scoreboard decision -- see the Scoreboard page for the decision itself."
    )

    for tab, (key, title, _) in zip(st.tabs([t for _, t, _ in LEVELS]), LEVELS):
        with tab:
            level = run["levels"][key]
            totals = level["totals"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total spend", f"{totals['spend']:,.0f} EGP" if totals["spend"] else "—")
            c2.metric("Total revenue", f"{totals['revenue']:,.0f} EGP" if totals["revenue"] else "—")
            c3.metric("ROAS", f"{totals['roas']:.2f}" if totals["roas"] is not None else "—")
            c4.metric("Sales", totals.get("sales") or "—")

            table = pd.DataFrame([
                money_row(
                    r, r.get("spend"), r.get("revenue"), r.get("sales"),
                    r.get("roas"), r.get("cost_per_sale"),
                )
                for r in level["rows"]
            ])
            st.dataframe(
                table, hide_index=True, width='stretch',
                column_config={
                    "Spend (EGP)": st.column_config.NumberColumn(format="%.0f"),
                    "Revenue (EGP)": st.column_config.NumberColumn(format="%.0f"),
                    "ROAS": st.column_config.NumberColumn(format="%.2f"),
                    "Cost / order (EGP)": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    st.caption(f"Source: {path}")
