"""
CTWA Marketing Intelligence -- the merchant app.

    python3 -m src.pipeline      # writes outputs/trace.db
    streamlit run app.py

Pages: Dashboard (decisions only) · Explore (filter/search everything) ·
Report (one id, every step behind it, chat on the side) · Ask (general chat).
Every page reads outputs/trace.db; nothing here computes a decision.
The previous single-page scoreboard is kept as app_legacy.py.
"""

import streamlit as st

from src.ui import pages

st.set_page_config(page_title="CTWA Marketing Intelligence", layout="wide")

nav = {
    "dashboard": st.Page(pages.dashboard, title="Dashboard", icon=":material/dashboard:", default=True,
                         url_path="dashboard"),
    "explore": st.Page(pages.explorer, title="Explore", icon=":material/search:", url_path="explore"),
    "report": st.Page(pages.report, title="Report", icon=":material/description:", url_path="report"),
    "chat": st.Page(pages.chat, title="Ask", icon=":material/chat:", url_path="ask"),
}
pages.NAV.update(nav)
st.navigation(list(nav.values())).run()
