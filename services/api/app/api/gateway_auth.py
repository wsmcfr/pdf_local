"""Authentication for inbound gateway requests.

The inbound OpenAI/Claude-compatible endpoints use a local gateway token rather
than the desktop app's ``KB_TOKEN``. The token is resolved in this order:

1. ``KB_GATEWAY_TOKEN`` environment variable (useful for tests/CI)
2. the ``gateway-token`` entry in the OS credential store
3. a freshly generated token written back to the credential store

Errors never echo the token or the credential store contents.
"""

from __future__ import annotations

import logging
import os
import secrets

from fastapi import Header, HTTPException, Request

from ..providers import secret_store

logger = logging.getLogger(__name__)

TOKEN_KEY = "gateway-token"

# Used only when the credential store cannot persist an auto-generated token;
# keeping it process-local means repeated requests still authenticate.
_generated_token: str | None = None


def _compare(provided: str, expected: str) -> bool:
    return secrets.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    )


def _keyring_token() -> str | None:
    try:
        return secret_store.get_secret(TOKEN_KEY)
    except Exception:  # noqa: BLE001 - keyring failure must not leak details
        logger.warning("gateway token read from keyring failed", exc_info=True)
        return None


def get_gateway_token() -> str:
    """Return the gateway token, creating it on first use if needed."""
    global _generated_token

    env = os.environ.get("KB_GATEWAY_TOKEN")
    if env and env.strip():
        return env.strip()

    token = _keyring_token()
    if token:
        return token

    if _generated_token:
        return _generated_token

    token = secrets.token_urlsafe(32)
    try:
        secret_store.set_secret(TOKEN_KEY, token)
    except Exception:  # noqa: BLE001 - fall back to the process-local token
        logger.warning("gateway token write to keyring failed", exc_info=True)
    _generated_token = token
    return token


def rotate_gateway_token() -> str:
    """Generate and persist a new gateway token.

    The in-process fallback is cleared first so a failed keyring write cannot
    leave the old cached token usable.
    """
    global _generated_token

    _generated_token = None
    token = secrets.token_urlsafe(32)
    secret_store.set_secret(TOKEN_KEY, token)
    _generated_token = token
    return token


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    value = value.strip()
    return value or None


def require_gateway(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> None:
    """FastAPI dependency guarding every inbound gateway route."""
    settings = getattr(request.app.state, "settings", {}) or {}
    if not settings.get("gateway_enabled", False):
        raise HTTPException(status_code=403, detail="GATEWAY_DISABLED")

    provided = _bearer_token(authorization)
    if not provided and x_api_key and x_api_key.strip():
        provided = x_api_key.strip()

    expected = get_gateway_token()
    if not provided or not expected or not _compare(provided, expected):
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
