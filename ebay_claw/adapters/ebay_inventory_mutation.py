"""eBay Sell Inventory mutating calls (PUT) — used only from EbayWriteExecutor / guarded apply."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import httpx

from ebay_claw.adapters.ebay_oauth import ebay_api_base
from ebay_claw.adapters.read_only import safe_http_error_message
from ebay_claw.adapters.ebay_readonly_http import ApiCallBudget, ReadOnlyEbayInventoryClient, _TTLCache
from ebay_claw.config.settings import Settings
from ebay_claw.logging_config import get_logger
from ebay_claw.security.redaction import redact_string
from urllib.parse import quote

logger = get_logger(__name__)

TokenGetter = Callable[[], str]


@dataclass
class InventoryMutationResult:
    ok: bool
    http_status: int
    retryable: bool
    user_safe_message: str
    external_request_id: Optional[str] = None
    response_body_preview: Optional[str] = None


def _pick_request_id(headers: httpx.Headers) -> Optional[str]:
    for k, v in headers.items():
        lk = k.lower()
        if "correlation" in lk or "request-id" in lk or lk == "x-ebay-request-id":
            return str(v) if v else None
    return None


class EbayInventoryMutationClient:
    """
    GET inventory_item (reuse read client budget) + PUT inventory_item for title updates.
    """

    def __init__(
        self,
        settings: Settings,
        token_getter: TokenGetter,
        *,
        budget: Optional[ApiCallBudget] = None,
        transport: Optional[httpx.BaseTransport] = None,
        on_unauthorized: Optional[Callable[[], None]] = None,
    ):
        self._s = settings
        self._token_getter = token_getter
        self._base = ebay_api_base(settings).rstrip("/")
        self._transport = transport
        self._budget = budget or ApiCallBudget(max(1, settings.api_budget_max_calls_per_run))
        self._on_unauthorized = on_unauthorized
        self._read = ReadOnlyEbayInventoryClient(
            settings,
            token_getter,
            budget=self._budget,
            response_cache=_TTLCache(0, 0),
            transport=transport,
            on_unauthorized=on_unauthorized,
        )

    def get_inventory_item(self, sku: str) -> Dict[str, Any]:
        path = f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}"
        return self._read.get_json(path)

    def put_inventory_item(self, sku: str, body: Dict[str, Any]) -> InventoryMutationResult:
        path = f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}"
        return self._put_with_retries(path, body)

    def _put_with_retries(self, path: str, body: Dict[str, Any]) -> InventoryMutationResult:
        url = f"{self._base}{path}"
        last_err: Optional[str] = None
        max_attempts = min(self._s.ebay_max_retries, 8)
        for attempt in range(max_attempts):
            self._budget.consume()
            token = self._token_getter()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Language": "en-US",
                "X-EBAY-C-MARKETPLACE-ID": self._s.ebay_marketplace_id,
            }
            try:
                with httpx.Client(
                    timeout=self._s.ebay_http_timeout_sec,
                    transport=self._transport,
                ) as client:
                    r = client.put(url, headers=headers, content=json.dumps(body))
            except httpx.RequestError as e:
                last_err = redact_string(str(e))
                logger.warning(
                    "eBay PUT transport error attempt=%s path=%s err=%s",
                    attempt + 1,
                    path,
                    last_err,
                )
                self._sleep_backoff(attempt, None)
                continue

            rid = _pick_request_id(r.headers)

            if r.status_code == 429:
                self._sleep_backoff(
                    attempt,
                    float(r.headers.get("Retry-After"))
                    if r.headers.get("Retry-After", "").isdigit()
                    else None,
                )
                last_err = safe_http_error_message(r.status_code, r.text)
                continue

            if 500 <= r.status_code < 600:
                self._sleep_backoff(attempt, None)
                last_err = safe_http_error_message(r.status_code, r.text)
                continue

            if r.status_code == 401:
                if self._on_unauthorized:
                    try:
                        self._on_unauthorized()
                    except Exception as ex:
                        return InventoryMutationResult(
                            ok=False,
                            http_status=401,
                            retryable=False,
                            user_safe_message="eBay authentication failed for write request.",
                            external_request_id=rid,
                            response_body_preview=redact_string(str(ex))[:220],
                        )
                    continue
                return InventoryMutationResult(
                    ok=False,
                    http_status=401,
                    retryable=False,
                    user_safe_message="eBay returned 401 — token may be expired or missing write scope.",
                    external_request_id=rid,
                )

            if r.status_code in (200, 201, 204):
                return InventoryMutationResult(
                    ok=True,
                    http_status=r.status_code,
                    retryable=False,
                    user_safe_message="Inventory item updated successfully.",
                    external_request_id=rid,
                )

            # 400 validation, 404 unknown SKU, 409 conflict — non-retryable
            preview = (r.text or "")[:240].replace("\n", " ")
            return InventoryMutationResult(
                ok=False,
                http_status=r.status_code,
                retryable=False,
                user_safe_message=(
                    f"eBay rejected the update (HTTP {r.status_code}). "
                    "Check listing identifiers and permissions."
                ),
                external_request_id=rid,
                response_body_preview=preview,
            )

        return InventoryMutationResult(
            ok=False,
            http_status=0,
            retryable=True,
            user_safe_message="eBay write failed after retries — temporary API or network issue.",
            response_body_preview=redact_string(last_err or "")[:220],
        )

    def _sleep_backoff(self, attempt: int, override: Optional[float]) -> None:
        if override is not None:
            time.sleep(min(override, 60.0))
            return
        base = self._s.ebay_base_backoff_sec * (2**attempt)
        jitter = random.uniform(0, 0.25 * base)
        time.sleep(min(base + jitter, 30.0))
