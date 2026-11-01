# Roles: who can do what

Only HR staff log in. Employees are records in the system (team, contract, leave balance, skills, points), not users.
There are three roles, and each one adds to the one before it.

| Role | Demo account | In one line |
|---|---|---|
| **Recruiter** | `recruiter@acme.test` | HR recruiter: runs hiring and HR requests day to day. Proposes; does not sign off. |
| **Manager** | `manager@acme.test` | HR manager: everything a recruiter does, signs off on high-stakes actions and owns employee skills. |
| **Admin** | `admin@acme.test` | IT/security: everything, plus API keys, the audit log and demo data. |

## Access table

| Access | Recruiter | Manager | Admin |
|---|:-:|:-:|:-:|
| Jobs, CV upload and screening | ✅ | ✅ | ✅ |
| Inbox and drafting candidate emails | ✅ | ✅ | ✅ |
| Interview scheduling and follow-ups | ✅ | ✅ | ✅ |
| Approve routine actions (candidate emails, interview slots) | ✅ | ✅ | ✅ |
| Sign off job offers, hiring decisions, team broadcasts, scoring weights | — | ✅ | ✅ |
| HR tickets, broadcast and recognition proposals, Ask HR | ✅ | ✅ | ✅ |
| Projects and skill gaps (team totals) | ✅ | ✅ | ✅ |
| Individual employee skill levels: see, add, validate | — | ✅ | ✅ |
| AI Firewall: blocked files and injections | ✅ | ✅ | ✅ |
| Audit log, API keys, policy re-index, demo data | — | — | ✅ |

The same table is shown on the log-in page ("What each role can do") and in the sidebar ("What my role can do"). It
comes from `ROLE_ACCESS` in `backend/security/gateway.py`, next to the scopes that enforce it (`ROLE_SCOPES`).

## Rules that apply on top of the roles

- **Four-eyes.** Job offers, hiring decisions, team broadcasts and scoring weights need a second person: whoever
  requested the action cannot approve it, and the approver must be a manager or admin (`approvals:sign_off`).
- **Personal data.** Recruiters see project gaps and team totals, but not who has which skill at which level.
- **No self-service on your own record.** An HR user linked to an employee record cannot change their own skills or
  award themselves points.
- **Machines never decide.** API keys (n8n, CV collectors) can never hold `approvals:*`, `skills:*`, `admin` or
  `audit:read`, whatever an admin tries to grant.

## Scopes per role

| Scope | Recruiter | Manager | Admin |
|---|:-:|:-:|:-:|
| `jobs:read` `jobs:write` `resumes:upload` `screening:run` | ✅ | ✅ | ✅ |
| `emails:read` `emails:ingest` `emails:process` `drafts:create` | ✅ | ✅ | ✅ |
| `scheduling:read` `scheduling:manage` `followups:read` `followups:manage` `interview:assist` | ✅ | ✅ | ✅ |
| `approvals:read` `approvals:decide` | ✅ | ✅ | ✅ |
| `hr:read` `hr:write` `qa:ask` | ✅ | ✅ | ✅ |
| `projects:read` `projects:write` | ✅ | ✅ | ✅ |
| `security:read` | ✅ | ✅ | ✅ |
| `approvals:sign_off` `skills:read` `skills:manage` | — | ✅ | ✅ |
| `admin` `audit:read` `qa:admin` | — | — | ✅ |
