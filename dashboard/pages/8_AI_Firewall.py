"""AI Firewall visualizer. Polls /security/events (a React client would use the /ws/security WebSocket)."""
import pandas as pd
import streamlit as st

from api import call, require_login

require_login()
st.header("🛡️ AI Firewall")

auto = st.toggle("Live (refresh every 5 s)", value=False)


@st.fragment(run_every=5 if auto else None)
def feed():
    ev = call("GET", "/security/events", params={"limit": 300}) or []
    if not ev:
        st.success("No security events yet. Load the demo data to see the guard at work.")
        return
    df = pd.DataFrame(ev)
    c = st.columns(5)
    c[0].metric("Events", len(df))
    c[1].metric("🔴 High", int((df.severity == "high").sum()))
    c[2].metric("Injections", int(df.kind.isin(["injection", "hidden_text"]).sum()))
    c[3].metric("Quarantined", int((df.kind == "quarantined").sum()))
    c[4].metric("Blocked files", int((df.kind == "blocked_file").sum()))
    st.bar_chart(df.groupby(["source", "severity"]).size().unstack(fill_value=0))
    st.dataframe(df[["ts", "severity", "kind", "source", "detail"]], width="stretch", hide_index=True)


feed()

me = st.session_state.get("me") or call("GET", "/auth/me")
if "audit:read" in me["scopes"]:
    st.subheader("Audit log (hash-chained)")
    v = call("GET", "/audit/verify")
    if v:
        (st.success if v["chain_valid"] else st.error)(
            "Chain intact: no entry was modified or deleted." if v["chain_valid"] else f"Chain broken at entry {v['first_broken_id']}")
    st.dataframe(call("GET", "/audit", params={"limit": 200}) or [], width="stretch", hide_index=True)
