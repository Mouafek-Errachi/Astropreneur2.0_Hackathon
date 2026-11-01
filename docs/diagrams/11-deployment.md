# 11 · Deployment: what runs where

**In one sentence:** one command starts the API and the dashboard on a laptop; a database, n8n, email and the AI
models are plugged in through settings, and the Interview Copilot runs next to it.

```mermaid
flowchart LR
  subgraph user["HR staff's computer"]
    BR["Browser<br/>HRplus dashboard"]
    CH["Chrome + Interview Copilot<br/>extension on Google Meet"]
  end

  subgraph server["HRplus server (python run.py, or docker compose)"]
    DSH["Dashboard<br/>Streamlit · port 8501"]
    API["API<br/>FastAPI · port 8000<br/>gateway, guard, workflows, executor"]
    DB[("Database<br/>SQLite file, or Postgres + pgvector")]
    FILES[("Files<br/>quarantine and storage")]
    COP["Copilot backend<br/>FastAPI · port 8010"]
  end

  subgraph ext["Plugged in through .env settings"]
    LLM["AI: Gemini or Grok<br/>(mock mode needs none)"]
    SMTP["Email: Gmail SMTP,<br/>or a local catcher for tests"]
    N8N["n8n (optional)<br/>Gmail trigger, CV collector,<br/>scheduled calls (follow-ups, snapshot)"]
    STT["Speech-to-text<br/>local Whisper or a cloud API"]
  end

  CAND["Candidate<br/>interview slot link"]

  BR --> DSH --> API
  API --> DB & FILES
  API --> LLM
  API -->|"approved actions only"| SMTP
  N8N -->|"scoped API key"| API
  CAND -->|"single-use link"| API
  CH --> COP --> STT
  COP --> LLM
```

## How to read it
- **Runs offline by default.** With `LLM_PROVIDER=mock` the AI agents use rule-based stand-ins, so the whole demo
  works without any API key or internet access.
- **Email:** for tests, a local mail catcher prints every approved email. For real delivery, set Gmail with an app
  password; sent mail then appears in the Gmail Sent folder.
- **Database:** a local SQLite file by default; set `DATABASE_URL` to use Postgres (with pgvector for embeddings).
- **Candidates never log in.** The only thing they open is a single-use link to choose an interview slot.

**In the code:** `run.py`, `docker-compose.yml`, `.env.example`, `interview-copilot/backend/`.
