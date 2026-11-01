"""Web / system security: auth, RBAC, machine keys, tenant isolation, signed webhook."""
import hashlib
import hmac
import json
import time

from sqlmodel import Session, select

from backend.config import get_settings
from backend.data.db import engine
from backend.data.models import Tenant, User
from backend.orchestration import seed
from backend.security.gateway import create_access_token, hash_password
from backend.tests.conftest import login


def test_requires_auth(client):
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs", headers={"Authorization": "Bearer forged.token.value"}).status_code == 401


def test_removed_roles_are_refused_then_deleted(client):
    """Viewer and employee accounts left in an older database: no login, no token, gone at the next start."""
    with Session(engine) as s:
        tid = s.exec(select(Tenant)).first().id
        old = [User(tenant_id=tid, email=f"old.{role}@acme.test", role=role, hashed_password=hash_password("Old#2026!"))
               for role in ("viewer", "employee")]
        s.add_all(old)
        s.commit()
        token = create_access_token(old[0])
    assert client.post("/auth/login", data={"username": "old.viewer@acme.test", "password": "Old#2026!"}).status_code == 401
    assert client.get("/jobs", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    with Session(engine) as s:
        seed.seed(s)  # runs at every start
        assert {u.role for u in s.exec(select(User))} == {"admin", "recruiter", "manager"}


def test_unknown_fields_rejected(client, recruiter):
    r = client.post("/jobs", json={"title": "Job", "score_override": 100}, headers=recruiter)
    assert r.status_code == 422


def test_api_key_scopes_and_no_approval(client, admin):
    # human-only scopes cannot be granted to a machine key
    assert client.post("/auth/api-keys", json={"name": "bad", "scopes": ["approvals:decide"]}, headers=admin).status_code == 400
    key = client.post("/auth/api-keys", json={"name": "n8n", "scopes": ["emails:ingest", "approvals:read"]},
                      headers=admin).json()["key"]
    h = {"X-API-Key": key}
    r = client.post("/emails", json={"sender": "a@example.com", "subject": "Hi", "body": "Hello there"}, headers=h)
    assert r.status_code == 200
    pending = client.get("/approvals?status=pending", headers=h).json()
    if pending:
        assert client.post(f"/approvals/{pending[0]['id']}/approve", json={}, headers=h).status_code == 403
    assert client.get("/jobs", headers=h).status_code == 403  # scope not granted
    assert client.get("/jobs", headers={"X-API-Key": key[:-3] + "xyz"}).status_code == 401


def test_tenant_isolation(client, demo, recruiter):
    with Session(engine) as s:
        other = Tenant(name="globex")
        s.add(other)
        s.commit()
        s.add(User(tenant_id=other.id, email="eve@globex.test", role="admin", hashed_password=hash_password("Globex#2026!")))
        s.commit()
    eve = login(client, "eve@globex.test", "Globex#2026!")
    job_id = demo["job_id"]
    assert client.get(f"/jobs/{job_id}/ranking", headers=eve).status_code == 404  # 404, not 403: no existence leak
    assert client.get("/jobs", headers=eve).json() == []
    assert client.get("/approvals", headers=eve).json() == []


def _signed(body: dict, ts: int | None = None):
    raw = json.dumps(body).encode()
    ts = str(ts or int(time.time()))
    sig = hmac.new(get_settings().webhook_secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"X-Timestamp": ts, "X-Signature": sig, "Content-Type": "application/json"}


def test_webhook_signature(client):
    body = {"sender": "n8n@example.com", "subject": "From n8n", "body": "Hello from the workflow", "external_id": "n8n-1"}
    raw, h = _signed(body)
    assert client.post("/webhooks/email", content=raw, headers=h).status_code == 200
    assert client.post("/webhooks/email", content=raw, headers={**h, "X-Signature": "0" * 64}).status_code == 401
    old_raw, old_h = _signed(body, ts=int(time.time()) - 3600)  # replay of an old message
    assert client.post("/webhooks/email", content=old_raw, headers=old_h).status_code == 401


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Request-ID"].startswith("req_")
