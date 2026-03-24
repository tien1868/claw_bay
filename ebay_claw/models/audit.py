"""Immutable audit event schema (append-only log)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Append-only record — do not mutate after persist."""

    event_id: str
    event_type: Literal[
        "policy_decision",
        "queue_item_created",
        "approval",
        "rejection",
        "execution_attempt",
        "execution_result",
        "compliance_check",
        "sync_started",
        "sync_completed",
        "sync_failed",
        "write_attempted",
        "write_blocked",
        "queue_superseded",
        "queue_stale_vs_live_detected",
        "queue_dry_run_acknowledged",
        "apply_requested",
        "apply_revalidated",
        "apply_blocked",
        "apply_simulated_success",
        "apply_simulated_failure",
    ]
    timestamp_utc: datetime
    actor: str = Field(description="system | operator id")
    listing_id: Optional[str] = None
    review_item_id: Optional[str] = None
    decision: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    snapshot_before: Dict[str, Any] = Field(default_factory=dict)
    snapshot_after: Dict[str, Any] = Field(default_factory=dict)
    policy_allowed: Optional[bool] = None
    policy_blocks: List[str] = Field(default_factory=list)
    policy_warnings: List[str] = Field(default_factory=list)
    redacted_meta: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}
