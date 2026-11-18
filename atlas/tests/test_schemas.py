"""Domain types (lesson 4.3): invalid values are rejected at the edge, valid ones normalised."""

import pytest
from pydantic import TypeAdapter, ValidationError

from atlas.api.schemas import Email, Password, RepoFullName


def test_email_is_stored_in_one_form() -> None:
    assert TypeAdapter(Email).validate_python("  Ada@Example.COM ") == "ada@example.com"


@pytest.mark.parametrize("value", ["not-an-email", "ada@", "@example.com"])
def test_email_rejects_malformed_addresses(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Email).validate_python(value)


@pytest.mark.parametrize(
    "value", ["pytorch/ignite", "sktime/sktime", "ML4SCI/DeepLense", "a.b/c-d_e"]
)
def test_repo_full_name_accepts_owner_slash_name(value: str) -> None:
    assert TypeAdapter(RepoFullName).validate_python(value) == value


@pytest.mark.parametrize(
    "value", ["ignite", "pytorch/ignite/extra", "not a repo", "/ignite", "x" * 199 + "/y"]
)
def test_repo_full_name_rejects_anything_else(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RepoFullName).validate_python(value)


@pytest.mark.parametrize("value", ["short", "x" * 129])
def test_password_length_is_bounded(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Password).validate_python(value)
