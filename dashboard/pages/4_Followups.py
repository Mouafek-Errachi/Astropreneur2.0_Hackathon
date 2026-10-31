import streamlit as st

from api import call, require_login

require_login()
st.header("Follow-ups")

if st.button("Scan for follow-ups"):
    r = call("POST", "/followups/scan")
    if r:
        st.success(f"{r['created']} new follow-ups")

for f in call("GET", "/followups") or []:
    col1, col2 = st.columns([4, 1])
    col1.write(f"{'✅' if f['status'] == 'done' else '🕒'} {f['title']}")
    if f["status"] == "open" and col2.button("Done", key=f"f{f['id']}"):
        call("POST", f"/followups/{f['id']}/done")
        st.rerun()
