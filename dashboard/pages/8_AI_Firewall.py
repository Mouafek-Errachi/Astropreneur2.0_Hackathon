"""AI Firewall visualizer. Polls /security/events (a React client would use the /ws/security WebSocket)."""
import pandas as pd
import streamlit as st

from api import call, can, require_login
from ui import empty, human, page_header, when

require_login()
page_header("AI Firewall", "Everything the guard caught before it reached an agent: prompt injections, hidden text, "
                           "blocked files and quarantined emails.")

SEVERITY = {"high": "🔴 high", "medium": "🟠 medium", "low": "🟡 low"}
SEVERITY_COLOR = {"high": "#ef4444", "medium": "#f59e0b", "low": "#94a3b8"}


def details(d) -> str:
    """{'flags': [{'type': 'instruction_override', ...}], 'sender': 'x'} -> 'Flags: Instruction override; Sender: x'."""
    if not isinstance(d, dict):
        return str(d)
    parts = []
    for k, v in d.items():
        if isinstance(v, list):
            v = ", ".join(human(x.get("type", "")) if isinstance(x, dict) else str(x) for x in v)
        parts.append(f"{human(k)}: {v}")
    return "; ".join(parts)


auto = st.toggle("Live: refresh every 5 seconds", value=False)


@st.fragment(run_every=5 if auto else None)
def feed():
    ev = call("GET", "/security/events", params={"limit": 300}) or []
    if not ev:
        empty("No security events yet.", "Load the demo data on the Home page to see the guard at work.")
        return
    df = pd.DataFrame(ev)
    c = st.columns(5)
    c[0].metric("Events", len(df), border=True)
    c[1].metric("🔴 High severity", int((df.severity == "high").sum()), border=True)
    c[2].metric("Injections", int(df.kind.isin(["injection", "hidden_text"]).sum()), border=True,
                help="Instructions aimed at the AI, including text hidden in a file.")
    c[3].metric("Quarantined", int((df.kind == "quarantined").sum()), border=True)
    c[4].metric("Blocked files", int((df.kind == "blocked_file").sum()), border=True)
    st.markdown("**Where the attacks came from**")
    by_source = df.assign(source=df.source.map(human)).groupby(["source", "severity"]).size().unstack(fill_value=0)
    levels = [s for s in SEVERITY_COLOR if s in by_source.columns]  # red for high, whatever order pandas picks
    st.bar_chart(by_source[levels], color=[SEVERITY_COLOR[s] for s in levels], horizontal=True, height=220)
    st.dataframe(pd.DataFrame({"When": df.ts.map(when), "Severity": df.severity.map(lambda s: SEVERITY.get(s, s)),
                               "What": df.kind.map(human), "Source": df.source.map(human),
                               "Details": df.detail.map(details)}),
                 width="stretch", hide_index=True, column_config={"Details": st.column_config.TextColumn(width="large")})


feed()

if can("audit:read"):
    st.subheader("Audit log")
    st.caption("Every action is chained to the previous one by a hash, so an edited or deleted entry is detected.")
    v = call("GET", "/audit/verify")
    if v:
        if v["chain_valid"]:
            st.success("Chain intact: no entry was modified or deleted.", icon=":material/verified:")
        else:
            st.error(f"Chain broken at entry {v['first_broken_id']}.", icon=":material/error:")
    log = call("GET", "/audit", params={"limit": 200}) or []
    st.dataframe([{"When": when(r["ts"]), "Who": r["actor"], "Event": human(r["event"]), "Target": r["target"],
                   "Details": details(r["detail"]), "Hash": r["hash"]} for r in log],
                 width="stretch", hide_index=True, column_config={"Details": st.column_config.TextColumn(width="large")})
