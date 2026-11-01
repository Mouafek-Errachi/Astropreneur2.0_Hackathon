# 05 · AI security guardrails: how untrusted input is inspected

**In one sentence:** every file and message from outside passes a chain of checks before any AI reads it; anything
suspicious is held, quarantined or refused, and every catch is shown on the AI Firewall page.

```mermaid
flowchart TD
  IN["Incoming content<br/>CV, email, project file, company document"] --> GW

  subgraph gate["Gateway (every request)"]
    GW["Who is calling?<br/>login token or API key"] --> SC["Allowed for this role?<br/>(scopes)"]
    SC --> RL["Rate limit and size limit"]
  end

  RL --> F1

  subgraph files["File guard"]
    F1["Real file type from its bytes<br/>(a .pdf that is not a PDF is refused)"]
    F1 --> F2["Size and page limits,<br/>zip-bomb check"]
    F2 --> F3["Extract visible text;<br/>set aside hidden text<br/>(white, tiny, off-page, hidden runs)"]
  end

  F3 --> S1

  subgraph text["Text guard"]
    S1["Clean the text<br/>remove zero-width, bidi, tag characters"]
    S1 --> S2["Scan for injections<br/>'ignore previous instructions', role hijack,<br/>score manipulation, fake chat tokens,<br/>hidden base64 messages, data exfiltration"]
    S2 --> S3["Risk level<br/>none · low · medium · high"]
  end

  S3 --> D{"What happens"}
  D -->|"wrong or broken file"| REF["Refused"]
  D -->|"email, high risk"| QUA["Quarantined:<br/>never reaches the AI"]
  D -->|"CV, medium or high"| HOLD["Kept but held for review;<br/>score still computed in code"]
  D -->|"company document,<br/>hidden text or injection"| REF
  D -->|"clean"| AI

  AI["AI agent<br/>no tools, text fenced as untrusted,<br/>answers in a fixed JSON format"] --> OV["Output checks<br/>format, links, facts, wording"]
  OV --> PEND["Pending action<br/>(see 02 · Human approval)"]

  REF & QUA & HOLD --> EV["Security event + audit log"]
  EV --> FW["AI Firewall page"]
```

## How to read it
- **Hidden text is set aside, never given to the AI.** People can still see it, so the attack is visible as evidence.
- **The guard decides what to do by source:**
  - an email with a high-risk payload is quarantined;
  - a suspicious CV is kept and held for review, because refusing it would hide the attack and could drop a genuine
    candidate;
  - a company document the HR assistant would trust is refused outright.
- **Even if an injection slips through, it cannot act.** The AI has no tools and only returns JSON. Scores are
  computed in code, and anything it drafts still waits for a human approval.
- **API keys (n8n, CV collectors) can never approve,** whatever permissions an admin tries to give them.

**In the code:** `backend/security/gateway.py`, `backend/security/guard/` (`files.py`, `sanitizer.py`,
`injection.py`), `backend/security/validator.py`, `backend/security/events.py`, dashboard page *AI Firewall*.
