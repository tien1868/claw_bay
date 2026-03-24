"""Append-only operational history — read-only analytics; not marketplace writes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

OperationalEventType = Literal[
    "listing_created",
    "listing_synced",
    "listing_sold",
    "stale_crossed_90d",
    "stale_cleared",
    "relist_proposed",
    "bundle_proposed",
    "queue_approved",
    "queue_rejected",
]


class OperationalEventRecord(BaseModel):
    """Single append-only operational event (JSONL row)."""

    event_id: str
    event_type: OperationalEventType
    occurred_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(
        default="unknown",
        description="pipeline | sync | queue | recovery | inventory_tracker",
    )
    listing_id: Optional[str] = None
    review_item_id: Optional[str] = None
    actor: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}
