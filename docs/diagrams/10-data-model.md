# 10 · Data model: the main records and how they connect

**In one sentence:** four groups of records (hiring, approvals, people and projects, security), all tied to one
company (tenant); approvals and the audit log connect them.

```mermaid
erDiagram
  JOB_OFFER ||--o{ APPLICATION : receives
  CANDIDATE ||--o{ APPLICATION : submits
  CANDIDATE ||--o{ RESUME : sends
  APPLICATION ||--o{ PIPELINE_EVENT : "status changes"
  APPLICATION ||--o| FAIRNESS_REPORT : "bias check"
  CANDIDATE ||--o{ EMAIL_MESSAGE : writes

  PROPOSED_ACTION ||--o| APPROVAL : "needs (signed)"
  PROPOSED_ACTION ||--o{ SENT_EMAIL : "executor only"
  EMAIL_MESSAGE ||--o{ PROPOSED_ACTION : "reply drafts"
  APPLICATION ||--o{ PROPOSED_ACTION : "decisions, invites"

  TEAM ||--o{ EMPLOYEE : has
  EMPLOYEE ||--o{ EMPLOYEE_SKILL : holds
  PROJECT ||--o{ PROJECT_ASSIGNMENT : staffs
  EMPLOYEE ||--o{ PROJECT_ASSIGNMENT : "works on"
  PROJECT ||--o{ PROJECT_DOCUMENT : "charter, manifests"
  PROJECT ||--o{ SKILL_REQUIREMENT : "needs (versioned)"
  PROJECT ||--o{ SKILL_GAP : "computed"
  SKILL_GAP ||--o| PROPOSED_ACTION : "job offer draft"
  SKILL_GAP ||--o| JOB_OFFER : "hiring for"

  TENANT ||--o{ USER : "HR staff"
  TENANT ||--o{ METRIC_SNAPSHOT : "daily totals"
  TENANT ||--o{ SECURITY_EVENT : "AI Firewall"
  TENANT ||--o{ AUDIT_LOG : "hash-chained"

  PROPOSED_ACTION {
    uuid id
    string type "email, decision, interview, job offer..."
    string status "pending, executed, rejected, failed, cancelled"
    string risk_level
    string requested_by "for the four-eyes rule"
  }
  APPROVAL {
    uuid reviewer_id
    string payload_hash "locks the approved content"
    string signature "HMAC"
  }
  EMPLOYEE_SKILL {
    string skill
    int level "1 beginner to 4 expert"
    bool validated "only validated skills count"
  }
  SKILL_GAP {
    string kind "missing, level, capacity"
    float gap_fte
    string decision "open, internal, hire"
  }
  AUDIT_LOG {
    string actor
    string event
    string prev_hash
    string hash
  }
```

## How to read it
- **Hiring (top):** candidates, CVs, applications and every status change.
- **Approvals (middle):** a proposed action can be approved once, with a signature over its exact content. Only the
  executor creates sent emails.
- **People and projects:** employees, their validated skills and assignments, and the gaps computed from them. A gap
  marked "hire externally" leads to a job offer.
- **Security:** security events feed the AI Firewall. The audit log is hash-chained: each entry includes the hash of
  the previous one, so editing history is detectable.
- Every table also carries the company id (tenant), left out of the drawing to keep it readable.

**In the code:** `backend/data/models.py`.
