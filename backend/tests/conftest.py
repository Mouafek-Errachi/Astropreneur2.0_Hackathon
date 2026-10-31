import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.update({
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "VAR_DIR": str(_tmp / "var"),
    "LLM_MODE": "mock",
    "RATE_LIMIT_ENABLED": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def recruiter(client):
    return _login(client, "recruiter@acme.test", "Recruiter#2026!")


@pytest.fixture(scope="session")
def viewer(client):
    return _login(client, "viewer@acme.test", "Viewer#2026!")


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin@acme.test", "Admin#2026!")
