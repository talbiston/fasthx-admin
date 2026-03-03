"""
OIDC authentication helpers.

Replicates the Resource Owner Password Credentials flow used by Keycloak,
without any Flask dependency.  Two HTTP calls:
  1. POST credentials to Keycloak token endpoint (password grant)
  2. GET userinfo with the access token

Set ``AUTH_DISABLED=1`` to bypass auth entirely (local dev without Keycloak).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests
from fastapi import Request

log = logging.getLogger(__name__)

AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes")

ALLOWED_GROUPS: list[str] = [
    "/sdn.automation",
    "/Edge-Admins",
    "/Edge-Support",
    "/UCPE Admins",
]

# ---------------------------------------------------------------------------
# OIDC secrets
# ---------------------------------------------------------------------------

_secrets: dict | None = None


def _load_secrets() -> dict:
    """Load OIDC client secrets (same JSON format as old-ui docker/client_secrets.json)."""
    global _secrets
    if _secrets is not None:
        return _secrets

    secrets_path = os.environ.get(
        "OIDC_SECRETS", str(Path.cwd() / "client_secrets.json")
    )
    with open(secrets_path) as f:
        data = json.load(f)

    # Support both top-level and nested {"web": {...}} format
    _secrets = data.get("web", data)
    return _secrets


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Raised when OIDC authentication fails."""


def oidc_login(username: str, password: str) -> dict:
    """Exchange credentials for tokens via Keycloak, fetch userinfo, check groups.

    Returns ``{"username": ..., "groups": [...]}`` on success.
    Raises ``AuthError`` with a user-friendly message on failure.
    """
    secrets = _load_secrets()
    log.info("OIDC login attempt for user=%s", username)
    log.debug("token_uri=%s  client_id=%s", secrets.get("token_uri"), secrets.get("client_id"))

    # 1. Token request (Resource Owner Password Credentials grant)
    try:
        token_resp = requests.post(
            secrets["token_uri"],
            data={
                "grant_type": "password",
                "client_id": secrets["client_id"],
                "client_secret": secrets["client_secret"],
                "scope": "openid",
                "username": username,
                "password": password,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        log.error("Token request failed (network): %s", exc)
        raise AuthError(f"Cannot reach Keycloak: {exc}")

    log.info("Token response: status=%s", token_resp.status_code)
    log.debug("Token response body: %s", token_resp.text[:500])

    if token_resp.status_code != 200:
        detail = token_resp.json().get("error_description", "Invalid credentials")
        log.warning("Token request rejected: %s", detail)
        raise AuthError(detail)

    access_token = token_resp.json()["access_token"]

    # 2. Userinfo request
    try:
        userinfo_resp = requests.get(
            secrets["userinfo_uri"],
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        log.error("Userinfo request failed (network): %s", exc)
        raise AuthError(f"Cannot reach Keycloak userinfo: {exc}")

    log.info("Userinfo response: status=%s", userinfo_resp.status_code)
    log.debug("Userinfo body: %s", userinfo_resp.text[:500])

    if userinfo_resp.status_code != 200:
        raise AuthError("Failed to retrieve user information")

    userinfo = userinfo_resp.json()
    groups: list[str] = userinfo.get("member_of", [])
    log.info("User groups: %s", groups)

    # 3. Group check
    if not any(g in ALLOWED_GROUPS for g in groups):
        log.warning("User %s not in allowed groups. Has: %s", username, groups)
        raise AuthError("You are not a member of an authorized group")

    return {
        "username": userinfo.get("preferred_username", username),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# FastAPI helpers
# ---------------------------------------------------------------------------


def get_current_user(request: Request) -> dict | None:
    """Return user dict from the session, or a mock user when auth is disabled."""
    if AUTH_DISABLED:
        return {"username": "dev", "groups": ["/Edge-Admins"]}
    return request.session.get("user")
