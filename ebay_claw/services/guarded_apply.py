"""
Server-side guarded apply pipeline — audited, revalidates live identity + policy before executor.

``GuardedApplyService`` is the only supported outer apply path; executors (mock or future eBay)
implement ``ListingWriteExecutor``. Production eBay writes remain off unless explicitly configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from ebay_claw.audit.store import AuditLogStore, new_event_id
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.execution.idempotency import ApplyIdempotencyStore, build_apply_idempotency_key
from ebay_claw.execution.ebay_write_executor import EbayWriteExecutor
from ebay_claw.execution.mock_executor import MockExecutor
from ebay_claw.execution.protocol import ListingWriteExecutor
from ebay_claw.logging_config import get_logger
from ebay_claw.models.audit import AuditEvent
from ebay_claw.models.domain import ApplyResult, ListingRecord, ReviewQueueItem, ReviewStatus
from ebay_claw.policies.safety import PolicyEngine
from ebay_claw.review_queue.apply_guard import (
    apply_executor_ready,
    executor_gate_blockers_before_policy,
)
from ebay_claw.review_queue.store import ReviewQueueStore
from ebay_claw.services.live_identity import collect_live_identity_blockers

logger = get_logger(__name__)

LiveListingResolver = Callable[[str], Optional[ListingRecord]]


@dataclass
class GuardedApplyResult:
    """Outcome of apply_approved_item — operator-safe messages only in blockers."""

    ok: bool
    blocked_stage: Optional[str] = None
    state_machine_blockers: List[str] = field(default_factory=list)
    identity_blockers: List[str] = field(default_factory=list)
    idempotency_blockers: List[str] = field(default_factory=list)
    policy_blockers: List[str] = field(default_factory=list)
    execution: Optional[ApplyResult] = None


class GuardedApplyService:
    """
    Single entry for guarded apply: reload queue row, gates, live identity, idempotency, policy,
    then executor (no legacy executor audit / no executor-owned queue transitions).
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        queue: ReviewQueueStore,
        resolve_live_listing: LiveListingResolver,
        executor: Optional[ListingWriteExecutor] = None,
        policy: Optional[PolicyEngine] = None,
        idempotency_store: Optional[ApplyIdempotencyStore] = None,
    ):
        self._s = settings or get_settings()
        self._queue = queue
        self._resolve_live = resolve_live_listing
        self._executor = executor or MockExecutor(settings=self._s, queue=None)
        self._policy = policy or PolicyEngine(settings=self._s)
        self._audit = AuditLogStore(settings=self._s)
        self._idem = idempotency_store or ApplyIdempotencyStore.from_settings(self._s)

    def simulate_apply(self, review_item_id: str, *, actor: str) -> GuardedApplyResult:
        actor_s = (actor or "").strip()
        if not actor_s:
            self._emit(
                "apply_blocked",
                actor="unknown",
                review_item_id=review_item_id,
                listing_id=None,
                decision="blocked",
                reason_codes=["actor_required"],
                meta={"blocker_category": "invalid_actor", "stage": "precheck"},
            )
            return GuardedApplyResult(ok=False, blocked_stage="invalid_actor")

        self._emit(
            "apply_requested",
            actor=actor_s,
            review_item_id=review_item_id,
            listing_id=None,
            decision="requested",
            reason_codes=["guarded_apply_simulation"],
            meta={"stage": "requested"},
            snap_before={},
            snap_after={},
        )

        item = self._queue.get(review_item_id)
        if item is None:
            self._blocked(actor_s, None, "state_machine", ["unknown_queue_item"])
            return GuardedApplyResult(ok=False, blocked_stage="not_found", state_machine_blockers=["unknown_queue_item"])

        if item.status != ReviewStatus.APPROVED:
            self._blocked(
                actor_s,
                item,
                "state_machine",
                [f"queue_status_must_be_approved got={item.status.value}"],
            )
            return GuardedApplyResult(
                ok=False,
                blocked_stage="state_machine",
                state_machine_blockers=[f"queue_status_must_be_approved got={item.status.value}"],
            )

        sm_pre = executor_gate_blockers_before_policy(self._s, item)
        if sm_pre:
            self._blocked(actor_s, item, "state_machine", sm_pre)
            return GuardedApplyResult(
                ok=False,
                blocked_stage="state_machine",
                state_machine_blockers=list(sm_pre),
            )

        live = self._resolve_live(item.listing_id)
        if live is None:
            reasons = ["live_listing_not_found"]
            self._blocked(actor_s, item, "identity", reasons)
            return GuardedApplyResult(ok=False, blocked_stage="identity", identity_blockers=reasons)

        id_blockers = collect_live_identity_blockers(
            item,
            live,
            require_enqueue_fingerprint=self._s.apply_require_enqueue_fingerprint,
            strict_live_identity=self._s.apply_strict_live_identity,
        )
        if id_blockers:
            self._blocked(actor_s, item, "identity", id_blockers)
            return GuardedApplyResult(ok=False, blocked_stage="identity", identity_blockers=id_blockers)

        idem_key = build_apply_idempotency_key(item)
        if self._idem.has_successful_apply(idem_key):
            reasons = [
                "duplicate_apply_idempotency: this approval was already applied successfully "
                "(same queue item revision)."
            ]
            self._blocked(actor_s, item, "idempotency", reasons)
            return GuardedApplyResult(
                ok=False,
                blocked_stage="idempotency",
                idempotency_blockers=reasons,
            )

        self._emit(
            "apply_revalidated",
            actor=actor_s,
            review_item_id=item.id,
            listing_id=item.listing_id,
            decision="revalidated",
            reason_codes=["live_identity_ok"],
            meta={
                "listing_id": live.listing_id,
                "sku": live.sku,
                "fingerprint_expected": (item.listing_snapshot_fingerprint or "")[:20],
                "apply_idempotency_key_prefix": idem_key[:16],
            },
            snap_before=item.current_state_snapshot,
            snap_after=item.before_after_diff,
            item=item,
        )

        pol = self._policy.must_pass_before_write(item, live)
        if not pol.allowed:
            self._blocked(actor_s, item, "policy", list(pol.blocked_reasons))
            return GuardedApplyResult(
                ok=False,
                blocked_stage="policy",
                policy_blockers=list(pol.blocked_reasons),
            )

        if not apply_executor_ready(self._s, item, policy_snapshot_verified=True):
            fallbacks = [
                b
                for b in executor_gate_blockers_before_policy(self._s, item)
                if "policy" in b.lower()
            ]
            reasons = fallbacks or ["apply_executor_not_ready_after_policy"]
            self._blocked(actor_s, item, "state_machine", reasons)
            return GuardedApplyResult(ok=False, blocked_stage="state_machine", state_machine_blockers=reasons)

        listing_payload = live.model_dump(mode="python")
        result = self._executor.apply(
            item,
            listing_snapshot=listing_payload,
            idempotency_key=idem_key,
            legacy_audit=False,
            transition_queue=False,
        )

        if result.success:
            self._queue.transition(item.id, ReviewStatus.APPLIED, actor=actor_s)
            self._idem.record_success(
                idempotency_key=idem_key,
                review_item_id=item.id,
                listing_id=item.listing_id,
                extra={
                    "correlation_id": result.correlation_id,
                    "simulated": result.simulated,
                },
            )
            self._emit(
                "apply_simulated_success",
                actor=actor_s,
                review_item_id=item.id,
                listing_id=item.listing_id,
                decision="simulated_success",
                reason_codes=["executor_ok"],
                meta={
                    "executor_message": result.user_safe_message,
                    "idempotency_key": idem_key,
                    "correlation_id": result.correlation_id,
                    "external_request_id": result.external_request_id,
                    "simulated": result.simulated,
                    "live_write": not result.simulated,
                    "target_sku": result.target_sku,
                    "target_offer_id": result.target_offer_id,
                    "changed_specific_keys": list(result.changed_specific_keys or []),
                },
                snap_before=item.current_state_snapshot,
                snap_after=item.before_after_diff,
                item=item,
            )
            logger.info("Guarded apply simulated success item=%s listing=%s", item.id, item.listing_id)
            return GuardedApplyResult(ok=True, execution=result)

        self._emit(
            "apply_simulated_failure",
            actor=actor_s,
            review_item_id=item.id,
            listing_id=item.listing_id,
            decision="simulated_failure",
            reason_codes=[result.user_safe_message[:220]],
            meta={
                **(result.adapter_detail or {}),
                "idempotency_key": idem_key,
                "correlation_id": result.correlation_id,
                "external_request_id": result.external_request_id,
                "retryable": result.retryable,
                "simulated": result.simulated,
                "live_write": not result.simulated,
            },
            snap_before=item.current_state_snapshot,
            snap_after=item.before_after_diff,
            item=item,
        )
        try:
            self._queue.transition(item.id, ReviewStatus.FAILED, actor=actor_s)
        except Exception as ex:
            logger.warning("FAILED transition after simulated failure: %s", ex)
        return GuardedApplyResult(ok=False, blocked_stage="executor", execution=result)

    def apply_approved_item(self, review_item_id: str, *, actor: str) -> GuardedApplyResult:
        """Canonical name for authenticated server/API callers — same pipeline as ``simulate_apply``."""
        return self.simulate_apply(review_item_id, actor=actor)

    def _blocked(
        self,
        actor: str,
        item: Optional[ReviewQueueItem],
        category: str,
        reasons: List[str],
    ) -> None:
        self._emit(
            "apply_blocked",
            actor=actor,
            review_item_id=item.id if item else None,
            listing_id=item.listing_id if item else None,
            decision="blocked",
            reason_codes=reasons[:24],
            meta={"blocker_category": category, "stage": "blocked"},
            snap_before=item.current_state_snapshot if item else {},
            snap_after=item.before_after_diff if item else {},
            item=item,
        )

    def _emit(
        self,
        event_type: str,
        *,
        actor: str,
        review_item_id: Optional[str],
        listing_id: Optional[str],
        decision: str,
        reason_codes: List[str],
        meta: dict,
        snap_before: Optional[dict] = None,
        snap_after: Optional[dict] = None,
        item: Optional[ReviewQueueItem] = None,
    ) -> None:
        merged_meta = dict(meta)
        if item is not None:
            merged_meta.setdefault("proposed_action_type", item.proposed_action_type.value)
        self._audit.append(
            AuditEvent(
                event_id=new_event_id(),
                event_type=event_type,  # type: ignore[assignment]
                timestamp_utc=datetime.now(timezone.utc),
                actor=actor,
                listing_id=listing_id,
                review_item_id=review_item_id,
                decision=decision,
                reason_codes=reason_codes,
                snapshot_before=dict(snap_before or {}),
                snapshot_after=dict(snap_after or {}),
                redacted_meta={**merged_meta, "runtime_mode": self._s.runtime_mode.value},
            )
        )


def build_guarded_apply_for_orchestrator(
    *,
    settings: Settings,
    queue: ReviewQueueStore,
    load_listings: Callable[[], List[ListingRecord]],
    executor: Optional[ListingWriteExecutor] = None,
) -> GuardedApplyService:
    """Resolve live rows from the same ingest snapshot the operator sees."""

    def _resolve(listing_id: str) -> Optional[ListingRecord]:
        for lst in load_listings():
            if lst.listing_id == listing_id:
                return lst
        return None

    ex = executor
    if ex is None:
        if settings.ebay_real_writes_enabled and settings.apply_api_allow_live_executor:
            ex = EbayWriteExecutor(settings=settings)
        else:
            ex = MockExecutor(settings=settings, queue=None)

    return GuardedApplyService(
        settings=settings,
        queue=queue,
        resolve_live_listing=_resolve,
        executor=ex,
    )
