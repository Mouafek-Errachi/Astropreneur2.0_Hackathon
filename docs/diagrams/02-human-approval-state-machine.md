# 02 · Human approval: how an AI action waits for a person

**In one sentence:** the AI can only *propose* an action; nothing is sent or changed until a person approves it, the
approval is signed, and the executor checks that signature before doing anything.

## The life of one proposed action (state machine)

```mermaid
stateDiagram-v2
  [*] --> Pending: an agent proposes<br/>(email draft, decision, interview, job offer...)
  Pending --> Rejected: a person rejects it
  Pending --> Cancelled: the system withdraws it<br/>(e.g. the candidate already replied)
  Pending --> Approved: a person approves it<br/>(may edit the text first)
  state Approved {
    direction LR
    [*] --> Signed: HMAC signature over<br/>the exact content
    Signed --> Verified: executor re-checks<br/>signature and content
  }
  Approved --> Executed: action done<br/>(email sent, record changed)
  Approved --> Failed: signature mismatch<br/>or delivery error
  Executed --> [*]
  Rejected --> [*]
  Cancelled --> [*]
  Failed --> [*]
```

## Who may approve what (checked before signing)

```mermaid
flowchart TD
  A["Person clicks Approve"] --> H{"Human user?<br/>(not an API key)"}
  H -- no --> X1["Refused"]
  H -- yes --> R{"Risk flagged high?"}
  R -- "yes, not acknowledged" --> X2["Refused: review the flags first"]
  R -- "no, or acknowledged" --> T{"High-stakes action?<br/>job offer, hiring decision,<br/>team or group email, scoring weights"}
  T -- no --> OK["Signed and executed"]
  T -- yes --> S{"Approver is the person<br/>who asked for it?"}
  S -- yes --> X3["Refused: four-eyes rule"]
  S -- no --> M{"Approver is a<br/>manager or admin?"}
  M -- no --> X4["Refused: needs a manager sign-off"]
  M -- yes --> OK
```

## One example end to end: replying to a candidate email

```mermaid
sequenceDiagram
  autonumber
  participant C as Candidate
  participant G as Security guard
  participant AI as AI agent
  participant Q as Approval queue
  participant HR as HR recruiter
  participant EX as Executor
  C->>G: email arrives
  G->>G: check for hidden text and injections
  alt high risk
    G-->>Q: quarantined, shown on the AI Firewall page (never reaches the AI)
  else clean
    G->>AI: cleaned text, fenced as untrusted
    AI->>Q: draft reply = pending action
    HR->>Q: reads, edits, approves
    Q->>EX: signed approval
    EX->>EX: verify signature and content hash
    EX->>C: send the email
    EX->>Q: mark executed, write the audit log
  end
```

## How to read it
- **Pending is the only door.** Agents can create pending actions, nothing else. Only `backend/security/executor.py`
  sends email or changes records, and only from an approved action.
- **The signature locks the content.** If anyone changes the action after it was approved, the check fails and the
  action ends as *Failed*. Nothing is sent.
- **"Approved" is a step, not a resting place:** the signed approval is stored, and the action goes straight on to
  *Executed* or *Failed*.
- **Four-eyes:** for job offers, hiring decisions, team and group emails and scoring weights, a second person must
  approve, and that person must be a manager or admin.
- **Every step is in the audit log**, which is hash-chained: editing an old entry breaks the chain.

**In the code:** `backend/orchestration/workflow.py` (propose, cancel, resume), `backend/security/executor.py`
(approve, sign, verify, execute), `backend/api/approvals.py`, dashboard page *Approvals*.
