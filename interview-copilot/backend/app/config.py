from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    gemini_model_live: str = "gemini-2.5-flash"      # fast: live questions
    gemini_model_summary: str = "gemini-3-flash-preview"  # end-of-call brief (Pro models: no free-tier quota)
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    dev_auth_bypass: bool = False
    # Extra browser origins (e.g. the HR platform's web app). The extension itself calls from its service worker.
    cors_origins: list[str] = []

    # Storage: SQLite for local runs, PostgreSQL in production (postgresql+psycopg://user:pass@host/db)
    database_url: str = "sqlite:///./copilot.db"
    retention_days: int = 180              # confirmed interviews (transcript, summary, files) are deleted after this
    unconfirmed_ttl_hours: float = 12      # in-memory sessions HR never confirmed are dropped after this

    # Speech-to-text: auto (Deepgram if DEEPGRAM_API_KEY is set, else local Whisper) | deepgram | whisper | gemini
    stt_provider: str = "auto"
    stt_language: str = ""                 # whisper/gemini: "" = detect, or "en", "fr", "ar", ...
    whisper_model: str = "small"           # tiny | base | small | medium | large-v3 (bigger = better, slower)
    whisper_device: str = "cpu"            # cpu | cuda
    whisper_compute_type: str = "int8"     # int8 on CPU, float16 on GPU
    gemini_model_stt: str = "gemini-2.5-flash"
    stt_silence_ms: int = 700              # quiet time that ends an utterance (whisper/gemini)
    stt_max_utterance_seconds: float = 20  # long answers are cut and transcribed in pieces
    stt_min_speech_ms: int = 300           # shorter bursts (coughs, clicks) are ignored
    stt_min_rms: float = 300               # speech level floor, PCM16

    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    deepgram_language: str = "en"
    deepgram_endpointing_ms: int = 800

    # Live-assist tuning
    suggest_debounce_seconds: float = 1.5   # wait after the candidate stops before suggesting
    min_answer_words: int = 15              # ignore very short answers ("yes", "sure")
    end_check_seconds: float = 15.0         # silence after a goodbye before asking about ending


settings = Settings()
