"""Settings (lesson 4.2): fail fast, and never print a secret while failing."""

import pytest
from pydantic import ValidationError

from atlas.config import ApiSettings

FAKE_TOKEN = "ghp_example_not_a_real_token_123"


def test_missing_settings_are_all_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as failure:
        ApiSettings(_env_file=None)  # type: ignore[call-arg]

    missing = {error["loc"][0] for error in failure.value.errors()}
    assert missing == {"database_url", "secret_key"}


def test_a_startup_error_never_contains_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:db-password@h/d")
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)

    with pytest.raises(ValidationError) as failure:
        ApiSettings(_env_file=None)  # type: ignore[call-arg]

    message = str(failure.value)
    assert "secret_key" in message
    # Pydantic shortens long values in the middle ("ghp_exa...token_123"), so check both ends.
    assert FAKE_TOKEN[:7] not in message
    assert FAKE_TOKEN[-9:] not in message
    assert "db-password" not in message


def test_secrets_print_as_stars() -> None:
    settings = ApiSettings(database_url="postgresql+psycopg://x/y", secret_key="s3cret-value")  # type: ignore[arg-type]

    assert "s3cret-value" not in repr(settings)
    assert settings.secret_key.get_secret_value() == "s3cret-value"
