"""Load normalized listings from JSON fixtures."""

from __future__ import annotations

import json
from datetime import date, datetime
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from ebay_claw.adapters.base import ListingAdapter
from ebay_claw.logging_config import get_logger
from ebay_claw.models.domain import ListingRecord
from ebay_claw.services.sync_state import SyncStateStore

logger = get_logger(__name__)


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def raw_dict_to_listing(raw: dict) -> ListingRecord:
    specifics = raw.get("item_specifics") or {}
    if not isinstance(specifics, dict):
        specifics = {}
    return ListingRecord(
        listing_id=str(raw["listing_id"]),
        title=str(raw.get("title", "")),
        sku=raw.get("sku"),
        category_id=raw.get("category_id"),
        price_amount=float(raw.get("price_amount", 0)),
        currency=str(raw.get("currency", "USD")),
        quantity=int(raw.get("quantity", 1)),
        listed_at=_parse_dt(raw.get("listed_at")),
        listed_on=_parse_date(raw.get("listed_on")),
        watchers=raw.get("watchers"),
        view_count=raw.get("view_count"),
        sold_quantity_last_90_days=raw.get("sold_quantity_last_90_days"),
        brand=raw.get("brand"),
        size=raw.get("size"),
        department=raw.get("department"),
        garment_type=raw.get("garment_type"),
        color=raw.get("color"),
        material=raw.get("material"),
        condition=raw.get("condition"),
        description=raw.get("description"),
        item_specifics={str(k): str(v) for k, v in specifics.items()},
        source_payload_ref=raw.get("source_payload_ref"),
        extra=dict(raw.get("extra") or {}),
    )


class MockJsonListingAdapter(ListingAdapter):
    def __init__(
        self,
        path: Path,
        sync_store: Optional[SyncStateStore] = None,
    ):
        self._path = path
        self._sync = sync_store

    def fetch_active_listings(self) -> List[ListingRecord]:
        started = datetime.now(timezone.utc)
        if not self._path.exists():
            logger.warning("Fixture missing at %s — returning empty", self._path)
            if self._sync:
                self._sync.mark_error(
                    "fixture",
                    f"missing_fixture:{self._path}",
                    started_at=started,
                )
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "listings" in data:
            rows = data["listings"]
        else:
            rows = data
        out: List[ListingRecord] = []
        for row in rows:
            try:
                out.append(raw_dict_to_listing(row))
            except Exception as e:
                logger.exception("Skip bad row: %s", e)
        if self._sync:
            self._sync.mark_ok(
                "fixture",
                len(out),
                1,
                started_at=started,
                message=f"fixture_file={self._path.name}",
            )
        return out
