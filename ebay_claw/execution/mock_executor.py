"""Execution — blocked by default; gated guarded-write path only (server canonical mode)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from ebay_claw.audit.store import AuditLogStore, new_event_id
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.audit import AuditEvent
from ebay_claw.models.domain import ApplyResult, ReviewQueueItem, ReviewStatus
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.policies.safety import PolicyEngine
from ebay_claw.review_queue.apply_guard import ApplyPreconditionError, assert_apply_state_machine_satisfied
from ebay_claw.security.write_guard import WriteForbiddenError, assert_write_mutation_allowed

if TYPE_CHECKING:
    from ebay_claw.review_queue.store import ReviewQueueStore

logger = get_logger(__name__)


class MockExecutor:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        queue: Optional["ReviewQueueStore"] = None,
    ):
        self._s = settings or get_settings()
        self._queue = queue
        self._audit = AuditLogStore(settings=self._s)
        self._policy = PolicyEngine(settings=self._s)

    def apply(
        self,
        item: ReviewQueueItem,
        listing_snapshot: Optional[dict] = None,
        *,
        idempotency_key: str = "",
        legacy_audit: bool = True,
        transition_queue: bool = False,
    ) -> ApplyResult:
        """
        Simulated / gated apply. When ``legacy_audit`` is False (GuardedApplyService path),
        this does not emit write_attempted/write_blocked — the service owns apply_* audit events.
        When ``transition_queue`` is True, moves APPROVED → APPLIED on success (deprecated;
        GuardedApplyService transitions explicitly).
        """
        actor = item.approved_by or self._s.default_actor
        snap_before_item = item.current_state_snapshot
        snap_after_item = dict(item.before_after_diff or {})

        def fail(msg: str, detail: Optional[dict] = None) -> ApplyResult:
            d = detail or {}
            if legacy_audit:
                self._audit.append(
                    AuditEvent(
                        event_id=new_event_id(),
                        event_type="write_blocked",
                        timestamp_utc=datetime.now(timezone.utc),
                        actor=actor,
                        listing_id=item.listing_id,
                        review_item_id=item.id,
                        decision="blocked",
                        reason_codes=[msg[:220]],
                        snapshot_before=snap_before_item,
                        snapshot_after=snap_after_item,
                        redacted_meta={**d, "runtime_mode": self._s.runtime_mode.value},
                    )
                )
            logger.info("Execution blocked listing=%s %s", item.listing_id, msg)
            cid = new_event_id()
            return ApplyResult(
                listing_id=item.listing_id,
                attempted_action=item.proposed_action_type,
                success=False,
                user_safe_message=msg,
                idempotency_key=idempotency_key,
                correlation_id=cid,
                retryable=False,
                simulated=True,
                adapter_detail=d,
                changed_specific_keys=[],
            )

        if legacy_audit:
            self._audit.append(
                AuditEvent(
                    event_id=new_event_id(),
                    event_type="write_attempted",
                    timestamp_utc=datetime.now(timezone.utc),
                    actor=actor,
                    listing_id=item.listing_id,
                    review_item_id=item.id,
                    decision="attempted",
                    reason_codes=["apply_requested"],
                    snapshot_before=snap_before_item,
                    snapshot_after=snap_after_item,
                    redacted_meta={"runtime_mode": self._s.runtime_mode.value},
                )
            )

        try:
            assert_write_mutation_allowed(self._s, caller="MockExecutor.apply")
        except WriteForbiddenError as e:
            return fail(str(e), {"write_guard": True})

        if not self._s.execution_enabled:
            return fail("Execution disabled (EBAY_CLAW_EXECUTION_ENABLED=false)")

        if self._s.runtime_mode == ClawRuntimeMode.LIVE_GUARDED_WRITE:
            if not self._s.guarded_write_enabled:
                return fail("live_guarded_write requires GUARDED_WRITE_ENABLED=true")

        try:
            assert_apply_state_machine_satisfied(self._s, item)
        except ApplyPreconditionError as e:
            return fail(str(e), {"apply_precondition": True})

        if item.policy_flags:
            return fail("policy_flags on item — cannot apply")

        if self._s.require_policy_pass_for_write:
            from ebay_claw.models.domain import ListingRecord

            listing = (
                ListingRecord.model_validate(listing_snapshot)
                if listing_snapshot
                else None
            )
            pol = self._policy.must_pass_before_write(item, listing)
            if not pol.allowed:
                return fail(
                    "policy_blocked_pre_write",
                    {"blocks": pol.blocked_reasons},
                )

        detail: Dict[str, Any] = {
            "mock": True,
            "applied": True,
            "runtime_mode": self._s.runtime_mode.value,
        }
        if legacy_audit and self._s.require_audit_on_apply:
            self._audit.append(
                AuditEvent(
                    event_id=new_event_id(),
                    event_type="execution_result",
                    timestamp_utc=datetime.now(timezone.utc),
                    actor=actor,
                    listing_id=item.listing_id,
                    review_item_id=item.id,
                    decision="success",
                    reason_codes=["mock_apply_ok"],
                    snapshot_before=snap_before_item,
                    snapshot_after=snap_after_item,
                    redacted_meta=detail,
                )
            )
        if (
            transition_queue
            and self._queue is not None
            and self._queue.get(item.id) is not None
        ):
            op = item.approved_by or self._s.default_actor
            self._queue.transition(item.id, ReviewStatus.APPLIED, actor=op)
        logger.info(
            "Mock apply %s for listing %s",
            item.proposed_action_type.value,
            item.listing_id,
        )
        cid = new_event_id()
        tsku: Optional[str] = None
        toffer: Optional[str] = None
        if listing_snapshot:
            raw_sku = listing_snapshot.get("sku")
            if raw_sku is not None and str(raw_sku).strip():
                tsku = str(raw_sku).strip()
            ex = listing_snapshot.get("extra")
            if isinstance(ex, dict):
                oid = ex.get("ebay_offer_id")
                if oid is not None and str(oid).strip():
                    toffer = str(oid).strip()
        return ApplyResult(
            listing_id=item.listing_id,
            attempted_action=item.proposed_action_type,
            success=True,
            user_safe_message="Mock execution succeeded",
            idempotency_key=idempotency_key,
            target_sku=tsku,
            target_offer_id=toffer,
            correlation_id=cid,
            retryable=False,
            simulated=True,
            adapter_detail=detail,
            changed_specific_keys=[],
        )
