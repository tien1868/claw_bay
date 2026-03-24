"""Stable auth failure codes for dashboards and sync state (no secrets). AUTH_* values are stable API for operators."""

from __future__ import annotations

# Config / env — live mode without OAuth pieces
AUTH_MISSING_CREDENTIALS = "auth_missing_credentials"
# Static access token rejected by eBay (401) before or after refresh attempt
AUTH_ACCESS_TOKEN_REJECTED = "auth_access_token_rejected"
# No refresh_token (or missing client id/secret) so recovery cannot run
AUTH_REFRESH_UNAVAILABLE = "auth_refresh_unavailable"
# Refresh endpoint returned non-success
AUTH_REFRESH_FAILED = "auth_refresh_failed"
# Network / parsing / uncategorized
AUTH_REQUEST_FAILED = "auth_request_failed"


def classify_auth_message(message: str) -> str:
    """Map a redacted error string to a stable auth code for UI and sync_state."""
    m = (message or "").lower()
    if "missing credentials" in m or ("not configured" in m and "oauth" in m):
        return AUTH_MISSING_CREDENTIALS
    if "refresh_token not configured" in m or "no refresh_token" in m:
        return AUTH_REFRESH_UNAVAILABLE
    if "client_id and client_secret required" in m or "refresh_token required" in m:
        return AUTH_REFRESH_UNAVAILABLE
    if "oauth token refresh failed" in m or "token refresh failed" in m:
        return AUTH_REFRESH_FAILED
    if "credentials rejected after one recovery" in m or "401" in m or "unauthorized" in m:
        return AUTH_ACCESS_TOKEN_REJECTED
    return AUTH_REQUEST_FAILED
