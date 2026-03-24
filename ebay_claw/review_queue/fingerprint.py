"""Stable fingerprints for listing snapshots — detect live drift vs review items."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Union

from ebay_claw.models.domain import ListingRecord


def listing_snapshot_fingerprint(listing: Union[ListingRecord, Dict[str, Any]]) -> str:
    d = listing.model_dump() if isinstance(listing, ListingRecord) else dict(listing)
    payload = {
        "title": d.get("title"),
        "price_amount": d.get("price_amount"),
        "currency": d.get("currency"),
        "sku": d.get("sku"),
        "quantity": d.get("quantity"),
        "item_specifics": d.get("item_specifics") or {},
    }
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]
