"""Normalized sold-comp and market-position schemas (read-only intelligence)."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MarketPricePosition(str, Enum):
    UNKNOWN = "unknown"
    BELOW_MARKET = "below_market"
    AT_MARKET = "at_market"
    ABOVE_MARKET = "above_market"


class SoldCompRecord(BaseModel):
    """Single comparable sold listing — internal canonical form (any adapter normalizes into this)."""

    sold_price: float = Field(ge=0)
    currency: str = "USD"
    sold_date: date
    #: Heuristic 0–1: how well this comp matches the subject listing (adapter-supplied).
    match_quality: float = Field(default=0.7, ge=0.0, le=1.0)
    condition_hint: Optional[str] = None
    title_hint: Optional[str] = None
    source_listing_id: Optional[str] = None

    model_config = {"extra": "ignore"}


class MarketCompSummary(BaseModel):
    """Aggregated sold-market view for one active listing (never persists secrets)."""

    median_sold_price: Optional[float] = None
    comp_count: int = 0
    recency_window_days: int = 90
    price_position: MarketPricePosition = MarketPricePosition.UNKNOWN
    comp_match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Percentage: positive = ask above median (e.g. 15.0 => 15% above).
    pct_vs_median: Optional[float] = None
    comps_data_source: str = "none"
