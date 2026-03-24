"""Explicit review-queue transitions — production operator discipline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Tuple

from ebay_claw.models.domain import ReviewQueueItem, ReviewStatus

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings


class QueueTransitionError(ValueError):
    """Illegal or incomplete queue status change."""


# (from_status, to_status) allowed without extra semantics check
_ALLOWED_EDGES: frozenset[Tuple[ReviewStatus, ReviewStatus]] = frozenset(
    {
        (ReviewStatus.PENDING, ReviewStatus.APPROVED),
        (ReviewStatus.PENDING, ReviewStatus.REJECTED),
        (ReviewStatus.PENDING, ReviewStatus.SUPERSEDED),
        (ReviewStatus.APPROVED, ReviewStatus.APPLIED),
        (ReviewStatus.APPROVED, ReviewStatus.FAILED),
    }
)


def assert_transition_allowed(current: ReviewStatus, target: ReviewStatus) -> None:
    if (current, target) not in _ALLOWED_EDGES:
        raise QueueTransitionError(
            f"Transition not allowed: {current.value} → {target.value}. "
            "Allowed: pending→approved|rejected|superseded; approved→applied|failed."
        )


def build_transition_update(
    item: ReviewQueueItem,
    target: ReviewStatus,
    *,
    now: datetime,
    actor: str,
    dry_run_acknowledged: Optional[bool] = None,
    settings: Optional["Settings"] = None,
) -> dict:
    """
    Validate operator fields and return a dict of fields to merge onto the item.
    Raises QueueTransitionError if requirements are not met.
    """
    actor_s = (actor or "").strip()
    if not actor_s:
        raise QueueTransitionError("actor (operator id) is required for queue transitions")

    assert_transition_allowed(item.status, target)

    patch: dict = {"status": target}

    if target == ReviewStatus.APPROVED:
        reviewed = now
        patch.update(
            {
                "reviewed_at": reviewed,
                "approved_at": now,
                "approved_by": actor_s,
                "rejected_at": None,
                "rejected_by": None,
            }
        )
        if settings and settings.require_dry_run_acknowledgement:
            if not item.dry_run_acknowledged:
                raise QueueTransitionError(
                    "Dry-run / diff must be acknowledged first — call "
                    "ReviewQueueStore.acknowledge_dry_run(...) before approving "
                    "(EBAY_CLAW_REQUIRE_DRY_RUN_ACKNOWLEDGEMENT)."
                )
            if dry_run_acknowledged is not True:
                raise QueueTransitionError(
                    "dry_run_acknowledged must be True before approval "
                    "(EBAY_CLAW_REQUIRE_DRY_RUN_ACKNOWLEDGEMENT)."
                )
            patch["dry_run_acknowledged"] = True
        elif dry_run_acknowledged is not None:
            patch["dry_run_acknowledged"] = bool(dry_run_acknowledged)

    elif target == ReviewStatus.REJECTED:
        patch.update(
            {
                "reviewed_at": now,
                "rejected_at": now,
                "rejected_by": actor_s,
                "approved_at": None,
                "approved_by": None,
                "dry_run_acknowledged": False,
            }
        )

    elif target == ReviewStatus.SUPERSEDED:
        # Timestamps optional; supersede path sets superseded_by elsewhere
        pass

    elif target in (ReviewStatus.APPLIED, ReviewStatus.FAILED):
        if item.status != ReviewStatus.APPROVED:
            raise QueueTransitionError("Only an approved item can move to applied/failed.")
        if not item.approved_at or not item.reviewed_at or not (item.approved_by or "").strip():
            raise QueueTransitionError(
                "Approved item missing approved_at, reviewed_at, or approved_by — data integrity error."
            )
        patch["status"] = target

    return patch


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
