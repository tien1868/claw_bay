"""Last ingest/sync metadata — no secrets."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SyncState(BaseModel):
    source: Literal["fixture", "live"] = "fixture"
    runtime_mode: str = "fixture"
    status: Literal["idle", "running", "ok", "error", "partial"] = "idle"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    listing_count: int = 0
    message_safe: str = ""
    pages_fetched: int = 0
    partial_sync: bool = False
    warnings: List[str] = Field(default_factory=list)
    api_calls_used: int = 0
    api_budget_max: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    last_auth_failure_code: Optional[str] = None
