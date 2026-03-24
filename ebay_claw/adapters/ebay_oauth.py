"""OAuth access token — refresh_token grant; secrets never logged."""

from __future__ import annotations

import base64

import httpx

from ebay_claw.adapters.read_only import safe_http_error_message
from ebay_claw.config.settings import Settings
from ebay_claw.logging_config import get_logger
from ebay_claw.security.redaction import redact_string

logger = get_logger(__name__)


class TokenRefreshError(RuntimeError):
    pass


class EbayAuthFailure(RuntimeError):
    """Authentication failure — message is safe for logs (no token material)."""


def ebay_identity_base(settings: Settings) -> str:
    if settings.ebay_use_sandbox:
        return "https://api.sandbox.ebay.com/identity/v1"
    return "https://api.ebay.com/identity/v1"


def ebay_api_base(settings: Settings) -> str:
    if settings.ebay_use_sandbox:
        return "https://api.sandbox.ebay.com"
    return "https://api.ebay.com"


def _basic_auth_header(settings: Settings) -> str:
    pair = f"{settings.ebay_client_id}:{settings.ebay_client_secret}"
    enc = base64.b64encode(pair.encode("utf-8")).decode("ascii")
    return f"Basic {enc}"


def refresh_access_token(settings: Settings) -> str:
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        raise TokenRefreshError("client_id and client_secret required for token refresh")
    if not settings.ebay_refresh_token:
        raise TokenRefreshError("refresh_token required for token refresh")

    url = f"{ebay_identity_base(settings)}/oauth2/token"
    headers = {
        "Authorization": _basic_auth_header(settings),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": settings.ebay_refresh_token,
        "scope": settings.ebay_oauth_scope,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, headers=headers, data=data)

    if r.status_code != 200:
        msg = redact_string(safe_http_error_message(r.status_code, r.text))
        logger.warning("OAuth token refresh failed: %s", msg)
        raise TokenRefreshError(msg)

    payload = r.json()
    token = payload.get("access_token")
    if not token:
        raise TokenRefreshError("access_token missing in OAuth response")
    logger.info("eBay OAuth access token refreshed (read-only scope)")
    return str(token)


def recover_inventory_session_after_401(settings: Settings, token_holder: dict) -> None:
    """
    Single controlled refresh after a 401 on Inventory API.
    Without refresh_token: fail closed immediately (no retry storm).
    """
    if token_holder.get("_oauth_recovered"):
        raise EbayAuthFailure("eBay credentials rejected after one recovery attempt")
    if not settings.ebay_refresh_token or not str(settings.ebay_refresh_token).strip():
        raise EbayAuthFailure("eBay 401: refresh_token not configured")
    if not settings.ebay_client_id or not settings.ebay_client_secret:
        raise EbayAuthFailure("eBay 401: client_id and client_secret required for token refresh")
    token_holder["_oauth_recovered"] = True
    token_holder["t"] = refresh_access_token(settings)


def resolve_access_token(settings: Settings) -> str:
    """Return a usable bearer token, refreshing when no static access_token is set."""
    if settings.ebay_access_token and settings.ebay_access_token.strip():
        return settings.ebay_access_token.strip()
    return refresh_access_token(settings)


def live_credentials_configured(settings: Settings) -> bool:
    if settings.ebay_access_token and settings.ebay_access_token.strip():
        return True
    return bool(
        settings.ebay_client_id
        and settings.ebay_client_secret
        and settings.ebay_refresh_token
    )
