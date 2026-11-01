"""The recruiter's request in their own words -> ranking criteria."""
import pytest

from backend.agents.brief import _mock, parse_brief


@pytest.mark.parametrize("text, required, nice, years", [
    ("i am looking for people with skill python sql it is better to have linux and 3 years of experience minimum",
     ["python", "sql"], ["linux"], 3),
    ("Must know Java and Docker, Kubernetes is a plus. 5+ years.", ["java", "docker"], ["kubernetes"], 5),
    ("JavaScript and React. Ideally AWS, GCP", ["javascript", "react"], ["aws", "gcp"], 0),
    ("Linux is nice to have and Python is required", ["python"], ["linux"], 0),
    ("Better to have figma, but SQL is needed. Machine learning, 2 ans d'expérience", ["sql", "machine learning"],
     ["figma"], 2),
])
def test_rules_read_required_nice_and_years(text, required, nice, years):
    crit = _mock(text, [])
    assert (crit["required_skills"], crit["nice_to_have"], crit["min_years"]) == (required, nice, years)


def test_known_skills_are_found_and_common_words_are_not():
    crit, fallback = parse_brief("It is better to have Docker. We need Terraform", ["Terraform", "IT"])
    assert not fallback and crit.required_skills == ["terraform"] and crit.nice_to_have == ["docker"]


def test_criteria_from_text_endpoint(client, recruiter, reader):
    job = client.post("/jobs", json={"title": "Brief Job", "required_skills": ["azure"]}, headers=recruiter).json()
    url = f"/jobs/{job['id']}/criteria/from-text"
    r = client.post(url, json={"text": "Python and SQL required, Linux is a plus, at least 3 years"}, headers=recruiter)
    assert r.status_code == 200, r.text
    assert r.json()["criteria"] == {"required_skills": ["python", "sql"], "nice_to_have": ["linux"], "min_years": 3}
    assert r.json()["llm_fallback"] is False
    saved = next(j for j in client.get("/jobs", headers=recruiter).json() if j["id"] == job["id"])
    assert saved["criteria"]["required_skills"] == ["azure"]  # reading the request saves nothing
    assert client.post(url, json={"text": "Python"}, headers=reader).status_code == 403
    assert client.post(url, json={"text": "x" * 2001}, headers=recruiter).status_code == 422
