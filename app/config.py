from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Google OAuth2
    google_client_id: str = ""
    google_client_secret: str = ""

    # Where Google redirects after login (must match Google Console setting)
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"

    # JWT
    jwt_secret_key: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours

    # Database — PostgreSQL (psycopg3 driver)
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/rss_reader"

    # Redis — caching, rate limiting, SSE pub/sub, and Celery result backend
    redis_url: str = "redis://localhost:6379/0"

    # Celery — RabbitMQ broker, Redis result backend
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Razorpay — checkout for plan upgrades; create keys at https://dashboard.razorpay.com/app/keys
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Anthropic — LLM-assisted parser generation (app.services.parser_gen --llm)
    anthropic_api_key: str = ""
    parser_gen_model: str = "claude-sonnet-4-6"

    # Admin — comma-separated emails allowed to trigger self-healing-feed
    # fetcher generation/approval (app.routers.fetchers)
    admin_emails: str = ""

    # App
    frontend_url: str = "http://localhost:3000"

    # CORS — comma-separated list of allowed origins
    cors_origins: str = "http://localhost:5173,http://localhost:3000"


_INSECURE_JWT_SECRET = "change-me-in-production-use-a-long-random-string"


def _validate(s: "Settings") -> None:
    if s.jwt_secret_key == _INSECURE_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET_KEY is still the default placeholder — set a unique "
            "secret (see .env.example)."
        )


settings = Settings()
_validate(settings)
