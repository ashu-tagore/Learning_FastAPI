from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import User
from tests.conftest import sign_up


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_register_never_returns_the_password(client: TestClient) -> None:
    response = client.post(
        "/auth/register", json={"email": "Ada@Example.com", "password": "a-long-password"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert body["role"] == "user"
    assert "password" not in body and "password_hash" not in body


def test_passwords_are_stored_as_argon2_hashes(client: TestClient, session: Session) -> None:
    sign_up(client, "ada@example.com")

    stored = session.scalar(select(User.password_hash).where(User.email == "ada@example.com"))
    assert stored is not None and stored.startswith("$argon2")


def test_duplicate_email_is_a_conflict_in_the_standard_error_shape(client: TestClient) -> None:
    sign_up(client, "ada@example.com")

    response = client.post(
        "/auth/register", json={"email": "ada@example.com", "password": "another-long-one"}
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["message"] == "An account with this email exists"
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_invalid_input_lists_each_problem(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "not-an-email", "password": "short"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    locations = [detail["location"] for detail in error["details"]]
    assert ["body", "email"] in locations and ["body", "password"] in locations


def test_login_failures_look_identical(client: TestClient) -> None:
    sign_up(client, "ada@example.com")

    wrong_password = client.post(
        "/auth/login", data={"username": "ada@example.com", "password": "wrong-password"}
    )
    unknown_email = client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": "wrong-password"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]


def test_me_requires_a_valid_token(client: TestClient) -> None:
    headers = sign_up(client, "ada@example.com")

    assert client.get("/users/me", headers=headers).json()["email"] == "ada@example.com"

    missing = client.get("/users/me")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"

    tampered = {"Authorization": headers["Authorization"][:-2] + "xx"}
    assert client.get("/users/me", headers=tampered).status_code == 401


def test_patch_changes_only_the_fields_sent(client: TestClient) -> None:
    headers = sign_up(client, "ada@example.com")

    response = client.patch(
        "/users/me", json={"skills_profile": "metrics, handlers"}, headers=headers
    )

    assert response.json()["skills_profile"] == "metrics, handlers"
    assert response.json()["email"] == "ada@example.com"


def login_tokens(client: TestClient) -> dict[str, str]:
    client.post("/auth/register", json={"email": "ada@example.com", "password": "a-long-password"})
    response = client.post(
        "/auth/login", data={"username": "ada@example.com", "password": "a-long-password"}
    )
    tokens: dict[str, str] = response.json()
    return tokens


def test_refresh_rotates_tokens(client: TestClient) -> None:
    first = login_tokens(client)

    response = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = client.get("/users/me", headers={"Authorization": f"Bearer {second['access_token']}"})
    assert me.status_code == 200


def test_reusing_a_refresh_token_signs_out_everywhere(client: TestClient) -> None:
    first = login_tokens(client)
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    # The first token comes back: maybe stolen. Every token for this user is revoked.
    reuse = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    later = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})

    assert reuse.status_code == 401
    assert later.status_code == 401


def test_logout_revokes_the_refresh_token(client: TestClient) -> None:
    tokens = login_tokens(client)

    assert (
        client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code
        == 204
    )
    after = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert after.status_code == 401


def test_github_login_redirects_with_state_and_pkce(client: TestClient) -> None:
    response = client.get("/auth/github/login", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "github.com"
    assert "scope" not in query
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["state"][0] == client.cookies["atlas_oauth"].split(".")[0]


def test_github_callback_rejects_a_state_it_did_not_issue(client: TestClient) -> None:
    client.get("/auth/github/login", follow_redirects=False)

    response = client.get("/auth/github/callback", params={"code": "good-code", "state": "forged"})

    assert response.status_code == 400


def test_github_callback_creates_then_reuses_the_account(
    client: TestClient, session: Session
) -> None:
    for _ in range(2):
        start = client.get("/auth/github/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        response = client.get("/auth/github/callback", params={"code": "good-code", "state": state})
        assert response.status_code == 200
        assert set(response.json()) >= {"access_token", "refresh_token"}

    accounts = session.scalars(select(User).where(User.github_id == 4242)).all()
    assert [account.github_login for account in accounts] == ["octo-learner"]


def test_github_callback_reports_a_bad_code(client: TestClient) -> None:
    start = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    response = client.get("/auth/github/callback", params={"code": "stale-code", "state": state})

    assert response.status_code == 502
    assert "incorrect or expired" in response.json()["error"]["message"]


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-me-123"})

    assert response.headers["X-Request-ID"] == "trace-me-123"


def test_cors_allows_only_configured_origins(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "GET"}

    allowed = client.options("/repos", headers={"Origin": "http://localhost:8000", **preflight})
    blocked = client.options("/repos", headers={"Origin": "https://evil.example", **preflight})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert "access-control-allow-origin" not in blocked.headers
