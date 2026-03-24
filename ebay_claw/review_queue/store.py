"""Persistent review queue — JSON file for MVP auditability."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ebay_claw.audit.store import AuditLogStore, new_event_id
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.audit import AuditEvent
from ebay_claw.models.domain import ListingRecord, ProposedActionType, ReviewQueueItem, ReviewStatus, StrategicPath
from ebay_claw.review_queue.fingerprint import listing_snapshot_fingerprint
from ebay_claw.review_queue.state_machine import (
    QueueTransitionError,
    build_transition_update,
    utc_now,
)
from ebay_claw.services.operational_history_store import OperationalHistoryStore

logger = get_logger(__name__)


class ReviewQueueStore:
    def __init__(self, path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = path or self._s.review_queue_path
        self._audit = AuditLogStore(settings=self._s)
        self._items: Dict[str, ReviewQueueItem] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for row in raw.get("items", []):
                item = ReviewQueueItem.model_validate(row)
                self._items[item.id] = item
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Review queue load failed: %s", e)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": [i.model_dump(mode="json") for i in self._items.values()]}
        self._path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def add(self, item: ReviewQueueItem) -> ReviewQueueItem:
        self._items[item.id] = item
        self._save()
        return item

    def create(
        self,
        listing_id: str,
        listing_title: str,
        snapshot: dict,
        proposed: ProposedActionType,
        strategy: StrategicPath,
        diff: dict,
        confidence: float,
        rationale: str,
        impact_90: str,
        policy_flags: Optional[List[str]] = None,
        policy_warnings: Optional[List[str]] = None,
    ) -> ReviewQueueItem:
        fp = listing_snapshot_fingerprint(snapshot)
        return self.create_deduped(
            listing_id=listing_id,
            listing_title=listing_title,
            snapshot=snapshot,
            proposed=proposed,
            strategy=strategy,
            diff=diff,
            confidence=confidence,
            rationale=rationale,
            impact_90=impact_90,
            listing_snapshot_fingerprint=fp,
            policy_flags=policy_flags,
            policy_warnings=policy_warnings,
        )

    def create_deduped(
        self,
        listing_id: str,
        listing_title: str,
        snapshot: dict,
        proposed: ProposedActionType,
        strategy: StrategicPath,
        diff: dict,
        confidence: float,
        rationale: str,
        impact_90: str,
        listing_snapshot_fingerprint: str,
        policy_flags: Optional[List[str]] = None,
        policy_warnings: Optional[List[str]] = None,
    ) -> ReviewQueueItem:
        pending_same = [
            i
            for i in self._items.values()
            if i.listing_id == listing_id
            and i.proposed_action_type == proposed
            and i.status == ReviewStatus.PENDING
        ]
        new_id = str(uuid.uuid4())
        max_v = max((i.version for i in pending_same), default=0)
        for old in pending_same:
            logger.info(
                "Superseding pending queue item id=%s listing=%s action=%s → %s",
                old.id,
                listing_id,
                proposed.value,
                new_id,
            )
            self.add(
                old.model_copy(
                    update={
                        "status": ReviewStatus.SUPERSEDED,
                        "superseded_by": new_id,
                    }
                )
            )
            self._audit.append(
                AuditEvent(
                    event_id=new_event_id(),
                    event_type="queue_superseded",
                    timestamp_utc=datetime.now(timezone.utc),
                    actor=self._s.default_actor,
                    listing_id=listing_id,
                    review_item_id=old.id,
                    decision="superseded",
                    reason_codes=["pending_replaced_by_newer_enqueue"],
                    redacted_meta={
                        "superseded_by": new_id,
                        "proposed_action": proposed.value,
                        "runtime_mode": self._s.runtime_mode.value,
                    },
                )
            )

        item = ReviewQueueItem(
            id=new_id,
            listing_id=listing_id,
            listing_title=listing_title,
            current_state_snapshot=snapshot,
            proposed_action_type=proposed,
            recommended_strategy=strategy,
            before_after_diff=diff,
            confidence=confidence,
            rationale=rationale,
            expected_impact_90d=impact_90,
            created_at=datetime.now(timezone.utc),
            status=ReviewStatus.PENDING,
            policy_flags=policy_flags or [],
            policy_warnings=policy_warnings or [],
            version=max_v + 1,
            superseded_by=None,
            listing_snapshot_fingerprint=listing_snapshot_fingerprint,
            is_stale_vs_live=False,
        )
        return self.add(item)

    def list_all(self) -> List[ReviewQueueItem]:
        return sorted(self._items.values(), key=lambda x: x.created_at, reverse=True)

    def get(self, item_id: str) -> Optional[ReviewQueueItem]:
        return self._items.get(item_id)

    def transition(
        self,
        item_id: str,
        target: ReviewStatus,
        *,
        actor: str,
        dry_run_acknowledged: Optional[bool] = None,
    ) -> Optional[ReviewQueueItem]:
        """
        Enforced state machine for operator-driven transitions.
        Use instead of mutating status directly.
        """
        item = self._items.get(item_id)
        if not item:
            return None
        now = utc_now()
        patch = build_transition_update(
            item,
            target,
            now=now,
            actor=actor,
            dry_run_acknowledged=dry_run_acknowledged,
            settings=self._s,
        )
        updated = item.model_copy(update=patch)
        self._items[item_id] = updated
        self._save()
        if target == ReviewStatus.APPROVED:
            self._audit.append(
                AuditEvent(
                    event_id=new_event_id(),
                    event_type="approval",
                    timestamp_utc=now,
                    actor=actor,
                    listing_id=item.listing_id,
                    review_item_id=item_id,
                    decision="approved",
                    reason_codes=["queue_transition"],
                    redacted_meta={"runtime_mode": self._s.runtime_mode.value},
                )
            )
            OperationalHistoryStore(settings=self._s).append_event(
                "queue_approved",
                source="queue",
                listing_id=updated.listing_id,
                review_item_id=item_id,
                actor=actor,
                payload={"proposed_action_type": updated.proposed_action_type.value},
            )
        elif target == ReviewStatus.REJECTED:
            self._audit.append(
                AuditEvent(
                    event_id=new_event_id(),
                    event_type="rejection",
                    timestamp_utc=now,
                    actor=actor,
                    listing_id=item.listing_id,
                    review_item_id=item_id,
                    decision="rejected",
                    reason_codes=["queue_transition"],
                    redacted_meta={"runtime_mode": self._s.runtime_mode.value},
                )
            )
            OperationalHistoryStore(settings=self._s).append_event(
                "queue_rejected",
                source="queue",
                listing_id=updated.listing_id,
                review_item_id=item_id,
                actor=actor,
                payload={"proposed_action_type": updated.proposed_action_type.value},
            )
        return updated

    def acknowledge_dry_run(self, item_id: str, *, actor: str) -> ReviewQueueItem:
        """
        Operator confirms they reviewed the proposed before/after diff (still PENDING).
        Required before Approve when require_dry_run_acknowledgement is enabled.
        All queue mutations that bypass transition() are forbidden — this is the only API for dry-run ack on PENDING rows.
        """
        actor_s = (actor or "").strip()
        if not actor_s:
            raise QueueTransitionError("Operator identity (actor) is required for dry-run acknowledgement.")
        item = self._items.get(item_id)
        if not item:
            raise QueueTransitionError(f"Unknown queue item id={item_id!r}.")
        if item.status != ReviewStatus.PENDING:
            raise QueueTransitionError(
                f"dry_run acknowledgement only allowed for pending items (status={item.status.value})."
            )
        if item.dry_run_acknowledged:
            return item
        now = utc_now()
        updated = item.model_copy(update={"dry_run_acknowledged": True})
        self._items[item_id] = updated
        self._save()
        self._audit.append(
            AuditEvent(
                event_id=new_event_id(),
                event_type="queue_dry_run_acknowledged",
                timestamp_utc=now,
                actor=actor_s,
                listing_id=item.listing_id,
                review_item_id=item_id,
                decision="dry_run_acknowledged",
                reason_codes=["operator_viewed_diff"],
                redacted_meta={"runtime_mode": self._s.runtime_mode.value},
            )
        )
        logger.info("Dry-run acknowledged item=%s actor=%s", item_id, actor_s)
        return updated

    def set_status(self, item_id: str, status: ReviewStatus) -> Optional[ReviewQueueItem]:
        raise QueueTransitionError(
            "set_status is disabled — use ReviewQueueStore.transition(..., actor=...) "
            "with explicit operator id and workflow validation."
        )

    def flag_stale_vs_live(self, listings_by_id: Dict[str, ListingRecord]) -> int:
        """Mark pending items when live listing fingerprint drifted from enqueue-time snapshot."""
        changed = 0
        now = datetime.now(timezone.utc)
        for item in list(self._items.values()):
            if item.status != ReviewStatus.PENDING:
                continue
            if not item.listing_snapshot_fingerprint:
                continue
            cur = listings_by_id.get(item.listing_id)
            if not cur:
                continue
            if listing_snapshot_fingerprint(cur) != item.listing_snapshot_fingerprint:
                upd: dict = {"is_stale_vs_live": True}
                if item.stale_detected_at is None:
                    upd["stale_detected_at"] = now
                self.add(item.model_copy(update=upd))
                self._audit.append(
                    AuditEvent(
                        event_id=new_event_id(),
                        event_type="queue_stale_vs_live_detected",
                        timestamp_utc=now,
                        actor=self._s.default_actor,
                        listing_id=item.listing_id,
                        review_item_id=item.id,
                        decision="stale_vs_live",
                        reason_codes=["listing_fingerprint_drift"],
                        redacted_meta={"runtime_mode": self._s.runtime_mode.value},
                    )
                )
                changed += 1
        return changed
