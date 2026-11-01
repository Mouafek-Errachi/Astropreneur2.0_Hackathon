"""The executor's SMTP path, with a fake server: encryption and login for a real mailbox such as Gmail."""
import pytest

from backend.config import get_settings
from backend.security import executor


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"], msg["From"], msg["Subject"]))


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(executor.smtplib, "SMTP", FakeSMTP)
    s = get_settings()
    for k, v in {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_from": "hr.team@gmail.com",
                 "smtp_user": "", "smtp_password": "", "smtp_starttls": False}.items():
        monkeypatch.setattr(s, k, v)
    return s


def _approve_group_email(client, admin, recruiter):
    """Sales has 3 active people: one email each, so three SMTP sessions."""
    action = client.post("/hr/group-email", json={"request": "tell the sales team that the review is Monday 10:00"},
                         headers=recruiter).json()
    return client.post(f"/approvals/{action['action_id']}/approve", json={}, headers=admin).json()


def test_gmail_login_is_encrypted_and_the_mail_is_sent(client, admin, recruiter, smtp, monkeypatch):
    monkeypatch.setattr(smtp, "smtp_user", "hr.team@gmail.com")
    monkeypatch.setattr(smtp, "smtp_password", "app-password")
    done = _approve_group_email(client, admin, recruiter)
    assert done["status"] == "executed" and done["result"].startswith("3 emails sent")
    assert len(FakeSMTP.instances) == 3
    for s in FakeSMTP.instances:
        assert (s.host, s.port) == ("smtp.gmail.com", 587)
        assert s.calls[0] == "starttls" and s.calls[1] == ("login", "hr.team@gmail.com", "app-password")
        assert s.calls[2][0] == "send" and s.calls[2][2] == "hr.team@gmail.com"
    assert {s.calls[2][1] for s in FakeSMTP.instances} == {"karim.t@hr-demo.test", "sarra.d@hr-demo.test",
                                                           "rania.f@hr-demo.test"}


def test_local_catcher_needs_no_login(client, admin, recruiter, smtp):
    _approve_group_email(client, admin, recruiter)
    assert all([c[0] if isinstance(c, tuple) else c for c in s.calls] == ["send"] for s in FakeSMTP.instances)
