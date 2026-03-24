"""Append-only sync run history entries (operator visibility)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SyncHistoryEntry(BaseModel):
    completed_at_utc: datetime
    source: Literal["fixture", "live"]
    runtime_mode: str
    status: Literal["ok", "error", "partial"]
    listing_count: int
    duration_sec: float = 0.0
    api_calls_used: int = 0
    api_budget_max: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    partial_sync: bool = False
    auth_failure_code: Optional[str] = None
    message_safe: str = ""
