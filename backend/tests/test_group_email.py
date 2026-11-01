"""Group emails: the AI reads who and what, the code picks the recipients, a second person approves."""


def propose(client, headers, request):
    return client.post("/hr/group-email", json={"request": request}, headers=headers)


def ids(r):
    return [x["id"] for x in r.json()["payload"]["recipients"]]


def test_backend_engineers_get_one_email_each_after_four_eyes(client, recruiter, admin):
    r = propose(client, recruiter, "send to all backend engineers that the meeting is tomorrow at 18:00")
    assert r.status_code == 200, r.text
    assert ids(r) == ["emp_002", "emp_011", "emp_001"]  # Omar, Sami, Yasmine (sorted by first name)
    p = r.json()["payload"]
    assert "18:00" in p["body"] and "Backend Engineers" in p["audience"]
    action = r.json()["action_id"]
    assert client.post(f"/approvals/{action}/approve", json={}, headers=recruiter).status_code == 403  # four-eyes
    done = client.post(f"/approvals/{action}/approve", json={}, headers=admin).json()
    assert done["status"] == "executed" and done["result"].startswith("3 emails sent")
    sent = {m["to"] for m in client.get("/approvals/sent", headers=admin).json() if m["subject"] == p["subject"]}
    assert sent == {"yasmine.b@hr-demo.test", "omar.k@hr-demo.test", "sami.j@hr-demo.test"}


def test_reviewer_can_remove_recipients_but_not_all(client, recruiter, admin):
    action = propose(client, recruiter, "tell the sales team that the quarterly review moved to Monday 10:00").json()
    assert action["payload"]["to"].startswith("3 people")
    url = f"/approvals/{action['action_id']}/approve"
    everyone = [x["id"] for x in action["payload"]["recipients"]]
    assert client.post(url, json={"excluded_recipients": everyone}, headers=admin).status_code == 400
    done = client.post(url, json={"excluded_recipients": ["emp_005"]}, headers=admin).json()
    assert done["result"].startswith("2 emails sent")


def test_skills_narrow_the_group_and_inactive_people_are_left_out(client, recruiter):
    assert ids(propose(client, recruiter, "email everyone in engineering who knows kubernetes: cluster upgrade "
                                          "on Friday at 9:00")) == ["emp_011"]
    assert "emp_012" not in ids(propose(client, recruiter, "remind the media team that assets are due Thursday"))


def test_unclear_audience_is_refused(client, recruiter):
    r = propose(client, recruiter, "send to the astronauts that the launch is at noon")
    assert r.status_code == 422 and "NO_AUDIENCE" in r.json()["detail"]


