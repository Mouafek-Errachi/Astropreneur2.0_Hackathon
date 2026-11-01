"""The HR assistant: a chat that answers from the company's documents, with sources, and keeps the conversation so
follow-up questions work. Admins add and remove the documents it reads."""
import streamlit as st

from api import call, can, require_login
from ui import esc, flash, page_header

require_login()
page_header("Ask HR", "Your HR assistant. It answers only from the company's documents and says which ones it used; "
                      "follow-up questions keep the context. Anything the documents do not cover goes to the HR team.")

EXAMPLES = ["How do I claim my expenses?", "How long is the probation period?", "How is overtime paid?",
            "What is my training budget?"]
DOC_TYPES = ["pdf", "docx", "odt", "rtf", "txt", "md", "html"]


def _pick_example() -> None:
    st.session_state.hr_question, st.session_state.hr_example = st.session_state.hr_example, None


def history() -> list[dict]:
    """The last turns, sent with each question so the assistant understands follow-ups."""
    turns = [{"role": m["role"], "text": m["text"] if m["role"] == "user" else m["answer"]}
             for m in st.session_state.get("hr_chat", [])]
    return turns[-6:]


def show_answer(msg: dict) -> None:
    st.write(msg["answer"])
    sources = msg["sources"]
    st.caption(f"Confidence: {msg['confidence']}" + (f" · {len(sources)} source(s)" if sources else " · no source"))
    if sources:
        with st.expander("Where this comes from", icon=":material/description:"):
            for s in sources:
                st.markdown(f"**{esc(s['title'])}** `{s['label']}`")
                st.caption(esc(s.get("excerpt", "")))
    if msg["flags"]:
        st.warning("This question contains text that looks like an instruction to the AI. It was treated as a "
                   "question only.", icon=":material/gpp_maybe:")
    if msg["escalate_to_hr"]:
        st.info("Not covered by the company documents: the HR team has been told.", icon=":material/support_agent:")


tabs = ["Chat", "Company documents"]
views = dict(zip(tabs, st.tabs(tabs)))

with views["Chat"]:
    chat = st.session_state.setdefault("hr_chat", [])
    if not chat:
        st.pills("Try one of these", EXAMPLES, key="hr_example", on_change=_pick_example)
    for msg in chat:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.text(msg["text"])
            else:
                show_answer(msg)
    if question := st.chat_input("Ask about the company: expenses, overtime, training, travel…") \
            or st.session_state.pop("hr_question", None):
        past = history()
        chat.append({"role": "user", "text": question})
        with st.spinner("Looking in the company documents..."):
            r = call("POST", "/qa/ask", json={"question": question, "history": past})
        if r:
            chat.append({"role": "assistant", **r})
        st.rerun()
    if chat and st.button("New conversation", icon=":material/add_comment:", type="tertiary"):
        st.session_state.hr_chat = []
        st.rerun()

with views["Company documents"]:
    docs = call("GET", "/qa/documents") or []
    st.caption(f"The assistant reads these {len(docs)} documents and nothing else.")
    st.dataframe([{"Document": d["title"], "Added": "built in" if d["kind"] == "built-in" else "uploaded by HR",
                   "Reference": d["doc_id"]} for d in docs], hide_index=True, width="stretch")
    if not can("qa:admin"):
        st.caption("Ask an admin to add a document.")
    else:
        if results := st.session_state.pop("doc_upload", None):
            for x in results:
                if x["accepted"]:
                    st.success(f"Added **{esc(x['title'])}** ({x['sections']} sections): the assistant can answer "
                               "from it now.", icon=":material/check_circle:")
                else:
                    st.error(f"{esc(x['filename'])} was not added: {esc(x['error'])}.", icon=":material/block:")
        with st.form("add_docs", clear_on_submit=True, border=True):
            st.markdown("**Add company documents**")
            files = st.file_uploader("Policies, handbooks, procedures… (PDF, Word, OpenDocument, RTF, text, Markdown, "
                                     "HTML)", type=DOC_TYPES, accept_multiple_files=True)
            st.caption("Each document is checked like a CV: file type, hidden text and instructions aimed at the AI. "
                       "The assistant trusts what it reads, so a suspicious document is refused.")
            if st.form_submit_button("Add to the assistant", type="primary", icon=":material/upload_file:") and files:
                with st.spinner("Reading and indexing..."):
                    r = call("POST", "/qa/documents", files=[("files", (f.name, f.getvalue())) for f in files])
                if r:
                    st.session_state.doc_upload = r
                    st.rerun()
        uploaded = [d for d in docs if d["kind"] == "uploaded"]
        if uploaded:
            left, right = st.columns([4, 1], vertical_alignment="bottom")
            gone = left.selectbox("Remove an uploaded document", uploaded, format_func=lambda d: d["title"])
            if right.button("Remove", icon=":material/delete:", width="stretch"):
                if call("DELETE", f"/qa/documents/{gone['doc_id']}"):
                    flash(f"Removed {gone['title']}: the assistant no longer uses it.", icon=":material/delete:")
                    st.rerun()
        with st.expander("Maintenance", icon=":material/settings:"):
            st.caption("Re-read every document, e.g. after editing a built-in policy file.")
            if st.button("Re-index documents"):
                if r := call("POST", "/qa/reindex"):
                    st.success(f"{r['chunks']} sections indexed")
