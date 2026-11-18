"""Sign in with GitHub (OAuth2 web flow): turn a one-time code into a GitHub identity.

Atlas asks only for the user's public identity: no scopes, so no repository access.
It uses both protections GitHub's documentation strongly recommends: `state` against
forged callbacks, and PKCE so an intercepted code is useless without the verifier.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from atlas.github import GITHUB_API, GitHubError

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"


@dataclass(frozen=True)
class GitHubIdentity:
    github_id: int
    login: str


def new_pkce_pair() -> tuple[str, str]:
    """A random code verifier and its S256 code challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    # Base64url without padding gives the 43-character challenge GitHub expects.
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    """Where to send the browser so the user can approve signing in to Atlas."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            # Echoed back to the callback; proves the callback answers our own request (lesson 4.9).
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # No "scope" parameter: Atlas only needs to know who the user is.
            "allow_signup": "true",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code_verifier: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> GitHubIdentity:
    """Swap the one-time code for an access token, then ask GitHub who it belongs to."""
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        token_response = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        # GitHub explains problems (an expired code, a wrong secret) in the JSON body
        # with "error" and "error_description" fields, so check the body, not only the status.
        token_body = token_response.json()
        github_token = token_body.get("access_token")
        if not token_response.is_success or github_token is None:
            reason = token_body.get("error_description") or token_body.get("error") or "unknown"
            raise GitHubError(f"GitHub sign-in failed: {reason}")

        user_response = await client.get(
            f"{GITHUB_API}/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "atlas-course",
            },
        )
    if not user_response.is_success:
        raise GitHubError(f"GitHub answered {user_response.status_code} for the signed-in user")

    user = user_response.json()
    return GitHubIdentity(github_id=user["id"], login=user["login"])
