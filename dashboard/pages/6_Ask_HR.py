import streamlit as st

from api import call, require_login

require_login()
me = st.session_state.get("me") or call("GET", "/auth/me")
st.header("Ask HR")

q = st.text_input("Ask a question about HR policies (leave, payroll, remote work, benefits…)")
if q and st.button("Ask"):
    r = call("POST", "/qa/ask", json={"question": q})
    if r:
        st.write(r["answer"])
        st.caption(f"Confidence: {r['confidence']} · Sources: {', '.join(s['label'] for s in r['sources']) or '—'}")
        if r["escalate_to_hr"]:
            st.warning("Not covered by the policies: flagged for the HR team.")

if "tickets:create" in me["scopes"]:
    st.subheader("Open a request to HR")
    text = st.text_area("Your request")
    if st.button("Send request") and text:
        r = call("POST", "/hr/tickets", json={"request_text": text})
        if r:
            st.success(f"{r['message']} (category: {r['category']}, priority: {r['priority']})")

if "qa:admin" in me["scopes"] and st.button("Re-index policies"):
    r = call("POST", "/qa/reindex")
    if r:
        st.success(f"{r['chunks']} policy sections indexed")
