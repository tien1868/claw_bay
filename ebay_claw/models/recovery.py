"""Operator recovery / prioritization schemas — read-only recommendations only."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ebay_claw.models.domain import ProposedActionType, StrategicPath


class ConfidenceBand(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RelistProposal(BaseModel):
    """Proposal-only: end-and-relist review — no execution."""

    listing_id: str
    listing_title: str
    current_summary: str
    why_relist_recommended: str
    suggested_refreshed_title: str
    suggested_target_price: Optional[float] = None
    suggested_price_range_low: Optional[float] = None
    suggested_price_range_high: Optional[float] = None
    why_relist_vs_markdown_hold_bundle: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.6)
    strategy_context: StrategicPath = StrategicPath.END_AND_SELL_SIMILAR


class PriceToSellRecommendation(BaseModel):
    """Stale / overpriced listings — directional pricing guidance from comps (read-only)."""

    listing_id: str
    target_price: Optional[float] = None
    recommended_range_low: Optional[float] = None
    recommended_range_high: Optional[float] = None
    median_sold_price: Optional[float] = None
    comp_count: int = 0
    comp_match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_band: ConfidenceBand = ConfidenceBand.MEDIUM
    explanation: str
    caution_note: Optional[str] = None
    is_directional_only: bool = False


class BundleRecommendation(BaseModel):
    """Proposal-only lot/bundle grouping — no publish."""

    bundle_id: str
    listing_ids: List[str] = Field(min_length=2)
    suggested_lot_title: str
    target_lot_price_low: float = Field(ge=0.0)
    target_lot_price_high: float = Field(ge=0.0)
    rationale: str
    grouping_signals: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.55)


class DailyPriorityAction(BaseModel):
    """Single ranked operator action for the daily list."""

    rank: int = Field(default=0, ge=0, le=100, description="0 = unranked before final sort")
    listing_id: str
    title_snippet: str
    action_type: ProposedActionType
    reason: str
    expected_impact_summary: str
    score: float = Field(ge=0.0, le=100.0)
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    urgency: float = Field(ge=0.0, le=1.0)
    ease: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class VelocityWeekRollup(BaseModel):
    """One calendar-aligned week of operational events (when history exists)."""

    week_start: date
    week_end: date
    listings_created: int = 0
    sold_units: float = 0.0
    stale_inflow: int = 0
    stale_cleared: int = 0
    net_inventory_change: float = 0.0
    queue_approved: int = 0
    queue_rejected: int = 0
    #: event_based | partial | estimated_only
    data_quality: str = "estimated_only"


class VelocityMetrics(BaseModel):
    """Store-level velocity — event-based where history coverage exists, else documented estimates."""

    as_of: date
    computed_at: datetime
    listings_created_last_7d: int = 0
    listings_created_prior_7d: int = 0
    sold_units_estimated_last_7d: float = 0.0
    sold_units_event_last_7d: Optional[float] = None
    net_inventory_change_estimated_7d: float = 0.0
    net_inventory_change_last_7d: Optional[float] = None
    stale_inventory_count: int = 0
    stale_new_inflow_estimated_7d: int = 0
    stale_inflow_event_last_7d: Optional[int] = None
    stale_cleared_last_7d: Optional[int] = None
    stale_cleared_event_last_7d: Optional[int] = None
    stale_cleared_data_note: Optional[str] = None
    at_risk_90d_listings_count: int = 0
    intervention_needed_this_week_count: int = 0
    trend_notes: List[str] = Field(default_factory=list)
    #: Per-field: event | estimated | mixed
    metric_sources: Dict[str, str] = Field(default_factory=dict)
    historical_coverage_ok: bool = False
    weekly_trend_last_4: List[VelocityWeekRollup] = Field(default_factory=list)
    intervention_queue_approval_rate_30d: Optional[float] = None
    intervention_conversion_note: Optional[str] = None
