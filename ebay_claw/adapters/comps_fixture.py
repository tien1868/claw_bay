"""Fixture-based sold comps for development and tests — replace with official API adapter later."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ebay_claw.adapters.comps_base import SoldCompsAdapter
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.comps import SoldCompRecord
from ebay_claw.models.domain import ListingRecord

logger = get_logger(__name__)


def _parse_date(s: Any) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, date):
        return s
    if isinstance(s, str):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _group_key(listing: ListingRecord) -> str:
    brand = (listing.brand or listing.item_specifics.get("Brand") or "").strip().lower()
    g = (listing.garment_type or "").strip().lower()
    sz = (listing.size or listing.item_specifics.get("Size") or "").strip().lower()
    return f"{brand}|{g}|{sz}"


def normalize_comp_row(row: dict) -> Optional[SoldCompRecord]:
    try:
        sd = _parse_date(row.get("sold_date"))
        if sd is None:
            return None
        price = float(row.get("sold_price", 0))
        if price <= 0:
            return None
        return SoldCompRecord(
            sold_price=price,
            currency=str(row.get("currency") or "USD"),
            sold_date=sd,
            match_quality=float(row.get("match_quality", 0.7)),
            condition_hint=row.get("condition_hint"),
            title_hint=row.get("title_hint"),
            source_listing_id=row.get("source_listing_id"),
        )
    except (TypeError, ValueError, KeyError):
        return None


class FixtureSoldCompsAdapter(SoldCompsAdapter):
    """
    Loads `fixtures/sold_comps.json` (or configured path):

    {
      "recency_window_days": 90,
      "listing_comps": { "L1": [ { "sold_price", "sold_date", ... }, ... ] },
      "group_comps": { "brand|type|size": [ ... ] }
    }
    """

    def __init__(self, path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = path or self._s.comps_fixture_path
        self._payload: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            self._payload = {}
            return
        try:
            self._payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Sold comps fixture load failed %s: %s", self._path, e)
            self._payload = {}

    def fetch_comps_for_listing(self, listing: ListingRecord) -> List[SoldCompRecord]:
        out: List[SoldCompRecord] = []
        seen: Set[Tuple[float, str]] = set()

        def add_from_rows(rows: Any) -> None:
            if not isinstance(rows, list):
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rec = normalize_comp_row(row)
                if rec is None:
                    continue
                key = (round(rec.sold_price, 2), rec.sold_date.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                out.append(rec)

        lc = self._payload.get("listing_comps") or {}
        if isinstance(lc, dict) and listing.listing_id in lc:
            add_from_rows(lc.get(listing.listing_id))

        gc = self._payload.get("group_comps") or {}
        if isinstance(gc, dict):
            gk = _group_key(listing)
            if gk in gc:
                add_from_rows(gc.get(gk))
            if "" not in gk and gk != "||":
                pass

        return out
