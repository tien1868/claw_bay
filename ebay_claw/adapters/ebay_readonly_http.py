"""Read-only httpx wrapper: GET only, retries, backoff, rate limits, budget, optional cache."""

from __future__ import annotations

import hashlib
import json
import random
import time
from typing import Any, Callable, Dict, Optional

import httpx

from ebay_claw.adapters.read_only import assert_read_only_method, safe_http_error_message
from ebay_claw.config.settings import Settings
from ebay_claw.logging_config import get_logger
from ebay_claw.security.redaction import redact_string

logger = get_logger(__name__)

TokenGetter = Callable[[], str]


class ApiCallBudget:
    """Hard cap on HTTP calls per ingest run."""

    def __init__(self, max_calls: int):
        self.max_calls = max(1, max_calls)
        self.used = 0

    def consume(self) -> None:
        self.used += 1
        if self.used > self.max_calls:
            raise RuntimeError("ebay_api_budget_exceeded")


class _TTLCache:
    def __init__(self, ttl_sec: float, max_entries: int):
        self.ttl_sec = ttl_sec
        self.max_entries = max(0, max_entries)
        self._store: Dict[str, tuple[float, dict]] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, path: str, params: Optional[Dict[str, Any]]) -> str:
        blob = json.dumps({"path": path, "p": params or {}}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, path: str, params: Optional[Dict[str, Any]]) -> Optional[dict]:
        if self.ttl_sec <= 0 or self.max_entries <= 0:
            self.misses += 1
            return None
        k = self._key(path, params)
        row = self._store.get(k)
        if not row:
            self.misses += 1
            return None
        exp, body = row
        if time.time() > exp:
            del self._store[k]
            self.misses += 1
            return None
        self.hits += 1
        return body

    def set(self, path: str, params: Optional[Dict[str, Any]], body: dict) -> None:
        if self.ttl_sec <= 0 or self.max_entries <= 0:
            return
        k = self._key(path, params)
        while len(self._store) >= self.max_entries and self._store:
            self._store.pop(next(iter(self._store)))
        self._store[k] = (time.time() + self.ttl_sec, body)


class ReadOnlyEbayInventoryClient:
    """
    Inventory API client: official REST GET only.
    """

    def __init__(
        self,
        settings: Settings,
        token_getter: TokenGetter,
        base_url: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        budget: Optional[ApiCallBudget] = None,
        response_cache: Optional[_TTLCache] = None,
        on_unauthorized: Optional[Callable[[], None]] = None,
    ):
        self._s = settings
        self._token_getter = token_getter
        from ebay_claw.adapters.ebay_oauth import ebay_api_base

        self._base = (base_url or ebay_api_base(settings)).rstrip("/")
        self._transport = transport
        self._budget = budget
        self._cache = response_cache or _TTLCache(0, 0)
        self._on_unauthorized = on_unauthorized

    def usage_summary(self) -> dict:
        return {
            "budget_used": self._budget.used if self._budget else None,
            "budget_max": self._budget.max_calls if self._budget else None,
            "cache_hits": self._cache.hits,
            "cache_misses": self._cache.misses,
        }

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert_read_only_method("GET")
        cached = self._cache.get(path, params)
        if cached is not None:
            return cached

        if self._budget:
            self._budget.consume()

        body = self._request_with_retries("GET", path, params=params)
        self._cache.set(path, params, body)
        return body

    def _request_with_retries(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        assert_read_only_method(method)
        url = f"{self._base}{path}"
        last_err: Optional[str] = None
        max_attempts = min(self._s.ebay_max_retries, 8)
        for attempt in range(max_attempts):
            token = self._token_getter()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": self._s.ebay_marketplace_id,
            }
            try:
                with httpx.Client(
                    timeout=self._s.ebay_http_timeout_sec,
                    transport=self._transport,
                ) as client:
                    r = client.get(url, headers=headers, params=params)
            except httpx.RequestError as e:
                last_err = redact_string(str(e))
                logger.warning(
                    "eBay GET transport error attempt=%s path=%s err=%s",
                    attempt + 1,
                    path,
                    last_err,
                )
                self._sleep_backoff(attempt, None)
                continue

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else None
                logger.warning(
                    "eBay rate limited path=%s attempt=%s retry_after=%s",
                    path,
                    attempt + 1,
                    retry_after,
                )
                self._sleep_backoff(attempt, wait)
                last_err = safe_http_error_message(r.status_code, r.text)
                continue

            if 500 <= r.status_code < 600:
                logger.warning(
                    "eBay server error path=%s status=%s attempt=%s",
                    path,
                    r.status_code,
                    attempt + 1,
                )
                self._sleep_backoff(attempt, None)
                last_err = safe_http_error_message(r.status_code, r.text)
                continue

            if r.status_code == 401:
                from ebay_claw.adapters.ebay_oauth import EbayAuthFailure

                detail = redact_string(safe_http_error_message(r.status_code, r.text))
                logger.warning("eBay 401 unauthorized path=%s detail=%s", path, detail)
                if self._on_unauthorized:
                    try:
                        self._on_unauthorized()
                    except EbayAuthFailure as e:
                        raise RuntimeError(redact_string(str(e))) from e
                    last_err = "401_recovered"
                    continue
                raise RuntimeError(redact_string(f"401 unauthorized path={path}"))

            if r.status_code != 200:
                msg = safe_http_error_message(r.status_code, r.text)
                logger.warning("eBay GET failed path=%s %s", path, redact_string(msg))
                raise RuntimeError(redact_string(msg))

            try:
                return r.json()
            except ValueError as e:
                raise RuntimeError(
                    redact_string(f"invalid JSON from eBay path={path}: {e}")
                ) from e

        raise RuntimeError(redact_string(last_err or "eBay request exhausted retries"))

    def _sleep_backoff(self, attempt: int, override: Optional[float]) -> None:
        if override is not None:
            time.sleep(min(override, 60.0))
            return
        base = self._s.ebay_base_backoff_sec * (2**attempt)
        jitter = random.uniform(0, 0.25 * base)
        cap = min(base + jitter, 30.0)
        time.sleep(cap)
