import pytest


def test_create_user_hides_password(client):
    response = client.post("/users/", json={"email": "a@b.com", "password": "longenough"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "a@b.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_duplicate_email_is_rejected(client, user):
    response = client.post("/users/", json={"email": user["email"], "password": "whatever1"})
    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "longenough"},
        {"email": "a@b.com", "password": "short"},
        {"email": "a@b.com"},
    ],
)
def test_invalid_signup_returns_422(client, payload):
    assert client.post("/users/", json=payload).status_code == 422


def test_login_returns_bearer_token(client, user):
    form = {"username": user["email"], "password": user["password"]}
    response = client.post("/login", data=form)

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


@pytest.mark.parametrize(
    "email, password",
    [
        ("ada@example.com", "wrong-password"),
        ("nobody@example.com", "correct-horse"),
    ],
)
def test_bad_login_returns_same_401(client, user, email, password):
    response = client.post("/login", data={"username": email, "password": password})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_me_requires_token(client):
    assert client.get("/users/me").status_code == 401


def test_me_rejects_tampered_token(client, auth_headers):
    tampered = {"Authorization": auth_headers["Authorization"] + "x"}
    assert client.get("/users/me", headers=tampered).status_code == 401


def test_me_returns_current_user(client, user, auth_headers):
    response = client.get("/users/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == user["email"]
