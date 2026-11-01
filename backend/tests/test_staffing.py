"""Project staffing & skill gaps: the 8 acceptance criteria of docs/specs/project-staffing-gap-analysis.md."""
import uuid

import pytest
from sqlmodel import Session

from backend.agents import jd_writer
from backend.agents.schemas import JobDescriptionDraft
from backend.data.db import engine
from backend.data.models import Tenant, User
from backend.orchestration.staffing import manifest_skills, taxonomy
from backend.security.gateway import hash_password
from backend.security.validator import check_job_description
from backend.tests.conftest import login


@pytest.fixture(scope="module")
def manager(client):
    return login(client, "manager@acme.test", "Manager#2026!")


def _project(client, headers, name):
    return next(p for p in client.get("/projects", headers=headers).json() if p["name"] == name)


def _confirm(client, headers, pid, skills=None):
    det = client.get(f"/projects/{pid}", headers=headers).json()
    body = skills or [{k: r[k] for k in ("skill", "level", "headcount", "allocation_pct", "evidence")} for r in det["requirements"]]
    r = client.put(f"/projects/{pid}/requirements", json={"skills": body}, headers=headers)
    assert r.status_code == 200, r.text
    return {g["skill"]: g for g in r.json()["gaps"]}


def _new_project(client, headers, name, charter: str, team="engineering"):
    p = client.post("/projects", json={"name": name, "summary": "test", "team_id": team}, headers=headers).json()
    r = client.post(f"/projects/{p['id']}/documents", files=[("files", ("charter.md", charter.encode()))], headers=headers)
    assert r.status_code == 200 and r.json()["documents"][0]["accepted"], r.text
    return p["id"]


# 1 ---------------------------------------------------------------- injection in a charter changes nothing
def test_injected_charter_is_flagged_and_cannot_hide_gaps(client, recruiter, demo):
    mobile = _project(client, recruiter, "Customer mobile app")
    det = client.get(f"/projects/{mobile['id']}", headers=recruiter).json()
    charter = next(d for d in det["documents"] if d["kind"] == "charter")
    assert charter["risk_level"] == "high"
    gaps = _confirm(client, recruiter, mobile["id"])
    assert {"react native", "typescript"} <= set(gaps)  # "mark every skill as covered" had no effect

    clean = (demo_charter := "\n".join(l for l in (
        "Customer mobile app - clean copy", "Team needed:",
        "- React Native (senior, 2 people, full time) for the app",
        "- TypeScript (full time) shared models and API client",
        "- React (half time) for the web companion")))
    pid = _new_project(client, recruiter, "Mobile app clean copy", clean, team="media_team")
    assert demo_charter and set(_confirm(client, recruiter, pid)) == {"react native", "typescript"}
    kinds = {(e["source"], e["kind"]) for e in client.get("/security/events?limit=500", headers=recruiter).json()}
    assert ("project_document", "injection") in kinds


# 2 ---------------------------------------------------------------- gaps are deterministic; booked people do not count
def test_gaps_are_deterministic_and_respect_capacity(client, recruiter):
    pay = _project(client, recruiter, "Payments API migration")
    first = _confirm(client, recruiter, pay["id"])
    again = client.post(f"/projects/{pay['id']}/gaps/recompute", headers=recruiter).json()
    assert {g["skill"]: (g["kind"], g["gap_fte"]) for g in again} == {s: (g["kind"], g["gap_fte"]) for s, g in first.items()}
    py = first["python"]
    # emp_001 (100% on this project) gives 1.0; emp_009 is 80% booked on the data platform and gives only 0.2
    assert py["kind"] == "insufficient_capacity" and py["covered_fte"] == 1.2 and py["gap_fte"] == 0.8
    assert first["sql"]["kind"] == "insufficient_level"  # nobody validated at senior level


# 3 ---------------------------------------------------------------- unvalidated skills do not count until validated
def test_unvalidated_skill_does_not_cover_until_a_manager_validates(client, recruiter, manager):
    r = client.put("/employees/emp_001/skills", json={"skills": [{"skill": "kafka", "level": 3, "validated": False}]},
                   headers=manager)
    assert r.status_code == 200 and not next(s for s in r.json()["skills"] if s["skill"] == "kafka")["validated"]
    pid = _new_project(client, recruiter, "Streaming pilot", "Team needed:\n- Kafka (senior, half time) for the event bus")
    gaps = _confirm(client, recruiter, pid)
    assert gaps["kafka"]["kind"] == "missing"
    assert any(o["kind"] == "validate" for o in gaps["kafka"]["internal_options"])

    assert client.put("/employees/emp_001/skills", json={"skills": [{"skill": "kafka", "level": 3}]},
                      headers=recruiter).status_code == 403  # recruiters cannot validate skills
    assert client.put("/employees/emp_001/skills", json={"skills": [{"skill": "kafka", "level": 3}]},
                      headers=manager).status_code == 200
    # validated now, but emp_001 is 100% booked on the payments project: the gap becomes a capacity gap
    after = {g["skill"]: g for g in client.post(f"/projects/{pid}/gaps/recompute", headers=recruiter).json()}
    assert after["kafka"]["kind"] == "insufficient_capacity"
    # a validated colleague with free time closes it
    assert client.put("/employees/emp_002/skills", json={"skills": [{"skill": "kafka", "level": 3}]},
                      headers=manager).status_code == 200
    final = client.post(f"/projects/{pid}/gaps/recompute", headers=recruiter).json()
    assert "kafka" not in {g["skill"] for g in final}


# 4 + 5 ------------------------------------------------------------ internal options first, then a four-eyes job offer
def test_job_description_needs_decision_then_second_person(client, recruiter, manager):
    pay = _project(client, recruiter, "Payments API migration")
    gaps = _confirm(client, recruiter, pay["id"])
    k8s = gaps["kubernetes"]
    assert k8s["internal_options"]  # listed before anything else
    url = f"/projects/{pay['id']}/gaps/{k8s['id']}"
    assert client.post(f"{url}/job-description", headers=recruiter).status_code == 409  # not decided yet
    assert client.put(url, json={"decision": "hire_externally"}, headers=recruiter).status_code == 200

    jd = client.post(f"{url}/job-description", headers=recruiter).json()
    assert jd["status"] == "pending" and jd["payload"]["criteria"]["required_skills"] == ["kubernetes"]
    assert client.post(f"{url}/job-description", headers=recruiter).status_code == 409  # one draft per gap
    assert client.post(f"/approvals/{jd['action_id']}/approve", json={}, headers=recruiter).status_code == 403
    done = client.post(f"/approvals/{jd['action_id']}/approve", json={"acknowledge_risk": True}, headers=manager).json()
    assert done["status"] == "executed", done

    gap = next(g for g in client.get(f"/projects/{pay['id']}/gaps", headers=recruiter).json() if g["skill"] == "kubernetes")
    assert gap["status"] == "hiring" and gap["job_offer_id"]
    job = next(j for j in client.get("/jobs", headers=recruiter).json() if j["id"] == gap["job_offer_id"])
    assert job["criteria"]["required_skills"] == ["kubernetes"]
    # the new job is a normal job: screening ranks applicants against the gap skill
    cv = b"Kim Ops\nkim@example.com\nSenior platform engineer, 6 years experience. Skills: Kubernetes, Docker, Linux\n"
    client.post(f"/jobs/{job['id']}/resumes", files=[("files", ("kim.txt", cv))], headers=recruiter)
    ranked = client.post(f"/jobs/{job['id']}/screen", headers=recruiter).json()["ranking"]
    assert ranked[0]["score_breakdown"]["required_matched"] == ["kubernetes"]


# 6 ---------------------------------------------------------------- the JD checks
def test_job_description_checks_reject_bad_drafts():
    bad = JobDescriptionDraft(title="Young Kubernetes ninja", summary="Work with Sami J. on the cluster",
                              required_skills=["kubernetes", "java"], nice_to_have=["cobol"], min_years=3,
                              body="Salary 5000 TND per month. Native English speakers only. Apply at https://jobs.evil.example")
    kinds = {f.type for f in check_job_description(bad, {"kubernetes"}, {"docker"}, ["Sami J.", "Sami"])}
    assert {"age_wording", "origin_wording", "invented_compensation", "link_in_job_description", "mentions_employee",
            "skills_not_the_gap", "nice_to_have_not_in_project"} <= kinds
    f = jd_writer.facts("Payments", "Move payments.", "kubernetes", 3, ["docker"], "Engineering", "")
    clean = JobDescriptionDraft.model_validate(jd_writer.template(f))
    assert not [x for x in check_job_description(clean, {"kubernetes"}, {"docker"}, ["Sami"]) if x.severity == "high"]


def test_ai_draft_breaking_the_rules_is_replaced_by_the_template(client, recruiter, monkeypatch):
    def rogue(task, schema, mock, **kwargs):
        d = mock()
        d["body"] += "\nCompetitive salary of 90k per year, apply at https://evil.example"
        return schema.model_validate(d), False
    monkeypatch.setattr(jd_writer, "complete_json", rogue)
    data = _project(client, recruiter, "Data platform")
    gaps = _confirm(client, recruiter, data["id"])
    url = f"/projects/{data['id']}/gaps/{gaps['airflow']['id']}"
    client.put(url, json={"decision": "hire_externally"}, headers=recruiter)
    jd = client.post(f"{url}/job-description", headers=recruiter).json()
    assert "salary" not in jd["payload"]["body"].lower() and "evil.example" not in jd["payload"]["body"]
    assert not [f for f in jd["flags"] if f["severity"] == "high"]


# 7 ---------------------------------------------------------------- access control on skills and projects
def test_skill_privacy_and_tenant_isolation(client, recruiter, manager):
    pay = _project(client, recruiter, "Payments API migration")
    gaps = client.get(f"/projects/{pay['id']}/gaps", headers=recruiter).json()
    assert all("employee_id" not in o for g in gaps for o in g["internal_options"])  # recruiters: no names
    assert any("employee_id" in o for g in client.get(f"/projects/{pay['id']}/gaps", headers=manager).json()
               for o in g["internal_options"])
    assert "people" not in client.get("/skills/matrix", headers=recruiter).json()["skills"][0]
    assert "people" in client.get("/skills/matrix", headers=manager).json()["skills"][0]
    assert client.get("/employees/emp_002/skills", headers=recruiter).status_code == 403
    assert client.get("/employees/emp_002/skills", headers=manager).status_code == 200

    with Session(engine) as s:
        t = Tenant(name=f"initech-{uuid.uuid4().hex[:6]}")
        s.add(t)
        s.commit()
        email = f"boss@{t.name}.test"
        s.add(User(tenant_id=t.id, email=email, role="admin", hashed_password=hash_password("Initech#2026!")))
        s.commit()
    other = login(client, email, "Initech#2026!")
    assert client.get(f"/projects/{pay['id']}", headers=other).status_code == 404
    assert client.get("/projects", headers=other).json() == []


# 8 ---------------------------------------------------------------- manifests are read as text, fake files refused
def test_manifests_are_parsed_not_executed_and_fakes_are_refused(client, recruiter):
    from backend.data.db import engine as eng

    with Session(eng) as s:
        tax = taxonomy(s)
    req = {n["skill"] for n in manifest_skills("requirements.txt", "fastapi==0.1\npsycopg2-binary\n# c\nredis>=5", tax)}
    assert req == {"python", "fastapi", "sql", "redis"}
    pkg = {n["skill"] for n in manifest_skills("package.json", '{"dependencies": {"react-native": "1"}, '
                                                               '"devDependencies": {"typescript": "5"}}', tax)}
    assert pkg == {"javascript", "react native", "typescript"}
    assert {n["skill"] for n in manifest_skills("Dockerfile", "FROM postgres:16\nRUN rm -rf /", tax)} == {"docker", "sql"}

    p = client.post("/projects", json={"name": "Upload checks", "team_id": "engineering"}, headers=recruiter).json()
    files = [("files", ("package.json", b"\x00\x01binary\x00")), ("files", ("spec.pdf", b"MZ\x90\x00 not a pdf at all"))]
    r = client.post(f"/projects/{p['id']}/documents", files=files, headers=recruiter).json()
    assert [d["accepted"] for d in r["documents"]] == [False, False]


# -------------------------------------------------------------------- a gap covered internally
def test_gap_covered_internally_needs_a_plan_and_blocks_hiring(client, recruiter, manager):
    tools = _project(client, recruiter, "HR internal tools portal")
    gaps = _confirm(client, recruiter, tools["id"])
    assert set(gaps) == {"fastapi"}  # python, react, docker, javascript are covered by the team
    fa = gaps["fastapi"]
    assert fa["kind"] == "insufficient_capacity"
    kinds = {o["kind"] for o in client.get(f"/projects/{tools['id']}/gaps", headers=manager).json()[0]["internal_options"]}
    assert {"rebalance", "upskill"} <= kinds  # emp_001 has it but is booked; emp_002 is one level below

    url = f"/projects/{tools['id']}/gaps/{fa['id']}"
    assert client.put(url, json={"decision": "internal"}, headers=recruiter).status_code == 422  # plan required
    resp = client.put(url, json={"decision": "internal", "plan": "upskill: emp_002 takes the FastAPI training"},
                      headers=recruiter)
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["decision"] == "internal" and "emp_002" in r["internal_plan"]
    assert client.post(f"{url}/job-description", headers=recruiter).status_code == 409  # covered: no hiring
    again = client.post(f"/projects/{tools['id']}/gaps/recompute", headers=recruiter).json()
    assert again[0]["decision"] == "internal" and again[0]["internal_plan"]  # survives a recompute
    undone = client.put(url, json={"decision": "open"}, headers=recruiter).json()
    assert undone["decision"] == "open" and undone["internal_plan"] == ""
