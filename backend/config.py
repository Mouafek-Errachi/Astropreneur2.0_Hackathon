"""Central configuration, read from environment variables (.env supported)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_name: str = "Secure AI HR System"
    environment: str = "dev"

    # Database (SQLite by default, any SQLAlchemy URL works, e.g. postgresql+psycopg://)
    database_url: str = f"sqlite:///{(ROOT_DIR / 'var' / 'app.db').as_posix()}"

    # Auth
    jwt_secret: str = "change-me-in-production-please-32+chars"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Demo users seeded on first start (override in .env)
    seed_users: bool = True
    demo_admin_password: str = "Admin#2026!"
    demo_recruiter_password: str = "Recruiter#2026!"
    demo_viewer_password: str = "Viewer#2026!"

    # LLM
    llm_mode: str = "mock"  # mock | live
    llm_provider: str = "anthropic"  # anthropic | gemini
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"  # fast and accurate enough for extraction and drafting
    # Used in turn when a model is rate-limited or overloaded. Free-tier quotas are per model, so a pool adds up.
    gemini_fallback_models: str = "gemini-3.1-flash-lite,gemini-2.5-flash,gemini-3.6-flash"
    gemini_wait_budget_s: int = 120  # longest one call waits for a free model before using the rule-based fallback
    llm_max_retries: int = 2
    llm_concurrency: int = 6

    # Files
    data_dir: Path = ROOT_DIR / "data"
    var_dir: Path = ROOT_DIR / "var"
    max_upload_mb: float = 5.0
    max_pdf_pages: int = 10
    max_text_chars: int = 30_000
    max_files_per_batch: int = 120

    # Web security
    cors_origins: str = "http://localhost:8501"
    rate_limit_enabled: bool = True
    login_rate_limit: str = "5/minute"
    default_rate_limit: str = "300/minute"
    max_request_mb: float = 60.0

    # Inbound email webhook (HMAC-SHA256)
    webhook_secret: str = "change-me-webhook-secret"
    webhook_tolerance_seconds: int = 300

    # Outbound email allow-list for links inside drafted replies
    allowed_link_domains: str = "acme.test,careers.acme.test"

    # Human-in-the-loop rules
    four_eyes_decisions: bool = True  # whoever proposes a candidate decision or learned weights cannot approve it
    reminder_after_days: int = 3  # candidate follow-up when we wrote and they have not answered
    unanswered_email_hours: int = 48

    # Admin-only endpoint that loads fictional demo data (turn off outside demos)
    enable_demo_data: bool = True

    @property
    def storage_dir(self) -> Path:
        return self.var_dir / "storage"

    @property
    def quarantine_dir(self) -> Path:
        return self.var_dir / "quarantine"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def max_request_bytes(self) -> int:
        return int(self.max_request_mb * 1024 * 1024)

    @property
    def weak_jwt_secret(self) -> bool:
        return self.jwt_secret.startswith("change-me") or len(self.jwt_secret) < 32


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.weak_jwt_secret and s.environment != "dev":
        raise RuntimeError("JWT_SECRET is a default or shorter than 32 characters. Set a long random value.")
    s.var_dir.mkdir(parents=True, exist_ok=True)
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    s.quarantine_dir.mkdir(parents=True, exist_ok=True)
    return s
