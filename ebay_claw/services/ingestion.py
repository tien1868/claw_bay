"""Normalize and validate ingested listings."""

from __future__ import annotations

from typing import List

from ebay_claw.adapters.base import ListingAdapter
from ebay_claw.models.domain import ListingRecord


class IngestionService:
    def __init__(self, adapter: ListingAdapter):
        self._adapter = adapter

    def load_listings(self) -> List[ListingRecord]:
        listings = self._adapter.fetch_active_listings()
        return [self._normalize(l) for l in listings]

    def _normalize(self, listing: ListingRecord) -> ListingRecord:
        title = (listing.title or "").strip()
        if len(title) > 80:
            title = title[:80]
        return listing.model_copy(update={"title": title})
