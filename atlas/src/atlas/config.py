"""All configuration, read once from environment variables (and an optional .env file).

Nothing else in Atlas reads os.environ. Each setting is typed and validated here,
so a missing or malformed value fails at startup with a clear message.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings every part of Atlas may need, including the command line."""

    # hide_input_in_errors: a startup error would otherwise print every value it read,
    # secrets included, into logs (lesson 4.2 shows the leak).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str | None = None
    # Optional: without a token GitHub allows 60 requests an hour, with one 5,000.
    github_token: SecretStr | None = None
    # The job queue (lesson 5.1).
    redis_url: str = "redis://localhost:6379/0"
    # Where the worker keeps its clones of tracked repositories (lesson 5.1).
    repos_dir: Path = Path.home() / ".cache" / "atlas" / "repos"
    # Clones come from here; tests point it at local repositories instead of GitHub.
    git_base_url: str = "https://github.com"
    # How often the scheduler queues a sync of every repository (lesson 5.2).
    sync_interval_minutes: int = 15
    # Object storage for snapshots (lesson 5.4). Unset endpoint means real AWS S3. Keys are
    # not settings: boto3 reads AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY itself, and on
    # AWS it uses the task's IAM role instead, so no key is ever configured there (Part 7).
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    snapshot_bucket: str = "atlas-snapshots"
    # Older snapshots of a repository are deleted once this many newer ones exist.
    snapshots_kept: int = 3
    # Language models (Part 6). Ollama runs models locally and is the default; "anthropic"
    # uses Claude and needs ANTHROPIC_API_KEY (read by the SDK, not by Atlas).
    llm_provider: Literal["ollama", "anthropic"] = "ollama"
    # Unset: qwen3:1.7b on Ollama, claude-opus-5-5 on Anthropic.
    chat_model: str | None = None
    ollama_url: str = "http://localhost:11434"
    # Claude only: how hard the model thinks (low, medium, high, xhigh, max).
    llm_effort: str = "medium"
    # Embeddings for semantic search (lesson 6.4). Changing the model means re-embedding
    # everything, and a model with other dimensions needs a migration of the vector column.
    embedding_model: str = "nomic-embed-text"


class ApiSettings(Settings):
    """Settings the web API needs on top. Secrets have no defaults: a missing one stops startup."""

    database_url: str
    # At least 32 characters (256 bits for HS256 keys, RFC 7518): a short key can be
    # guessed, and whoever knows it can sign tokens for any user (lesson 4.8).
    secret_key: SecretStr = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # Browser origins allowed to call the API (lesson 4.16).
    cors_origins: list[str] = ["http://localhost:8000"]
    # GitHub sign-in (lesson 4.9). Leave unset to disable it.
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    # Where the ready-made web interface lives on disk; unset to serve the API only.
    web_dir: str | None = None
    # Shared with GitHub when creating the webhook (lesson 5.2). Unset disables the route.
    github_webhook_secret: SecretStr | None = None
