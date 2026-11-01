import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.update({
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "VAR_DIR": str(_tmp / "var"),
    "LLM_PROVIDER": "mock",
    "LLM_FALLBACK_PROVIDER": "",
    "EMBEDDINGS_PROVIDER": "local",
    "SMTP_HOST": "",
    "RATE_LIMIT_ENABLED": "false",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def login(client, email, password):
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return login(client, "admin@acme.test", "Admin#2026!")


@pytest.fixture(scope="session")
def recruiter(client):
    return login(client, "recruiter@acme.test", "Recruiter#2026!")


@pytest.fixture(scope="session")
def viewer(client):
    return login(client, "viewer@acme.test", "Viewer#2026!")


@pytest.fixture(scope="session")
def employee(client):
    return login(client, "yasmine.b@hr-demo.test", "Employee#2026!")


@pytest.fixture(scope="session")
def demo(client, admin):
    r = client.post("/admin/demo/load", headers=admin)
    assert r.status_code == 200, r.text
    job = next(j for j in client.get("/jobs", headers=admin).json() if j["title"] == "Backend Engineer")
    assert client.post(f"/jobs/{job['id']}/screen", headers=admin).status_code == 200
    return {"load": r.json(), "job_id": job["id"]}
