"""Hard read-only guard for eBay HTTP — blocks any non-GET semantics."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


class ReadOnlyViolationError(RuntimeError):
    """Raised if a write-oriented call is attempted on a read-only client."""


def assert_read_only_method(method: str) -> None:
    m = (method or "").upper()
    if m != "GET":
        raise ReadOnlyViolationError(
            f"eBay Claw live integration is read-only; attempted method {method!r}"
        )


def redact_url_for_log(url: str) -> str:
    if "access_token" in url.lower() or "refresh_token" in url.lower():
        return "[redacted-url]"
    return url


def safe_http_error_message(
    status: Optional[int],
    body_preview: Optional[str],
) -> str:
    prev = (body_preview or "")[:240].replace("\n", " ")
    return f"http_status={status} body_preview={prev!r}"
