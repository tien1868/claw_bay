from datetime import datetime, timezone

import pytest

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ProposedActionType, ReviewStatus, StrategicPath
from ebay_claw.review_queue.state_machine import QueueTransitionError, build_transition_update
from ebay_claw.review_queue.store import ReviewQueueStore


def test_cannot_jump_pending_to_applied(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
    )
    q = ReviewQueueStore(settings=s)
    item = q.create(
        listing_id="L1",
        listing_title="Enough title length here for compliance",
        snapshot={"listing_id": "L1", "title": "Enough title length here", "price_amount": 10.0},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )
    with pytest.raises(QueueTransitionError):
        q.transition(item.id, ReviewStatus.APPLIED, actor="x")


def test_approval_requires_actor_and_dry_run_when_configured(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
        require_dry_run_acknowledgement=True,
    )
    q = ReviewQueueStore(settings=s)
    item = q.create(
        listing_id="L1",
        listing_title="Enough title length here for compliance",
        snapshot={"listing_id": "L1", "title": "Enough title length here", "price_amount": 10.0},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )
    with pytest.raises(QueueTransitionError):
        q.transition(item.id, ReviewStatus.APPROVED, actor="bob")
    with pytest.raises(QueueTransitionError, match="acknowledge"):
        q.transition(
            item.id,
            ReviewStatus.APPROVED,
            actor="bob",
            dry_run_acknowledged=True,
        )
    q.acknowledge_dry_run(item.id, actor="bob")
    updated = q.transition(
        item.id,
        ReviewStatus.APPROVED,
        actor="bob",
        dry_run_acknowledged=True,
    )
    assert updated is not None
    assert updated.approved_by == "bob"
    assert updated.dry_run_acknowledged is True
    assert updated.reviewed_at is not None
    assert updated.approved_at is not None


def test_rejection_sets_timestamps_and_rejected_by(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
        require_dry_run_acknowledgement=True,
    )
    q = ReviewQueueStore(settings=s)
    item = q.create(
        listing_id="L1",
        listing_title="Enough title length here for compliance",
        snapshot={"listing_id": "L1", "title": "Enough title length here", "price_amount": 10.0},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )
    out = q.transition(item.id, ReviewStatus.REJECTED, actor="carol")
    assert out is not None
    assert out.rejected_by == "carol"
    assert out.rejected_at is not None
    assert out.approved_by is None
    assert out.dry_run_acknowledged is False


def test_build_transition_rejects_empty_actor():
    now = datetime.now(timezone.utc)
    from ebay_claw.models.domain import ReviewQueueItem

    item = ReviewQueueItem(
        id="1",
        listing_id="L",
        listing_title="t",
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={},
        confidence=0.5,
        rationale="r",
        expected_impact_90d="x",
        created_at=now,
        status=ReviewStatus.PENDING,
        dry_run_acknowledged=True,
    )
    with pytest.raises(QueueTransitionError):
        build_transition_update(
            item,
            ReviewStatus.APPROVED,
            now=now,
            actor="   ",
            dry_run_acknowledged=True,
            settings=Settings(require_dry_run_acknowledgement=True),
        )
