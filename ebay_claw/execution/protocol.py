"""Executor boundary — mock today, eBay Inventory/Offer later."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from ebay_claw.models.domain import ApplyResult, ReviewQueueItem


@runtime_checkable
class ListingWriteExecutor(Protocol):
    def apply(
        self,
        item: ReviewQueueItem,
        listing_snapshot: Optional[dict] = None,
        *,
        idempotency_key: str,
        legacy_audit: bool = True,
        transition_queue: bool = False,
    ) -> ApplyResult: ...
