def test_list_posts_is_public(client, post):
    response = client.get("/posts/")

    assert response.status_code == 200
    assert response.json()[0]["post"]["title"] == "First post"
    assert response.json()[0]["votes"] == 0


def test_create_post_requires_login(client):
    response = client.post("/posts/", json={"title": "t", "content": "c"})
    assert response.status_code == 401


def test_create_post_sets_owner_from_token(client, user, post):
    assert post["owner_id"] == user["id"]
    assert post["owner"]["email"] == user["email"]
    assert post["published"] is True


def test_get_missing_post_returns_404(client):
    assert client.get("/posts/9999").status_code == 404


def test_search_and_pagination(client, auth_headers):
    for title in ["apple pie", "banana bread", "apple tart"]:
        client.post("/posts/", json={"title": title, "content": "x"}, headers=auth_headers)

    apples = client.get("/posts/", params={"search": "apple"}).json()
    assert len(apples) == 2

    first_page = client.get("/posts/", params={"limit": 2}).json()
    second_page = client.get("/posts/", params={"limit": 2, "skip": 2}).json()
    assert len(first_page) == 2
    assert len(second_page) == 1


def test_patch_changes_only_sent_fields(client, post, auth_headers):
    response = client.patch(
        f"/posts/{post['id']}", json={"published": False}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["published"] is False
    assert response.json()["title"] == post["title"]


def test_cannot_edit_someone_elses_post(client, post, other_auth_headers):
    response = client.patch(
        f"/posts/{post['id']}", json={"title": "hacked"}, headers=other_auth_headers
    )
    assert response.status_code == 403


def test_owner_can_delete_post(client, post, auth_headers):
    response = client.delete(f"/posts/{post['id']}", headers=auth_headers)

    assert response.status_code == 204
    assert client.get(f"/posts/{post['id']}").status_code == 404


def test_vote_counts_and_rules(client, post, auth_headers, other_auth_headers):
    upvote = {"post_id": post["id"], "direction": "up"}

    assert client.post("/votes/", json=upvote, headers=auth_headers).status_code == 201
    assert client.post("/votes/", json=upvote, headers=other_auth_headers).status_code == 201
    assert client.post("/votes/", json=upvote, headers=auth_headers).status_code == 409
    assert client.get(f"/posts/{post['id']}").json()["votes"] == 2

    remove = {"post_id": post["id"], "direction": "remove"}
    assert client.post("/votes/", json=remove, headers=auth_headers).status_code == 201
    assert client.get(f"/posts/{post['id']}").json()["votes"] == 1


def test_vote_on_missing_post_returns_404(client, auth_headers):
    vote = {"post_id": 9999, "direction": "up"}
    assert client.post("/votes/", json=vote, headers=auth_headers).status_code == 404
