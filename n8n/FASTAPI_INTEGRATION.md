# n8n as the orchestration layer (L2) for the FastAPI core

The FastAPI backend owns security, data, the approval queue and the executor. n8n does what it is good at:
triggers and plumbing (IMAP polling, cron, external webhooks). n8n never holds SMTP credentials for
candidate mail and can never approve anything: machine API keys cannot get the `approvals:decide` scope.

The original standalone workflows in `workflows/` (Gemini + n8n data tables) still work on their own.
Their logic was also ported to Python (`backend/agents/hr_ops.py`, `backend/orchestration/hr_ops.py`),
and their seed tables in `data/` are loaded by FastAPI on first start, so both share one dataset.

## 1. Create a scoped API key for n8n

Log in as admin, then:

```
POST /auth/api-keys
{"name": "n8n", "scopes": ["emails:ingest", "followups:manage", "resumes:upload"]}
```

The key is returned once. In n8n, create a "Header Auth" credential: name `X-API-Key`, value = the key.

## 2. Flows to build in n8n

| n8n trigger | HTTP Request node | Auth |
| --- | --- | --- |
| Email Trigger (IMAP), new mail | `POST {HR_API_URL}/emails` with `{"sender", "subject", "body", "external_id": messageId}` | X-API-Key |
| Or: any webhook source | `POST {HR_API_URL}/webhooks/email` (same body) | HMAC, see below |
| Schedule Trigger, every hour | `POST {HR_API_URL}/followups/scan` | X-API-Key |
| Email Trigger with attachment | `POST {HR_API_URL}/jobs/{job_id}/resumes` (multipart, field `files`) | X-API-Key |

`external_id` makes ingestion idempotent: n8n retries never create duplicates.

## 3. Signed webhook (alternative to the API key)

Headers: `X-Timestamp` = unix seconds, `X-Signature` = hex HMAC-SHA256 of `"{timestamp}.{raw_body}"`
with `WEBHOOK_SECRET`. Requests older than 5 minutes are refused (replay protection).
In n8n, use a Crypto node (HMAC, SHA256, hex) before the HTTP Request node.

## 4. What n8n gets back

High-risk emails are quarantined by the guard (`"quarantined": true`); they never reach an agent and show
up in the dashboard's AI Firewall page. Everything an agent drafts lands in `/approvals` for a human.
