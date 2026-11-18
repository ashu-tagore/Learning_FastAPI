"""All configuration, read once from environment variables (and an optional .env file).

Nothing else in Atlas reads os.environ. Each setting is typed and validated here,
so a missing or malformed value fails at startup with a clear message.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings every part of Atlas may need, including the command line."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = None
    # Optional: without a token GitHub allows 60 requests an hour, with one 5,000.
    github_token: SecretStr | None = None


class ApiSettings(Settings):
    """Settings the web API needs on top. Secrets have no defaults: a missing one stops startup."""

    database_url: str
    secret_key: SecretStr
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # Browser origins allowed to call the API (lesson 4.12).
    cors_origins: list[str] = ["http://localhost:8000"]
    # GitHub sign-in (lesson 4.8). Leave unset to disable it.
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    # Where the ready-made web interface lives on disk; unset to serve the API only.
    web_dir: str | None = None
