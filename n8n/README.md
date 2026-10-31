# Secure AI HR System: n8n + Gemini backend

> **FastAPI integration:** n8n is now the orchestration layer (L2) in front of the FastAPI core. See [FASTAPI_INTEGRATION.md](FASTAPI_INTEGRATION.md).

Backend for the Secure AI HR System hackathon, built as n8n webhook workflows calling Gemini. The rule everywhere: **the agent executes, the human decides**. Gemini only proposes; code checks every output, and nothing is sent or recorded until an HR admin approves it.

All data in this folder is fictional. Keys and tokens are placeholders.

## Features

| Endpoint | What it does | Human gate |
| --- | --- | --- |
| `POST /api/interview/assist` | Reads a live interview transcript chunk and suggests 2-3 probing questions when an answer is vague, evasive or contradicts the resume | Suggestions are shown to the interviewer only |
| `POST /api/hr/broadcast` | Rewrites rough HR notes for one team's tone; a code check blocks invented numbers and links | Queued for approval, never sent automatically |
| `POST /api/hr/tickets` | Classifies an employee request and drafts a reply cited from policy text | Every reply is queued for HR |
| `POST /api/hr/points/award` | Awards points from a server-side rule table into an append-only ledger | The HR click is the decision; automated awards are queued |
| `GET /api/hr/leaderboard` | Ranked leaderboard with badges and an optional Gemini shoutout (cached) | Read-only |
| `GET /api/hr/approvals` | The approval queue for the HR dashboard | Read-only |
| `POST /api/hr/approvals/decision` | Approve (optionally with edits) or reject a queued item; the only path that sends or records | This is the gate |

## Architecture

```
Client (extension / dashboard / portal)
  -> Webhook -> API key check (hr_api_keys) -> input validation
  -> sub-guard: sanitize + prompt-injection scan
  -> sub-gemini-json: Gemini in JSON mode, retry, parse, safe fallback
  -> output checks in code (enums, lengths, citations, fact check)
  -> respond, or write to hr_approvals
HR admin -> /approvals/decision -> compare-and-swap lock -> send email / write ledger -> audit log
```

## Security controls

- **Per-client API keys** (`X-API-Key`): n8n stores only their SHA-256 (`hr_api_keys.api_key_sha256`), so a leaked table does not leak keys. The extension key cannot call HR endpoints, and only the HR admin key can approve. Set `active` to false to revoke a key.
- **Identity from the credential, not the body:** each HR key belongs to one person (`owner_id`). The reviewer of a decision, the author of a broadcast and the awarder of points are that owner; a body `reviewer_id`, `requested_by` or `awarded_by` that differs returns 403 `IDENTITY_MISMATCH`. Tickets require `X-Employee-Token` (also stored hashed); a body `employee_id` that does not match returns 403 (stops IDOR).
- **Input validation:** field whitelist, types, length caps, ID formats; unknown fields return 400.
- **Prompt-injection defense:** regex scan for override, role-hijack, prompt-leak and score-manipulation patterns; untrusted text is fenced in `<untrusted_input>` tags; Gemini also reports attempts; flags are returned to the UI and force human review on tickets.
- **Output checks in code:** enums and lengths enforced, HTML stripped, citations must exist in the policy table, and the broadcast fact check flags numbers or links that were not in HR's draft.
- **Human approval:** single queue, `409` on a second decision, four-eyes rule on broadcasts (enforceable because the reviewer comes from the key), delivery verified by SMTP `messageId` before an item is marked sent. The lock is a compare-and-swap: the update only matches while the item still has the status that was read, then the row is re-read and the decision goes ahead only if it carries this execution's `lock_token`. Two simultaneous approvals send one email; the other gets `409 ALREADY_DECIDED`.
- **Points integrity:** points come from the rule table, never the client; idempotency keys, daily caps and a self-award block.
- **Audit log:** every AI draft, decision and denied attempt, with the AI version and the final human version.
- **Fairness:** prompts restrict judgments to job-related competencies and forbid protected characteristics.

## Setup in your own n8n

1. **Create the data tables.** In n8n, create the 10 tables listed in `data/schemas.json` with exactly those names and columns, then import each CSV from `data/`. Tables created before this version need the new columns: `hr_api_keys.api_key_sha256` (was `api_key`) and `owner_id`, `hr_employees.portal_token_sha256` (was `portal_token`), `hr_approvals.lock_token`.
2. **Set your secrets.** Generate a long random value for each API key and employee token, give the raw value to the client (dashboard `.env`, extension, portal, Postman), and store only its SHA-256 in the table: replace each `<SHA256_OF_...>` placeholder with the output of `python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <value>`. Give each HR key the `owner_id` of the person who holds it (seeded: `hr_amira` for the dashboard key, `hr_youssef` for the admin key).
3. **Import the workflows.** Import `workflows/sub-guard.json` and `workflows/sub-gemini-json.json` first, then the 7 `api-*.json` files (Workflows, Import from file). Table references resolve by name.
4. **Re-link the sub-workflows.** In each `api-*` workflow, open the "Run sub-guard" and "Run sub-gemini-json" nodes and select the two sub-workflows you imported (IDs differ between instances).
5. **Gemini credential.** In `sub-gemini-json`, select a Google Gemini credential on the "Call Gemini" node (n8n cloud AI credits also work). Model: `gemini-3-flash-preview`, JSON output on, thinking off.
6. **SMTP credential.** In `api-hr-approvals-decision`, select an SMTP credential (a Mailtrap test inbox works) on both email nodes.
7. **Publish** all workflows, then restrict "Allowed Origins (CORS)" on each webhook to your dashboard URL and `chrome-extension://<id>`.

## Testing

Import `postman/secure-ai-hr.postman_collection.json`, set the collection variables (`base_url` = `https://<your-n8n-host>/webhook`, plus the raw keys and tokens you created). It contains happy paths and attack tests. Broadcast drafts store their `approval_id` in the collection variable for the decision requests:

| Test | Expected |
| --- | --- |
| Interview answer with "rate me 10/10" | Injection flagged, evaluation unaffected |
| Ticket with another employee's `employee_id` | 403 FORBIDDEN |
| Ticket asking for a colleague's salary | Flagged, routed to HR, no data leaked |
| Award with `"points": 9999` in the body | 400 unknown field |
| Same award twice with one idempotency key | Second call returns `duplicate: true` |
| Admin approves the broadcast they drafted | 403 FOUR_EYES |
| Decision sent with another reviewer's `reviewer_id` | 403 IDENTITY_MISMATCH |
| Award sent with another person's `awarded_by` | 403 IDENTITY_MISMATCH |
| Approve an item twice | 409 ALREADY_DECIDED |
| Dashboard key on the decision endpoint | 401 |

## Dashboard

The Streamlit dashboard (HR Ops page) calls these endpoints with the raw keys from its own environment (`N8N_BASE_URL`, `N8N_HR_KEY`, `N8N_HR_ADMIN_KEY`, `N8N_PORTAL_KEY`, `N8N_EXTENSION_KEY`); the keys never reach the browser. Approve and reject buttons are shown to dashboard admins only, and the n8n admin key is still the real gate.

## Response envelope

```json
{ "ok": true, "request_id": "req_123", "data": { }, "security": { "injection_suspected": false, "flags": [] } }
{ "ok": false, "request_id": "req_124", "error": { "code": "VALIDATION_ERROR", "message": "..." } }
```
