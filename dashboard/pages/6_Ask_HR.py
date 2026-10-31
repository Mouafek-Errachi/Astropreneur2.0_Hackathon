import streamlit as st

from api import call, require_login

require_login()
st.header("Ask HR (policy Q&A)")

if st.button("Re-index policies (admin)"):
    r = call("POST", "/qa/reindex")
    if r:
        st.success(f"{r['chunks']} chunks indexed")

q = st.text_input("Your question")
if q and st.button("Ask"):
    r = call("POST", "/qa/ask", json={"question": q})
    if r:
        st.write(r["answer"])
        st.caption(f"Confidence: {r['confidence']} · Sources: {', '.join(c['source'] for c in r['citations']) or '—'}")
        if r["escalate_to_hr"]:
            st.warning("Escalated to the HR team.")
