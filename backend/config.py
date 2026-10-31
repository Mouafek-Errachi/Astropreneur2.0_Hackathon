"""Central configuration, read from environment variables (.env supported)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_name: str = "Secure AI HR System"

    # L4 data: SQLite by default; set postgresql+psycopg://... to use the compose Postgres
    database_url: str = f"sqlite:///{(ROOT_DIR / 'var' / 'app.db').as_posix()}"

    # L5 gateway
    jwt_secret: str = "change-me-in-production-please-32+chars"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    cors_origins: str = "http://localhost:8501,http://127.0.0.1:8501"
    rate_limit_enabled: bool = True
    login_rate_limit: str = "5/minute"
    default_rate_limit: str = "300/minute"

    # Demo users seeded on first start
    seed_demo: bool = True
    demo_admin_password: str = "Admin#2026!"
    demo_recruiter_password: str = "Recruiter#2026!"
    demo_viewer_password: str = "Viewer#2026!"
    demo_employee_password: str = "Employee#2026!"

    # L3 agents: LLM provider = mock | gemini | grok, with an optional fallback provider
    llm_provider: str = "mock"
    llm_fallback_provider: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    grok_api_key: str = ""
    grok_model: str = "grok-4"
    grok_base_url: str = "https://api.x.ai/v1"
    llm_max_retries: int = 2
    llm_timeout_seconds: float = 60
    embeddings_provider: str = "local"  # local | gemini
    gemini_embedding_model: str = "gemini-embedding-001"

    # Files
    data_dir: Path = ROOT_DIR / "data"
    n8n_data_dir: Path = ROOT_DIR / "n8n" / "data"
    var_dir: Path = ROOT_DIR / "var"
    max_upload_mb: float = 5.0
    max_pdf_pages: int = 10
    max_text_chars: int = 30_000

    # L2 orchestration: inbound webhook from n8n (HMAC-SHA256 over "timestamp.body")
    webhook_secret: str = "change-me-webhook-secret"
    webhook_tolerance_seconds: int = 300

    # L5 executor: the ONLY component that reads these. Empty SMTP host = record to the outbox only.
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_from: str = "hr@acme.test"
    approval_signing_secret: str = "change-me-approval-signing-secret"
    public_base_url: str = "http://127.0.0.1:8000"
    scheduling_token_hours: int = 72

    # Output validator: links allowed inside drafted emails
    allowed_link_domains: str = "acme.test,careers.acme.test,127.0.0.1,localhost"

    @property
    def storage_dir(self) -> Path:
        return self.var_dir / "storage"

    @property
    def quarantine_dir(self) -> Path:
        return self.var_dir / "quarantine"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.var_dir, s.storage_dir, s.quarantine_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
