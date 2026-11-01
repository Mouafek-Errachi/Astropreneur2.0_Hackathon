import streamlit as st

from api import call, can, require_login
from ui import page_header

require_login()
page_header("Ask HR", "Answers come only from the company policy documents, with their sources. Anything they do not "
                      "cover goes to the HR team.")

EXAMPLES = ["How do I claim my expenses?", "How long is the probation period?", "How is overtime paid?",
            "What is my training budget?"]


def _pick_example() -> None:
    st.session_state.hr_question, st.session_state.hr_example = st.session_state.hr_example, None


tabs = ["Ask a question"] + (["Send a request to HR"] if can("tickets:create") else [])
views = st.tabs(tabs) if len(tabs) > 1 else [st.container()]

with views[0]:
    chat = st.session_state.setdefault("hr_chat", [])
    st.pills("Examples", EXAMPLES, key="hr_example", on_change=_pick_example,
             label_visibility="visible" if not chat else "collapsed")
    for msg in chat:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.text(msg["text"])
                continue
            st.write(msg["answer"])
            st.caption(f"Confidence: {msg['confidence']} · Sources: "
                       + (", ".join(s["label"] for s in msg["sources"]) or "none"))
            if msg["flags"]:
                st.warning("This question contains text that looks like an instruction to the AI. It was treated as a "
                           "question only.", icon=":material/gpp_maybe:")
            if msg["escalate_to_hr"]:
                st.info("Not covered by the policies: the HR team has been told.", icon=":material/support_agent:")
    if question := st.chat_input("Ask about expenses, overtime, training, travel…") or st.session_state.pop("hr_question", None):
        chat.append({"role": "user", "text": question})
        with st.spinner("Looking in the policies..."):
            r = call("POST", "/qa/ask", json={"question": question})
        if r:
            chat.append({"role": "assistant", **r})
        st.rerun()
    if chat and st.button("Clear the conversation", icon=":material/delete_sweep:", type="tertiary"):
        st.session_state.hr_chat = []
        st.rerun()

if len(views) > 1:
    with views[1], st.form("ticket", clear_on_submit=True):
        st.caption("HR receives your request with a drafted answer. A person reviews it before anything is sent to you.")
        text = st.text_area("Your request", placeholder="e.g. I need a certificate of employment for my visa appointment.")
        if st.form_submit_button("Send request", type="primary", icon=":material/send:"):
            if not text.strip():
                st.warning("Write your request first.")
            elif r := call("POST", "/hr/tickets", json={"request_text": text}):
                st.success(f"{r['message']} (category: {r['category']}, priority: {r['priority']})",
                           icon=":material/check_circle:")

if can("qa:admin"):
    with st.expander("Policy index (admin)", icon=":material/settings:"):
        st.caption("Re-read the policy documents after you add or change one.")
        if st.button("Re-index policies"):
            if r := call("POST", "/qa/reindex"):
                st.success(f"{r['chunks']} policy sections indexed")
