import csv
import io
import random
from pathlib import Path

import pytest

from backend.orchestration.learner import LearnError, learn, read_csv

DATA = Path(__file__).resolve().parents[2] / "data"
REQ, NICE = ["python", "sql", "docker"], ["aws"]


def _csv(rows: list[dict]) -> bytes:
    f = io.StringIO()
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return f.getvalue().encode()


def _hires(n=80, seed=1, group_bias=False) -> list[dict]:
    """Results driven by experience only. With group_bias, group B has less experience (a biased history)."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        group = "A" if i % 2 else "B"
        # Both groups get the same spread of experience, unless the history is biased.
        years = rng.choice([0, 1, 2]) if group_bias and group == "B" else [0, 1, 2, 4, 6, 8][(i // 2) % 6]
        skills = [s for s in REQ + NICE if rng.random() < 0.6]
        good = rng.random() < (0.85 if years >= 3 else 0.15)
        rows.append({"skills": ";".join(skills), "years_experience": years, "jira_done": rng.randint(60, 120) if good
                     else rng.randint(10, 50), "performance": rng.randint(70, 95) if good else rng.randint(30, 60),
                     "group": group})
    return rows


def _learn(rows):
    return learn(read_csv(_csv(rows), REQ, NICE, 3))


def test_learns_that_experience_matters_most():
    rep = _learn(_hires())
    w = rep["weights"]
    assert w["experience"] > w["required"] and w["experience"] > w["nice"]
    assert rep["accuracy"] > rep["baseline_accuracy"]
    assert rep["fairness"]["checked"] and rep["fairness"]["passed"]


def test_outcome_is_the_better_half_by_performance_and_jira():
    rows = [{"skills": "python", "years_experience": 3, "jira_done": j, "performance": p}
            for j, p in [(10, 40), (50, 90), (90, 50), (100, 95)] * 8]
    recs = read_csv(_csv(rows), REQ, NICE, 3)
    # (100, 95) is best on both; (10, 40) worst on both; the middle two tie on success and both count as good.
    by_input = {(r["jira_done"], r["performance"]): rec for r, rec in zip(rows, recs)}
    assert by_input[(100, 95)].outcome == 1 and by_input[(10, 40)].outcome == 0
    assert by_input[(100, 95)].success > by_input[(50, 90)].success > by_input[(10, 40)].success


def test_performance_must_be_out_of_100():
    rows = [{"skills": "python", "years_experience": 3, "jira_done": 5, "performance": 140}]
    with pytest.raises(LearnError, match="between 0 and 100"):
        read_csv(_csv(rows), REQ, NICE, 3)


def test_personal_columns_are_refused():
    rows = [{**r, "gender": "x"} for r in _hires()]
    with pytest.raises(LearnError, match="Unexpected column"):
        _learn(rows)


def test_too_few_hires_is_refused():
    with pytest.raises(LearnError, match="at least 30"):
        _learn(_hires(n=10))


def test_bias_check_fails_on_biased_history():
    rep = _learn(_hires(group_bias=True))
    assert rep["fairness"]["impact_ratio"] < 0.8 and not rep["fairness"]["passed"]


def test_no_group_column_means_no_bias_check():
    rows = [{k: v for k, v in r.items() if k != "group"} for r in _hires()]
    assert _learn(rows)["fairness"]["checked"] is False


def test_sample_hires_file_learns_and_passes_bias_check():
    rep = learn(read_csv((DATA / "employees" / "backend_engineer_hires.csv").read_bytes(),
                         ["python", "sql", "docker"], ["fastapi", "aws", "kubernetes"], 3))
    assert rep["fairness"]["passed"] and rep["accuracy"] > rep["baseline_accuracy"]


def _job(client, headers):
    return client.post("/jobs", json={"title": "Learner job", "required_skills": REQ, "nice_to_have": NICE,
                                      "min_years": 3}, headers=headers).json()


def test_learned_weights_need_someone_else_to_approve(client, recruiter, admin):
    job = _job(client, recruiter)
    r = client.post(f"/jobs/{job['id']}/learn-weights", files={"file": ("hires.csv", _csv(_hires()))}, headers=recruiter)
    assert r.status_code == 200, r.text
    action_id, learned = r.json()["action_id"], r.json()["report"]["weights"]
    # Not applied before approval, and the proposer cannot approve it (four-eyes).
    assert job["criteria"]["weights"] != learned
    assert client.post(f"/approvals/{action_id}/approve", json={}, headers=recruiter).status_code == 403
    a = client.post(f"/approvals/{action_id}/approve", json={}, headers=admin)
    assert a.status_code == 200, a.text
    job_after = next(j for j in client.get("/jobs", headers=recruiter).json() if j["id"] == job["id"])
    assert job_after["criteria"]["weights"] == learned


def test_biased_weights_are_not_proposed(client, recruiter):
    job = _job(client, recruiter)
    r = client.post(f"/jobs/{job['id']}/learn-weights", files={"file": ("h.csv", _csv(_hires(group_bias=True)))},
                    headers=recruiter)
    assert r.status_code == 422 and "bias check" in r.json()["detail"]["message"]


@pytest.mark.parametrize("name, passes", [("past_hires_backend.csv", True), ("past_hires_biased.csv", False)])
def test_demo_hires_files(name, passes):
    """The two files offered for download on the Screening page: one passes the bias check, one is blocked."""
    rep = learn(read_csv((DATA / "employees" / name).read_bytes(), ["python", "sql", "docker"],
                         ["fastapi", "aws", "kubernetes"], 3), {"required": 60, "nice": 15, "experience": 25})
    assert rep["fairness"]["passed"] is passes
    assert rep["accuracy"] > rep["baseline_accuracy"]
    assert rep["weights"]["experience"] > 25  # these histories reward experience more than the current weights do
