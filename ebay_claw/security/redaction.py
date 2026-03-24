"""Remove secrets from strings and structures before logs, errors, or audit payloads."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Union

# Bearer tokens, Basic auth, common OAuth keys, JWT-like
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization:\s*)bearer\s+\S+", re.I), r"\1Bearer [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}", re.I), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}", re.I), "Basic [REDACTED]"),
    (
        re.compile(
            r"(?i)(access_token|refresh_token|id_token)\s*[:=]\s*['\"]?"
            r"[\w\-._~+/=]+['\"]?",
            re.I,
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)(client_secret|ebay_client_secret)\s*[:=]\s*\S+", re.I), r"\1=[REDACTED]"),
    (re.compile(r"eyJ[\w\-._~+/]*=*"), "[REDACTED_JWT]"),
)


def redact_string(value: str) -> str:
    if not value:
        return value
    s = value
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


JsonMap = dict[str, Any]


def redact_mapping(obj: JsonMap, _depth: int = 0) -> JsonMap:
    if _depth > 12:
        return {"_redaction": "max_depth"}
    sensitive_keys = frozenset(
        k.lower()
        for k in (
            "authorization",
            "access_token",
            "refresh_token",
            "client_secret",
            "ebay_access_token",
            "ebay_refresh_token",
            "ebay_client_secret",
            "password",
            "secret",
        )
    )
    out: JsonMap = {}
    for k, v in obj.items():
        lk = str(k).lower()
        if lk in sensitive_keys:
            out[k] = "[REDACTED]"
        elif isinstance(v, str):
            out[k] = redact_string(v)
        elif isinstance(v, Mapping):
            out[k] = redact_mapping(dict(v), _depth + 1)
        elif isinstance(v, list):
            out[k] = [
                redact_mapping(dict(x), _depth + 1)
                if isinstance(x, Mapping)
                else redact_string(x)
                if isinstance(x, str)
                else x
                for x in v[:50]
            ]
        else:
            out[k] = v
    return out


def redact_for_log(message: Union[str, JsonMap]) -> Union[str, JsonMap]:
    if isinstance(message, str):
        return redact_string(message)
    return redact_mapping(dict(message))


def safe_exception_message(exc: BaseException) -> str:
    return redact_string(str(exc))
