"""Read-only aggregates for live guarded title writes (audit-derived)."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Structured reason codes for expansion_advisory (API / dashboard; advisory only).
EXPANSION_REASON_INSUFFICIENT_LIVE_ATTEMPTS = "insufficient_live_attempts"
EXPANSION_REASON_SUCCESS_RATE_BELOW_CRITICAL = "success_rate_below_critical"
EXPANSION_REASON_SUCCESS_RATE_BELOW_ADVISORY_FLOOR = "success_rate_below_advisory_floor"
EXPANSION_REASON_FAILURES_EXCEED_PRIOR_WINDOW = "failures_exceed_prior_window"
EXPANSION_REASON_TRACKED_BUCKET_JUMP = "tracked_bucket_jump"
EXPANSION_REASON_READY_ADEQUATE_SIGNAL = "ready_adequate_signal"
EXPANSION_REASON_MIXED_SIGNALS_MIDDLE_BAND = "mixed_signals_middle_band"

ExecutorFailureReason = Literal[
    "unsupported_action",
    "identity_mismatch",
    "missing_identifier",
    "policy_failure",
    "auth_failure",
    "provider_failure",
    "retryable_transport",
    "title_drift",
    "idempotency_duplicate",
    "other",
]


class LiveWriteOperationsMetrics(BaseModel):
    """Rollups for UPDATE_TITLE / live eBay path (audit log)."""

    live_write_attempts: int = 0
    live_write_successes: int = 0
    live_write_failures: int = 0
    blocked_applies: int = 0
    idempotency_blocks: int = 0
    retryable_failures: int = 0
    non_retryable_failures: int = 0
    #: Executor outcomes only (apply_simulated_failure), live writes, UPDATE_TITLE.
    executor_failure_buckets: Dict[str, int] = Field(default_factory=dict)
    #: apply_blocked rows only, UPDATE_TITLE — keyed by blocker_category.
    blocked_apply_buckets: Dict[str, int] = Field(default_factory=dict)


class LiveTitleWriteHistoryRow(BaseModel):
    """One executor outcome for dashboard history (live guarded writes, non-simulated only)."""

    timestamp_utc: str
    event_type: str
    proposed_action_type: str = "update_title"
    listing_id: Optional[str] = None
    sku: Optional[str] = None
    success: bool
    retryable: bool = False
    user_safe_message: str = ""
    correlation_id: Optional[str] = None
    external_request_id: Optional[str] = None
    failure_reason: Optional[str] = None


TrendDirection = Literal["rising", "falling", "flat"]
ReadinessStatus = Literal["ready", "not_ready", "insufficient_data"]


class LiveWriteWindowBlock(BaseModel):
    """Metrics for a single UTC time window (UPDATE_TITLE live path only)."""

    period_start_utc: str
    period_end_utc: str
    window_days: int
    metrics: LiveWriteOperationsMetrics


class LiveWriteMetricDelta(BaseModel):
    """Current 7d vs the prior 7d for one headline counter."""

    metric: str
    current_7d: int
    previous_7d: int
    delta: int
    direction: TrendDirection


class LiveWriteBucketTrend(BaseModel):
    """Executor failure bucket: compare last 7d vs prior 7d."""

    bucket: str
    current_7d: int
    previous_7d: int
    delta: int
    direction: TrendDirection


class LiveWriteFailureMessageCount(BaseModel):
    """Top normalized failure messages (executor, live UPDATE_TITLE)."""

    message: str
    count: int


class LiveWriteOtherBucketDiagnostics(BaseModel):
    """Safe samples when failures land in the ``other`` category."""

    current_7d_count: int
    previous_7d_count: int
    sampled_normalized_messages: List[str] = Field(
        default_factory=list,
        description="Deduped, truncated operator-safe strings from reason_codes",
    )


class LiveWriteExpansionAdvisoryPolicy(BaseModel):
    """Configurable thresholds for the read-only expansion advisory (operators / dashboard)."""

    min_attempts_for_readiness: int = Field(
        default=3,
        ge=0,
        le=1_000_000,
        description="Rolling 7d live attempts must meet or exceed this to leave insufficient_data.",
    )
    min_attempts_for_rate_evaluation: int = Field(
        default=5,
        ge=0,
        le=1_000_000,
        description="Minimum 7d attempts before success-rate floors and ready path apply.",
    )
    success_rate_critical_below: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Below this success rate (with enough attempts) adds a not_ready signal.",
    )
    success_rate_advisory_floor: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Below this success rate (with enough attempts) adds a softer not_ready signal.",
    )
    success_rate_ready_floor: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="At or above this rate (with enough attempts) allows ready when no blockers.",
    )
    bucket_jump_min_current_7d: int = Field(
        default=2,
        ge=0,
        le=1_000_000,
        description="Tracked bucket: current 7d count must be at least this for a jump signal.",
    )
    bucket_jump_min_delta: int = Field(
        default=2,
        ge=0,
        le=1_000_000,
        description="Tracked bucket: rise vs prior 7d must be at least this for a jump signal.",
    )
    prior_window_min_attempts: int = Field(
        default=1,
        ge=0,
        le=1_000_000,
        description="Prior 7d must have at least this many attempts to compare failure counts.",
    )


class LiveWriteExpansionAdvisory(BaseModel):
    """Read-only heuristic — not a go-live gate."""

    readiness: ReadinessStatus
    summary: str
    reasons: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(
        default_factory=list,
        description="Stable machine-readable codes for why this readiness was chosen.",
    )
    primary_reason_code: Optional[str] = Field(
        default=None,
        description="Dominant driver when not_ready / insufficient_data (or ready signal code).",
    )


class LiveWriteTrendsSection(BaseModel):
    """7d/30d rollups, comparisons, and expansion readiness."""

    as_of: str
    last_7d: LiveWriteWindowBlock
    previous_7d: LiveWriteWindowBlock
    last_30d: LiveWriteWindowBlock
    seven_day_vs_prior: List[LiveWriteMetricDelta]
    failure_bucket_trends: List[LiveWriteBucketTrend]
    top_failure_messages_7d: List[LiveWriteFailureMessageCount]
    top_blocked_categories_7d: Dict[str, int] = Field(default_factory=dict)
    other_bucket: LiveWriteOtherBucketDiagnostics
    expansion_advisory: LiveWriteExpansionAdvisory


class LiveWriteOperationsSnapshot(BaseModel):
    """Metrics + recent history for operators (read-only)."""

    metrics: LiveWriteOperationsMetrics
    recent_live_title_writes: List[LiveTitleWriteHistoryRow] = Field(default_factory=list)
    trends: Optional[LiveWriteTrendsSection] = None
    note: str = ""
    expansion_advisory_policy: LiveWriteExpansionAdvisoryPolicy = Field(
        default_factory=LiveWriteExpansionAdvisoryPolicy,
        description="Active advisory thresholds from settings (read-only policy aid).",
    )
