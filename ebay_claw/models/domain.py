"""Normalized domain models — independent of eBay API wire format."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ebay_claw.models.compliance_issue import ComplianceIssueRecord
from ebay_claw.models.comps import MarketCompSummary


class StrategicPath(str, Enum):
    FAST_MOVE = "FAST_MOVE"
    OPTIMIZE_AND_HOLD = "OPTIMIZE_AND_HOLD"
    PREMIUM_PATIENCE = "PREMIUM_PATIENCE"
    AGING_RISK = "AGING_RISK"
    REPRICE_NOW = "REPRICE_NOW"
    REPACKAGE = "REPACKAGE"
    END_AND_SELL_SIMILAR = "END_AND_SELL_SIMILAR"


class AgeBucket(str, Enum):
    D0_29 = "0-29"
    D30_59 = "30-59"
    D60_74 = "60-74"
    D75_89 = "75-89"
    D90_119 = "90-119"
    D120_179 = "120-179"
    D180_PLUS = "180+"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class PricingAction(str, Enum):
    HOLD = "HOLD"
    REVIEW = "REVIEW"
    MARKDOWN_10 = "MARKDOWN_10"
    MARKDOWN_20 = "MARKDOWN_20"
    MARKDOWN_30 = "MARKDOWN_30"
    IMPROVE_TITLE = "IMPROVE_TITLE"
    FILL_SPECIFICS = "FILL_SPECIFICS"
    SEND_OFFER = "SEND_OFFER"
    END_AND_SELL_SIMILAR = "END_AND_SELL_SIMILAR"
    BUNDLE_CANDIDATE = "BUNDLE_CANDIDATE"


class ProposedActionType(str, Enum):
    UPDATE_TITLE = "update_title"
    #: Legacy / broad specifics proposals (not live-executed; policy-only shape).
    UPDATE_ITEM_SPECIFICS = "update_item_specifics"
    #: Single live-safe path: whitelisted low-risk aspects only (Inventory API PUT).
    UPDATE_SAFE_SPECIFICS = "update_safe_specifics"
    MARKDOWN_LISTING = "markdown_listing"
    SEND_OFFER = "send_offer"
    PREPARE_RELIST = "prepare_relist"
    END_AND_SELL_SIMILAR = "end_and_sell_similar"
    #: Proposal-only — operator reviews end/relist; no automated relist execution.
    RELIST_CANDIDATE = "relist_candidate"
    #: Proposal-only — multi-SKU lot; no bundle publish.
    BUNDLE_LOT_CANDIDATE = "bundle_lot_candidate"


class ListingRecord(BaseModel):
    """Canonical listing — adapter output target."""

    listing_id: str
    title: str
    sku: Optional[str] = None
    category_id: Optional[str] = None
    price_amount: float = Field(ge=0)
    currency: str = "USD"
    quantity: int = Field(default=1, ge=0)
    listed_at: Optional[datetime] = None
    listed_on: Optional[date] = None
    watchers: Optional[int] = Field(default=None, ge=0)
    view_count: Optional[int] = Field(default=None, ge=0)
    sold_quantity_last_90_days: Optional[int] = Field(default=None, ge=0)
    brand: Optional[str] = None
    size: Optional[str] = None
    department: Optional[str] = None
    garment_type: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    condition: Optional[str] = None
    description: Optional[str] = None
    item_specifics: Dict[str, str] = Field(default_factory=dict)
    source_payload_ref: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class ListingAnalysis(BaseModel):
    listing_id: str
    days_active: int = Field(ge=0)
    age_bucket: AgeBucket
    is_stale: bool
    stale_reasons: List[str] = Field(default_factory=list)
    missing_critical_fields: List[str] = Field(default_factory=list)
    weak_title_signals: List[str] = Field(default_factory=list)
    weak_description: bool = False
    price_outlier_note: Optional[str] = None
    dead_stock_likely: bool = False
    on_track_90_day_sale: bool
    highest_leverage_action: str
    group_keys: Dict[str, str] = Field(
        default_factory=dict,
        description="brand, garment_type, size, age_bucket labels",
    )
    #: Sold-market context from read-only comps adapters (optional).
    market: Optional[MarketCompSummary] = None


class ListingStrategyScore(BaseModel):
    listing_id: str
    days_active: int
    age_bucket: AgeBucket
    stale_risk_score: float = Field(ge=0.0, le=1.0)
    profit_protection_score: float = Field(ge=0.0, le=1.0)
    optimization_needed_score: float = Field(ge=0.0, le=1.0)
    sale_likelihood_before_90_days: float = Field(ge=0.0, le=1.0)
    recommended_strategy: StrategicPath
    rationale: str
    #: Strategy from age/listing/engagement rules before sold-comp overlay.
    baseline_strategy: StrategicPath
    strategy_changed_by_market: bool = False
    market_adjustment_note: Optional[str] = None
    comp_count: int = 0
    comp_match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    median_sold_price: Optional[float] = None
    price_position_vs_market: str = "unknown"
    comps_recency_window_days: int = 0


class TitleSuggestion(BaseModel):
    listing_id: str
    original_title: str
    suggested_title: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: List[str] = Field(default_factory=list)
    deterministic: bool = False


class SpecificsFieldOp(str, Enum):
    PROPOSE_ADD = "propose_add"
    PROPOSE_CORRECT = "propose_correct"
    SKIP_LOW_CONFIDENCE = "skip_low_confidence"


class SpecificsFieldSuggestion(BaseModel):
    name: str
    current_value: Optional[str] = None
    proposed_value: Optional[str] = None
    operation: SpecificsFieldOp
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: str


class SpecificsSuggestion(BaseModel):
    listing_id: str
    existing_specifics: Dict[str, str] = Field(default_factory=dict)
    proposed_additions: List[SpecificsFieldSuggestion] = Field(default_factory=list)
    proposed_corrections: List[SpecificsFieldSuggestion] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    warnings: List[str] = Field(default_factory=list)


class PricingRecommendation(BaseModel):
    listing_id: str
    recommended_action: PricingAction
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    factors_used: List[str] = Field(default_factory=list)
    expected_effect_on_90_day_sell_through: str
    profit_protection_note: str
    #: High-level pricing narrative using comps when available.
    pricing_segment: Optional[str] = None


class ReviewQueueItem(BaseModel):
    id: str
    listing_id: str
    listing_title: str
    current_state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    proposed_action_type: ProposedActionType
    recommended_strategy: StrategicPath
    before_after_diff: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    expected_impact_90d: str
    created_at: datetime
    status: ReviewStatus = ReviewStatus.PENDING
    policy_flags: List[str] = Field(default_factory=list)
    policy_warnings: List[str] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    superseded_by: Optional[str] = None
    listing_snapshot_fingerprint: str = ""
    is_stale_vs_live: bool = Field(
        default=False,
        description="True when live listing fingerprint differs from snapshot at enqueue time",
    )
    compliance_warnings: List[str] = Field(
        default_factory=list,
        description="Deprecated flat strings; prefer compliance_issues.",
    )
    compliance_issues: List[ComplianceIssueRecord] = Field(default_factory=list)
    compliance_checked_at: Optional[datetime] = None
    """Operator must acknowledge dry-run / diff before apply in guarded production mode."""
    dry_run_acknowledged: bool = False
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    stale_detected_at: Optional[datetime] = None


class ApplyResult(BaseModel):
    """Normalized outcome for all listing write executors (mock or future eBay)."""

    listing_id: str
    attempted_action: ProposedActionType
    success: bool
    user_safe_message: str
    idempotency_key: str = ""
    target_sku: Optional[str] = None
    target_offer_id: Optional[str] = None
    external_request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    retryable: bool = False
    simulated: bool = True
    adapter_detail: Optional[Dict[str, Any]] = None
    #: Populated on successful safe-specifics live writes (canonical aspect names).
    changed_specific_keys: List[str] = Field(default_factory=list)

    @property
    def message(self) -> str:
        """Compatibility with legacy ExecutionResult.message."""
        return self.user_safe_message


class ExecutionResult(BaseModel):
    """Legacy shape — prefer ApplyResult for new code."""

    listing_id: str
    action: ProposedActionType
    success: bool
    message: str
    adapter_detail: Optional[Dict[str, Any]] = None

    def to_apply_result(
        self,
        *,
        idempotency_key: str = "",
        target_sku: Optional[str] = None,
        target_offer_id: Optional[str] = None,
        simulated: bool = True,
        retryable: bool = False,
        external_request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        changed_specific_keys: Optional[List[str]] = None,
    ) -> "ApplyResult":
        return ApplyResult(
            listing_id=self.listing_id,
            attempted_action=self.action,
            success=self.success,
            user_safe_message=self.message,
            idempotency_key=idempotency_key,
            target_sku=target_sku,
            target_offer_id=target_offer_id,
            external_request_id=external_request_id,
            correlation_id=correlation_id,
            retryable=retryable,
            simulated=simulated,
            adapter_detail=self.adapter_detail,
            changed_specific_keys=list(changed_specific_keys or []),
        )


class BrandCount(BaseModel):
    brand: str
    count: int


class StoreMetrics(BaseModel):
    computed_at: datetime
    inventory_count: int
    age_distribution: Dict[str, int] = Field(default_factory=dict)
    stale_inventory_count: int
    average_listing_age_days: float
    listings_missing_critical_specifics: int
    listings_weak_titles: int
    pct_likely_sell_within_90_days: float
    pct_at_risk_past_90_days: float
    sell_through_rate: Optional[float] = None
    top_brands_by_count: List[BrandCount] = Field(default_factory=list)
    worst_performing_buckets: List[str] = Field(default_factory=list)
    intervention_needed_this_week_count: int
