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

    # Database — override with postgres:// URL in production
    database_url: str = "sqlite:///./rss_reader.db"

    # App
    frontend_url: str = "http://localhost:3000"


settings = Settings()
