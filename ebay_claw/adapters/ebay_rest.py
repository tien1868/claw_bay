"""Read-only eBay Inventory API → ListingRecord list."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ebay_claw.adapters.base import ListingAdapter
from ebay_claw.adapters.ebay_normalize import (
    merge_inventory_and_offer,
    offer_is_active_published,
)
from ebay_claw.adapters.auth_codes import classify_auth_message
from ebay_claw.adapters.ebay_oauth import (
    live_credentials_configured,
    recover_inventory_session_after_401,
    resolve_access_token,
)
from ebay_claw.adapters.ebay_readonly_http import (
    ApiCallBudget,
    ReadOnlyEbayInventoryClient,
    _TTLCache,
)
from ebay_claw.audit.store import AuditLogStore, new_event_id
from ebay_claw.config.settings import Settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.audit import AuditEvent
from ebay_claw.models.domain import ListingRecord
from ebay_claw.security.redaction import redact_string
from ebay_claw.services.sync_state import SyncStateStore

logger = get_logger(__name__)


def _cache_from_usage(usage: dict) -> tuple[int, int]:
    return int(usage.get("cache_hits") or 0), int(usage.get("cache_misses") or 0)


def _select_offers_for_listing(offers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active = [o for o in offers if offer_is_active_published(o)]
    if not active:
        return []
    fixed = [o for o in active if str(o.get("format") or "").upper() == "FIXED_PRICE"]
    return fixed or active


class EbayInventoryListingAdapter(ListingAdapter):
    """
    Paginates GET /sell/inventory/v1/inventory_item and GET .../offer per SKU.
    Official REST APIs only — no scraping or HTML parsing.
    """

    def __init__(
        self,
        settings: Settings,
        sync_store: Optional[SyncStateStore] = None,
        http_client: Optional[ReadOnlyEbayInventoryClient] = None,
    ):
        self._s = settings
        self._sync = sync_store or SyncStateStore(settings=settings)
        self._token_holder: Dict[str, Any] = {"t": None}
        self._override_client = http_client

        def token_getter() -> str:
            t = self._token_holder.get("t")
            if t:
                return str(t)
            if self._s.ebay_access_token and self._s.ebay_access_token.strip():
                return self._s.ebay_access_token.strip()
            tok = resolve_access_token(self._s)
            self._token_holder["t"] = tok
            return tok

        self._token_getter: Callable[[], str] = token_getter

    def _audit_sync(
        self,
        event_type: str,
        *,
        listing_count: int,
        api_used: int,
        budget_max: int,
        partial: bool = False,
        reason_codes: Optional[List[str]] = None,
    ) -> None:
        store = AuditLogStore(settings=self._s)
        store.append(
            AuditEvent(
                event_id=new_event_id(),
                event_type=event_type,  # type: ignore[arg-type]
                timestamp_utc=datetime.now(timezone.utc),
                actor=self._s.default_actor,
                reason_codes=reason_codes or [],
                redacted_meta={
                    "runtime_mode": self._s.runtime_mode.value,
                    "data_source": self._s.data_source,
                    "listing_count": listing_count,
                    "api_calls_used": api_used,
                    "api_budget_max": budget_max,
                    "partial_sync": partial,
                },
            )
        )

    def _build_client(self) -> ReadOnlyEbayInventoryClient:
        budget = ApiCallBudget(self._s.api_budget_max_calls_per_run)
        cache = _TTLCache(self._s.api_cache_ttl_sec, self._s.api_cache_max_entries)

        def on_401() -> None:
            recover_inventory_session_after_401(self._s, self._token_holder)

        return ReadOnlyEbayInventoryClient(
            self._s,
            self._token_getter,
            budget=budget,
            response_cache=cache,
            on_unauthorized=on_401,
        )

    def fetch_active_listings(self) -> List[ListingRecord]:
        if not live_credentials_configured(self._s):
            raise RuntimeError("eBay live credentials are not configured")

        client = self._override_client or self._build_client()

        self._sync.mark_running("live")
        budget_max = self._s.api_budget_max_calls_per_run
        self._audit_sync(
            "sync_started",
            listing_count=0,
            api_used=0,
            budget_max=budget_max,
        )
        started = datetime.now(timezone.utc)
        results: List[ListingRecord] = []
        pages = 0
        budget_cutoff = False
        try:
            limit = self._s.ebay_inventory_page_size
            page_index = 0
            total_pages: Optional[int] = None

            while True:
                params = {"limit": str(limit), "offset": str(page_index)}
                data = client.get_json("/sell/inventory/v1/inventory_item", params)
                pages += 1
                items = data.get("inventoryItems") or []
                if total_pages is None:
                    sz = data.get("size")
                    try:
                        total_pages = int(sz) if sz is not None else None
                    except (TypeError, ValueError):
                        total_pages = None

                if not items:
                    break

                for inv in items:
                    sku = inv.get("sku")
                    if not sku:
                        continue
                    try:
                        offers_data = client.get_json(
                            "/sell/inventory/v1/offer",
                            {"sku": str(sku)},
                        )
                    except RuntimeError as e:
                        err_part = str(e)
                        if "ebay_api_budget_exceeded" in err_part:
                            logger.warning(
                                "API budget exhausted during offer fetch sku=%s",
                                sku,
                            )
                            budget_cutoff = True
                            break
                        logger.warning(
                            "Skipping SKU after offer fetch failure sku=%s err=%s",
                            sku,
                            redact_string(err_part),
                        )
                        continue

                    raw_offers = offers_data.get("offers") or []
                    picked = _select_offers_for_listing(raw_offers)
                    for offer in picked:
                        try:
                            results.append(merge_inventory_and_offer(inv, offer))
                        except Exception as e:
                            logger.warning(
                                "Normalize failed sku=%s offer=%s err=%s",
                                sku,
                                offer.get("offerId"),
                                redact_string(str(e)),
                            )

                if budget_cutoff:
                    break

                page_index += 1
                if total_pages is not None and page_index >= total_pages:
                    break
                if len(items) < limit:
                    break

            usage = client.usage_summary()
            api_used = int(usage.get("budget_used") or 0)
            msg = (
                f"inventory_items_pages={pages} listings={len(results)} "
                f"budget_used={usage.get('budget_used')} cache_hits={usage.get('cache_hits')}"
            )
            if budget_cutoff:
                warn = "api_budget_exceeded_partial_sync"
                ch, cm = _cache_from_usage(usage)
                self._sync.mark_ok(
                    "live",
                    len(results),
                    pages,
                    started_at=started,
                    message=warn,
                    partial_sync=True,
                    warnings=[warn],
                    api_calls_used=api_used,
                    api_budget_max=budget_max,
                    cache_hits=ch,
                    cache_misses=cm,
                )
                self._audit_sync(
                    "sync_completed",
                    listing_count=len(results),
                    api_used=api_used,
                    budget_max=budget_max,
                    partial=True,
                    reason_codes=[warn, redact_string(msg)[:180]],
                )
                logger.warning(
                    "eBay live sync stopped on API budget listings=%s usage=%s",
                    len(results),
                    redact_string(str(usage)),
                )
                return results

            ch, cm = _cache_from_usage(usage)
            self._sync.mark_ok(
                "live",
                len(results),
                pages,
                started_at=started,
                message=msg,
                partial_sync=False,
                warnings=[],
                api_calls_used=api_used,
                api_budget_max=budget_max,
                cache_hits=ch,
                cache_misses=cm,
            )
            self._audit_sync(
                "sync_completed",
                listing_count=len(results),
                api_used=api_used,
                budget_max=budget_max,
                reason_codes=[redact_string(msg)[:220]],
            )
            logger.info(
                "eBay live sync complete listings=%s api_usage=%s",
                len(results),
                redact_string(str(usage)),
            )
            return results

        except RuntimeError as e:
            err_s = str(e)
            if "ebay_api_budget_exceeded" in err_s:
                usage = client.usage_summary()
                api_used = int(usage.get("budget_used") or 0)
                warn = "api_budget_exceeded_partial_sync"
                ch, cm = _cache_from_usage(usage)
                self._sync.mark_ok(
                    "live",
                    len(results),
                    pages,
                    started_at=started,
                    message=warn,
                    partial_sync=True,
                    warnings=[warn],
                    api_calls_used=api_used,
                    api_budget_max=budget_max,
                    cache_hits=ch,
                    cache_misses=cm,
                )
                self._audit_sync(
                    "sync_completed",
                    listing_count=len(results),
                    api_used=api_used,
                    budget_max=budget_max,
                    partial=True,
                    reason_codes=[warn],
                )
                logger.warning(
                    "eBay live sync stopped on API budget listings=%s usage=%s",
                    len(results),
                    redact_string(str(usage)),
                )
                return results

            msg = redact_string(err_s)
            usage = client.usage_summary()
            api_used = int(usage.get("budget_used") or 0)
            ch, cm = _cache_from_usage(usage)
            self._sync.mark_error(
                "live",
                msg[:500],
                started_at=started,
                listing_count=len(results),
                pages_fetched=pages,
                api_calls_used=api_used,
                auth_failure_code=classify_auth_message(msg),
                cache_hits=ch,
                cache_misses=cm,
            )
            self._audit_sync(
                "sync_failed",
                listing_count=len(results),
                api_used=api_used,
                budget_max=budget_max,
                reason_codes=[msg[:220]],
            )
            logger.warning("eBay live sync failed: %s", msg)
            raise

        except Exception as e:
            msg = redact_string(str(e))
            try:
                usage = client.usage_summary()
            except Exception:
                usage = {}
            api_used = int(usage.get("budget_used") or 0)
            ch, cm = _cache_from_usage(usage)
            self._sync.mark_error(
                "live",
                msg[:500],
                started_at=started,
                listing_count=len(results),
                pages_fetched=pages,
                api_calls_used=api_used,
                auth_failure_code=classify_auth_message(msg),
                cache_hits=ch,
                cache_misses=cm,
            )
            self._audit_sync(
                "sync_failed",
                listing_count=len(results),
                api_used=api_used,
                budget_max=budget_max,
                reason_codes=[msg[:220]],
            )
            logger.warning("eBay live sync failed: %s", msg)
            raise
