"""Chat component shared by the Report side panel and the Chat page."""

import uuid

import streamlit as st

from ..chat.bot import ChatBot
from ..chat.log import feedback

ROUTE_NOTE = {"refuse_decision": "Explains decisions only — does not make new ones.",
              "refuse_scope": "Outside what this assistant covers.",
              "not_yet": "Needs conversation text — coming with conversation insights."}


@st.cache_resource
def get_bot() -> ChatBot:
    return ChatBot()


def _session_id():
    st.session_state.setdefault("session_id", uuid.uuid4().hex[:12])
    return st.session_state.session_id


def _history_for_bot(messages):
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def _render_assistant(m, key, i, on_pick):
    r = m.get("reply")
    if r is None:
        st.markdown(m["content"])
        return
    if r.route == "busy":
        st.warning(m["content"].split("\n")[0])
        rest = "\n".join(m["content"].split("\n")[1:])
        if rest:
            st.caption("Meanwhile, the stored decision:")
            st.markdown(rest)
    elif r.fallback in ("guard_failed", "llm_error", "no_key"):
        st.caption("I couldn't write a checked explanation, so here is the stored decision:")
        st.markdown(m["content"])
    else:
        st.markdown(m["content"])
        if r.route in ROUTE_NOTE:
            st.caption(ROUTE_NOTE[r.route])
    if r.options:
        cols = st.columns(min(len(r.options), 2))
        for j, o in enumerate(r.options):
            if cols[j % len(cols)].button(f"{o['name']} ({o['level']})", key=f"{key}_opt_{i}_{j}",
                                          use_container_width=True):
                on_pick(m["question"], o["id"])
    if r.route == "answer" and not r.fallback:
        tools = sorted({f["tool"] for f in r.facts})
        with st.expander("How I answered", expanded=False):
            st.caption(f"Sources: {', '.join(tools) or 'stored decision'} · checks: "
                       f"{'passed' if r.guard.get('ok') else 'failed'} · {r.latency_ms / 1000:.1f} s")
            if r.answer_meta.get("first_issues"):
                st.caption("First draft was rewritten: " + "; ".join(r.answer_meta["first_issues"]))
    if r.log_id and r.route not in ("clarify", "busy"):
        fb = st.session_state.setdefault(f"fb_{r.log_id}", None)
        log_path = get_bot().log_path          # same file the bot logged to
        if fb is None:
            c1, c2, _ = st.columns([1, 1, 8])
            if c1.button("👍", key=f"{key}_up_{i}", help="Helpful"):
                feedback(r.log_id, 1, path=log_path); st.session_state[f"fb_{r.log_id}"] = 1; st.rerun()
            if c2.button("👎", key=f"{key}_down_{i}", help="Not helpful"):
                feedback(r.log_id, -1, path=log_path); st.session_state[f"fb_{r.log_id}"] = -1; st.rerun()
        else:
            st.caption("Thanks for the feedback." if fb == 1 else "Thanks — noted as not helpful.")


def chat_panel(key: str, selected_id: str | None = None, page: str = "chat",
               height: int | None = None, placeholder: str = "Ask about the decisions…",
               starters: list[str] | None = None):
    state = f"chat_{key}"
    st.session_state.setdefault(state, [])
    messages = st.session_state[state]
    pending_key = f"pending_{key}"

    def on_pick(question, entity_id):
        st.session_state[pending_key] = (question, entity_id)
        st.rerun()

    box = st.container(height=height, border=True) if height else st.container()
    with box:
        if not messages and starters:
            st.caption("Try asking:")
            for j, s in enumerate(starters):
                if st.button(s, key=f"{key}_start_{j}", use_container_width=True):
                    st.session_state[pending_key] = (s, selected_id)
                    st.rerun()
        for i, m in enumerate(messages):
            with st.chat_message(m["role"]):
                if m["role"] == "user":
                    st.markdown(m["content"])
                else:
                    _render_assistant(m, key, i, on_pick)

    question = st.chat_input(placeholder, key=f"input_{key}")
    target, shown = selected_id, None
    if pending_key in st.session_state:
        question, target = st.session_state.pop(pending_key)
        if target and target != selected_id:   # picked from "which one do you mean?"
            e = get_bot().index.by_id.get(target)
            shown = f"{question} → *{e.name}*" if e else None
    if question:
        with box:
            with st.chat_message("user"):
                st.markdown(shown or question)
            with st.chat_message("assistant"):
                with st.spinner("Checking the stored decisions…"):
                    reply = get_bot().ask(question, target, _history_for_bot(messages),
                                          session_id=_session_id(), page=page)
        messages.append({"role": "user", "content": shown or question})
        messages.append({"role": "assistant", "content": reply.text, "reply": reply, "question": question})
        st.rerun()

    if messages and st.button("Clear chat", key=f"clear_{key}"):
        st.session_state[state] = []
        st.rerun()
