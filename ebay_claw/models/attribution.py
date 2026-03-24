"""Outcome attribution — links operational events to later outcomes (read-only analytics)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

AttributionWindowDays = Literal[7, 30, 90]

PriorActionKind = Literal["queue_approved", "relist_proposed", "bundle_proposed"]

OutcomeKind = Literal["sale", "stale_cleared"]


class ActionOutcomeLink(BaseModel):
    """One outcome (sale or stale exit) attributed to a single prior action under a time window."""

    listing_id: str
    outcome_kind: OutcomeKind
    outcome_event_id: str
    outcome_at_utc: datetime
    attribution_window_days: int
    primary_event_id: str
    primary_kind: PriorActionKind
    primary_at_utc: datetime
    primary_proposed_action_type: Optional[str] = None
    primary_review_item_id: Optional[str] = None
    days_from_primary_to_outcome: float = 0.0
    ambiguous: bool = False
    ambiguity_note: Optional[str] = None


EvidenceTier = Literal["strong", "moderate", "weak", "insufficient"]


class ActionEffectivenessSummary(BaseModel):
    """Rollup for dashboard: how often attributed outcomes follow a class of actions."""

    action_key: str
    label: str
    cohort_actions_count: int
    attributed_sales: int
    attributed_stale_cleared: int
    #: Raw observed rates (no shrinkage).
    sale_rate: Optional[float] = None
    stale_clear_rate: Optional[float] = None
    #: Beta-style shrinkage toward priors (cohort-size aware).
    shrunk_sale_rate: Optional[float] = None
    shrunk_stale_clear_rate: Optional[float] = None
    evidence_tier: EvidenceTier = "insufficient"
    #: How strongly attributed outcomes may move rankings [0, 1].
    attribution_lift_weight: float = 0.0
    #: All-time (full cohort lookback) evidence-weighted lift [0, 1].
    attribution_lift_score: float = 0.42
    #: Recent-window lift: recency-weighted attributed outcomes + recent cohort only.
    recent_attribution_lift_score: float = 0.42
    recent_evidence_tier: EvidenceTier = "insufficient"
    recent_cohort_actions_count: int = 0
    #: Blended lift for ranking — prefers recent when evidence is strong enough.
    ranking_attribution_lift_score: float = 0.42
    #: Pre-shrinkage outcome mix (all-time); before tier weighting toward neutral.
    raw_outcome_signal: float = 0.42
    #: Recent-window raw signal (after shrinkage on weighted counts).
    recent_raw_outcome_signal: float = 0.42
    observation_note: str = ""


class ActionEffectivenessDashboard(BaseModel):
    """Four headline summaries plus window metadata."""

    as_of: str
    attribution_window_days: int
    observation_cutoff_days: int
    #: Cohort window for "recent" metrics (approvals/proposals + outcomes).
    recent_cohort_days: int = 90
    #: Exponential decay half-life on outcome age (days) for weighted attributed mass.
    recency_half_life_days: float = 45.0
    summaries: List[ActionEffectivenessSummary] = Field(default_factory=list)
    unattributed_sales_in_window: int = 0
    unattributed_stale_cleared_in_window: int = 0
