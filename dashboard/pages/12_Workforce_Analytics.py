"""Workforce analytics: team capacity, skills, gaps and the hiring pipeline, from records HR already keeps.
Read-only. Recruiters see team totals; managers and admins also see who holds what and the per-person workload."""
import altair as alt
import pandas as pd
import streamlit as st

import charts
from api import call, can, require_login
from ui import empty, esc, page_header

require_login()
page_header("Workforce analytics", "Where teams are stretched, which skills are thin, and how hiring is moving. "
                                   "Numbers are computed from HR records; nothing here tracks what people do.")
individual = can("skills:read")


def data_table(df: pd.DataFrame, view: str, label: str = "Show data") -> None:
    """The values behind a chart, readable without colour, and a CSV of what this role can see (logged)."""
    with st.expander(label, icon=":material/table:"):
        st.dataframe(df, hide_index=True, width="stretch")
        st.download_button("Download CSV", df.to_csv(index=False).encode(), f"{view}.csv", "text/csv",
                           key=f"dl_{view}", icon=":material/download:", type="tertiary",
                           on_click=lambda: call("POST", "/analytics/exported", json={"view": view}))


teams = call("GET", "/analytics/teams") or []
names = {t["team"]: t["name"] for t in teams}
team_order = [t["team"] for t in teams]

# ---------------------------------------------------------------- AI summary and snapshot
c1, c2 = st.columns([3, 1])
with c1:
    if st.button("Summarise with AI", icon=":material/auto_awesome:"):
        st.session_state.analytics_insights = call("POST", "/analytics/insights")
    if ins := st.session_state.get("analytics_insights"):
        with st.container(border=True):
            st.markdown(f"**{esc(ins['headline'])}**")
            for o in ins["observations"]:
                st.markdown(f"- {esc(o['text'])}")
            note = "Every number above was checked against the computed metrics."
            if ins["dropped"]:
                note += f" {ins['dropped']} sentence(s) quoting other numbers were removed."
            st.caption(note + (" (AI unavailable: rule-based summary.)" if ins["llm_fallback"] else ""))
with c2:
    if can("analytics:snapshot") and st.button("Save today's snapshot", icon=":material/save:",
                                               help="Stores today's team totals for the trend charts. n8n can do this "
                                                    "daily with an API key that holds analytics:snapshot."):
        if r := call("POST", "/analytics/snapshot"):
            st.toast(f"Snapshot saved for {r['day']} ({r['rows']} values).")

tab_names = ["Teams", "Skills", "Gaps", "Hiring pipeline"] + (["Workload"] if individual else [])
tabs = st.tabs(tab_names)

# ---------------------------------------------------------------- teams
with tabs[0]:
    if not teams:
        empty("No team yet.", "Teams and employees are seeded on first start.")
    flagged = [t for t in teams if t["flags"]]
    st.caption(f"{len(flagged)} of {len(teams)} teams flagged as possibly under-resourced." if teams else "")
    cols = st.columns(max(1, min(4, len(teams))))
    for i, t in enumerate(teams):
        with cols[i % len(cols)], st.container(border=True):
            st.markdown(f"**{esc(t['name'])}** · {t['people']} people")
            a, b = st.columns(2)
            a.metric("Booked", "—" if t["hidden"] else f"{round(t['utilisation'] * 100)}%",
                     help="Utilisation: project time booked / team capacity.")
            b.metric("Gap FTE", f"{t['gap_fte']:g}", help="Open skill gaps of confirmed projects, not covered internally.")
            if t["hidden"]:
                st.caption("Capacity figures hidden: fewer than 3 people (managers see them).")
            for f in t["flags"]:
                st.markdown(f":orange[:material/warning:] {esc(f)}")
            if not t["flags"]:
                st.markdown(":green[:material/check_circle:] No flag")

    shown = [t for t in teams if not t["hidden"]]
    if shown:
        st.subheader("Utilisation by team")
        df = pd.DataFrame([{"team": t["name"], "utilisation": t["utilisation"], "allocated FTE": t["allocated_fte"],
                            "free FTE": t["free_fte"], "people": t["people"], "over-allocated": t["over_allocated"]}
                           for t in shown])
        alert = (st.session_state.get("health") or {}).get("analytics_utilisation_alert", 0.9)
        charts.bars(df, "utilisation", "team", "share of capacity booked on projects", fmt="%", rule=alert,
                    rule_label=f"alert {round(alert * 100)}%", x_max=max(1.0, float(df["utilisation"].max())),
                    tooltip=["team", alt.Tooltip("utilisation:Q", format="%"), "allocated FTE", "free FTE", "over-allocated"])
        data_table(df, "team_utilisation")

    st.subheader("Trends")
    trend = call("GET", "/analytics/trends", params={"metric": "utilisation", "days": 30}) or []
    tdf = pd.DataFrame([r for r in trend if r["team"]])
    if tdf.empty or tdf["day"].nunique() < 2:
        st.caption("Trend lines appear once there are snapshots for 2 or more days.")
    else:
        tdf["team"] = tdf["team"].map(lambda t: names.get(t, t))
        present = [t for t in team_order if names[t] in set(tdf["team"])]
        charts.lines(tdf.rename(columns={"value": "utilisation"}), "day", "utilisation", "team",
                     [names[t] for t in present], [team_order.index(t) % 4 for t in present], "utilisation", fmt="%")
        data_table(tdf.pivot(index="day", columns="team", values="value").reset_index(), "utilisation_trend")

# ---------------------------------------------------------------- skills
with tabs[1]:
    team = st.selectbox("Team", [None] + team_order, format_func=lambda t: names.get(t, "All teams"), key="sk_team")
    sk = call("GET", "/analytics/skills", params={"team": team} if team else {}) or {"cells": [], "backlog": [], "hidden_teams": []}
    if sk["hidden_teams"]:
        st.caption("Not shown, fewer than 3 people: " + ", ".join(names.get(t, t) for t in sk["hidden_teams"]) + ".")
    if sk["cells"]:
        df = pd.DataFrame(sk["cells"])
        df["team"] = df["team"].map(lambda t: names.get(t, t))
        st.subheader("Validated people per skill")
        levels = {"depth": "senior or expert", "level_1": "beginner", "level_2": "working", "level_3": "senior",
                  "level_4": "expert", "unvalidated": "not validated"}
        held = df[df["holders"] > 0]  # a skill only claimed (not validated) is not a holder: it is in the backlog
        charts.heatmap(held, "skill", "team", "holders", "people", domain_max=float(held["holders"].max() or 1),
                       tooltip=["team", "skill", "holders"] + [alt.Tooltip(f"{k}:Q", title=t) for k, t in levels.items()])
        st.caption("Only validated skills count. Unvalidated entries are listed in the backlog below.")
        data_table(df[["team", "skill", "holders", "depth", "level_1", "level_2", "level_3", "level_4", "unvalidated"]],
                   "skills_by_team")
    else:
        empty("No skill to show.", "Managers add employee skills in Project staffing → Team skill matrix.")

    st.subheader("Skills that depend on one person")
    spofs = call("GET", "/analytics/skills/single-points") or []
    if spofs:
        st.dataframe([{"skill": s["skill"], "level needed": s["level"], "projects": ", ".join(s["projects"]),
                       **({"only holder": f"{s['holder_name']} ({names.get(s['holder_team'], s['holder_team'])})"}
                          if "holder_name" in s else {})} for s in spofs], hide_index=True, width="stretch")
        st.caption("A confirmed project needs these skills, and only one validated person has them at that level.")
    else:
        st.caption("None: no confirmed project depends on a single person for a skill.")
    if sk["backlog"]:
        st.subheader("Validation backlog")
        st.dataframe([{"team": names.get(b["team"], b["team"]), "unvalidated skills": b["unvalidated"],
                       "oldest": (b["oldest"] or "")[:10]} for b in sk["backlog"]], hide_index=True, width="stretch")

# ---------------------------------------------------------------- gaps
with tabs[2]:
    g = call("GET", "/analytics/gaps") or {}
    if not g.get("open_gaps") and not any(d["gaps"] for d in g.get("by_decision", [])):
        empty("No skill gap yet.", "Gaps appear when a project's requirements are confirmed in Project staffing.")
    else:
        a, b, c = st.columns(3)
        a.metric("Open gaps", g["open_gaps"])
        b.metric("Open gap FTE", f"{g['open_gap_fte']:g}")
        c.metric("Hiring", next((d["gaps"] for d in g["by_decision"] if d["status"] == "hiring"), 0))
        kinds = ["missing", "insufficient_level", "insufficient_capacity"]
        labels = {"missing": "missing", "insufficient_level": "level too low", "insufficient_capacity": "not enough capacity"}
        st.subheader("Open gap FTE by team and kind")
        rows = [{"team": names.get(r["team"], r["team"] or "no team"), "kind": labels[r["kind"]],
                 "kind_order": kinds.index(r["kind"]), "gap FTE": r["gap_fte"], "gaps": r["gaps"]}
                for r in g["by_team_kind"]]
        if rows:
            df = pd.DataFrame(rows)
            charts.stacked(df, "gap FTE", "team", "kind", [labels[k] for k in kinds], "gap FTE", "kind",
                           tooltip=["team", "kind", "gap FTE", "gaps"])
            data_table(df.drop(columns="kind_order"), "gaps_by_team")
        if g["top_skills"]:
            st.subheader("Most demanded skills")
            df = pd.DataFrame(g["top_skills"])
            charts.bars(df, "gap_fte", "skill", "open gap FTE", tooltip=["skill", "gap_fte"],
                        height=max(140, 32 * len(df)))
        st.subheader("What was decided")
        st.dataframe(pd.DataFrame(g["by_decision"]).rename(columns={"status": "decision", "gaps": "gaps"}),
                     hide_index=True, width="stretch")
        if g["gap_to_hire"]:
            st.caption("Gaps sent to hiring, and how far they got:")
            st.dataframe(g["gap_to_hire"], hide_index=True, width="stretch")

# ---------------------------------------------------------------- hiring pipeline
with tabs[3]:
    pl = call("GET", "/analytics/pipeline") or {}
    funnel = pl.get("funnel", [])
    if not any(f["total"] for f in funnel):
        empty("No application yet.", "CVs arrive through Screening, the inbox or the CV sources.")
    else:
        st.subheader("Applications by status")
        statuses = ["new", "screened", "needs_review", "shortlisted", "interview", "rejected"]
        top = max(1, max(f[s] for f in funnel for s in statuses))
        st.dataframe(pd.DataFrame([{"job": f["job"], "total": f["total"], **{s: f[s] for s in statuses}} for f in funnel]),
                     hide_index=True, width="stretch",
                     column_config={s: st.column_config.ProgressColumn(s.replace("_", " "), min_value=0, max_value=top,
                                                                       format="%d") for s in statuses})
    if pl.get("intake"):
        st.subheader("CVs received by source")
        df = pd.DataFrame([{"source": i["source"], "result": r, "result_order": n, "CVs": i[r]}
                           for i in pl["intake"] for n, r in enumerate(("accepted", "blocked"))])
        charts.stacked(df, "CVs", "source", "result", ["accepted", "blocked"], "CVs", "result",
                       tooltip=["source", "result", "CVs"], height=max(120, 40 * len(pl["intake"])))
        st.caption("Blocked = refused by the file guard (wrong type, hidden content, duplicates). See the AI Firewall.")
        data_table(df.drop(columns="result_order"), "intake_by_source")
    st.subheader("Approval queue")
    if pl.get("approvals"):
        st.dataframe([{"action": a["type"].replace("_", " "), "waiting": a["pending"],
                       "oldest (hours)": a["oldest_hours"]} for a in pl["approvals"]], hide_index=True, width="stretch")
    else:
        st.caption("Nothing waiting for approval.")
    if pl.get("time_in_stage"):
        st.subheader("Time to reach each status")
        st.dataframe([{"status": t["status"].replace("_", " "), "median days since the CV arrived": t["median_days"],
                       "applications": t["applications"]} for t in pl["time_in_stage"]], hide_index=True, width="stretch")

# ---------------------------------------------------------------- workload (managers and admins)
if individual:
    with tabs[4]:
        st.caption("Per-person data: visible to managers and admins only. Opening it is recorded in the audit log.")
        team = st.selectbox("Team", [None] + team_order, format_func=lambda t: names.get(t, "All teams"), key="wl_team")
        wl = call("GET", "/analytics/workload", params={"team": team} if team else {}) or {"projects": [], "rows": []}
        if not wl["rows"]:
            empty("Nobody in this team.")
        else:
            cells = [{"person": f"{r['name']} · {names.get(r['team'], r['team'])}", "project": p, "allocation %": pct}
                     for r in wl["rows"] for p, pct in r["allocations"].items()]
            if cells:
                st.subheader("Allocation per person and project")
                charts.heatmap(pd.DataFrame(cells), "project", "person", "allocation %", "allocation %",
                               tooltip=["person", "project", "allocation %"], domain_max=100)
            table = [{"person": r["name"], "team": names.get(r["team"], r["team"]), "total %": r["total"],
                      "status": "⚠️ over 100%" if r["total"] > 100 else ("free" if r["total"] == 0 else ""),
                      **r["allocations"]} for r in wl["rows"]]
            st.dataframe(pd.DataFrame(table).fillna("—"), hide_index=True, width="stretch")
