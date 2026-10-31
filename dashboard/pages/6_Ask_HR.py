import streamlit as st

from api import call, is_admin, require_login

require_login()
st.header("Ask HR (policy Q&A)")
st.caption("Answers come only from company policy documents, with sources. Anything else is escalated to HR.")

if is_admin() and st.button("Re-index policies"):
    r = call("POST", "/qa/reindex")
    if r:
        st.success(f"{r['chunks']} chunks indexed")

q = st.text_input("Your question")
if q and st.button("Ask"):
    r = call("POST", "/qa/ask", json={"question": q})
    if r:
        st.write(r["answer"])
        st.caption(f"Confidence: {r['confidence']} · Sources: "
                   f"{', '.join(c['source'] + ' / ' + c['heading'] for c in r['citations']) or '—'}")
        if r["escalate_to_hr"]:
            st.warning("Escalated to the HR team.")
        if r["flags"]:
            st.warning("This question contains text that looks like an instruction to the AI. It was treated as a question only.")
