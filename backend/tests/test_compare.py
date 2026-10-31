from backend.agents import compare, llm

ALICE = "Alice Martin\nalice@example.com\nSkills: python, sql\n6 years of experience\n"
CIPRIANO = "Cipriano Fernandez\ncipriano@example.com\nSkills: python, aws, pandas\n10 years of experience\n"
BOB = "Bob Nguyen\nbob@example.com\nSkills: excel\n1 year of experience\n"


def _job_with(client, headers, *resumes):
    """Returns the job and the application ids, in upload order."""
    job = client.post("/jobs", json={"title": "Compare job", "required_skills": ["python", "sql"],
                                     "nice_to_have": ["aws"], "min_years": 3}, headers=headers).json()
    files = [("files", (f"cv{i}.txt", text.encode())) for i, text in enumerate(resumes)]
    cids = [r["candidate_id"] for r in client.post(f"/jobs/{job['id']}/resumes", files=files, headers=headers).json()]
    ranking = client.post(f"/jobs/{job['id']}/screen", headers=headers).json()["ranking"]
    by_cand = {r["candidate_id"]: r["application_id"] for r in ranking}
    return job, [by_cand[c] for c in cids]


def test_recommends_the_top_score_and_says_when_the_other_is_better(client, recruiter):
    job, ids = _job_with(client, recruiter, ALICE, CIPRIANO, BOB)
    r = client.post(f"/jobs/{job['id']}/compare", json={"application_ids": ids}, headers=recruiter)
    assert r.status_code == 200, r.text
    advice = r.json()
    assert advice["recommended_application_id"] == ids[0]
    assert advice["recommendation"].startswith("I suggest choosing Alice Martin because")
    cipriano, bob = advice["alternatives"]
    assert cipriano["sentence"].startswith("If you want") and "more experience (10 years vs 6)" in cipriano["sentence"]
    assert "aws" in cipriano["sentence"] and cipriano["sentence"].endswith("Cipriano Fernandez is the better choice.")
    assert "scores lower on every criterion" in bob["sentence"]


def test_ai_sentence_about_the_wrong_person_is_replaced(client, recruiter, monkeypatch):
    """Injected resume text could make the LLM praise someone else: the output check keeps the code's choice."""
    def hijacked(task, schema, mock, **kwargs):
        return schema.model_validate({"recommendation": "I suggest choosing Bob Nguyen, the best by far.",
                                      "alternatives": []}), False
    monkeypatch.setattr(compare, "complete_json", hijacked)
    job, ids = _job_with(client, recruiter, ALICE + "x", BOB + "y")
    advice = client.post(f"/jobs/{job['id']}/compare", json={"application_ids": ids}, headers=recruiter).json()
    assert advice["recommendation"].startswith("I suggest choosing Alice Martin")
    assert "Bob Nguyen" in advice["alternatives"][0]["sentence"]


def test_compare_needs_two_candidates_of_the_same_job(client, recruiter):
    job, ids = _job_with(client, recruiter, ALICE + "z")
    r = client.post(f"/jobs/{job['id']}/compare", json={"application_ids": ids + ids}, headers=recruiter)
    assert r.status_code == 400


def test_task_forbids_gendered_pronouns():
    assert "never 'he' or 'she'" in compare.TASK and llm  # the prompt keeps people gender-neutral
