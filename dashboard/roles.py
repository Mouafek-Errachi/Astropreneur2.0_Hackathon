"""What each HR role can do, as served by the API (/auth/roles), so the dashboard never keeps its own copy."""
import requests
import streamlit as st

from api import API_URL


@st.cache_data(ttl=300, show_spinner=False)
def public_roles() -> dict:
    """{"summaries": {role: sentence}, "access": [{what, recruiter, manager, admin}]}; empty when the API is down."""
    try:
        data = requests.get(f"{API_URL}/auth/roles", timeout=5).json()
    except (requests.RequestException, ValueError):
        return {"summaries": {}, "access": []}
    return {"summaries": {r["role"]: r["summary"] for r in data["roles"]}, "access": data["access"]}


def role_table(access: list[dict], highlight: str | None = None) -> None:
    """One row per area, a ✅ or — per role. `highlight` puts the viewer's own role in the column title."""
    roles = ("recruiter", "manager", "admin")
    head = " | ".join(f"**{r.capitalize()}**" + (" (you)" if r == highlight else "") for r in roles)
    rows = [f"| {a['what']} | " + " | ".join("✅" if a[r] else "—" for r in roles) + " |" for a in access]
    # a markdown table wraps long lines, so it stays readable in the narrow log-in column and the sidebar
    st.markdown("\n".join([f"| Access | {head} |", "|---|:-:|:-:|:-:|", *rows]))
