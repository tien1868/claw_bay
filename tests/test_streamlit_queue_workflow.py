"""Queue workflow rules exercised the same way Streamlit drives DashboardAPI (no Streamlit dep)."""

from datetime import datetime, timezone

import pytest

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ProposedActionType, ReviewStatus, StrategicPath
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.review_queue.apply_guard import apply_executor_ready, list_apply_operator_blockers
from ebay_claw.review_queue.state_machine import QueueTransitionError
from ebay_claw.review_queue.store import ReviewQueueStore
from ebay_claw.services.dashboard_api import DashboardAPI
from ebay_claw.services.orchestrator import ClawOrchestrator


def _pend_item(q: ReviewQueueStore):
    return q.create(
        listing_id="L1",
        listing_title="Enough title length here for compliance",
        snapshot={"listing_id": "L1", "title": "Enough title length here", "price_amount": 10.0},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={"title": {"before": "a", "after": "b"}},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )


def test_dashboard_apply_readiness_reflects_guard_and_policy_flags(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
        runtime_mode=ClawRuntimeMode.LIVE_GUARDED_WRITE,
        guarded_write_enabled=True,
        execution_enabled=True,
        ebay_access_token="unit-test-token",
    )
    orch = ClawOrchestrator(settings=s)
    api = DashboardAPI(orch)
    item = _pend_item(orch.queue)
    r0 = api.apply_readiness_for_queue_item(item.id)
    assert r0["executor_ready"] is False
    assert any("[apply_guard]" in x for x in r0["blockers"])
    orch.queue.add(
        item.model_copy(
            update={
                "policy_flags": ["blocked_reason"],
                "status": ReviewStatus.APPROVED,
                "approved_by": "op",
                "reviewed_at": datetime.now(timezone.utc),
                "approved_at": datetime.now(timezone.utc),
                "dry_run_acknowledged": True,
            }
        )
    )
    r1 = api.apply_readiness_for_queue_item(item.id)
    assert r1["executor_ready"] is False
    assert any("[policy]" in x and "policy_flags" in x for x in r1["blockers"])


def test_dashboard_transition_enforces_acknowledge_before_approve_when_required(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
        require_dry_run_acknowledgement=True,
    )
    orch = ClawOrchestrator(settings=s)
    api = DashboardAPI(orch)
    item = _pend_item(orch.queue)
    bad = api.queue_transition_ui(
        item.id,
        ReviewStatus.APPROVED,
        actor="alex",
        dry_run_acknowledged=True,
    )
    assert bad["ok"] is False
    assert "acknowledge" in bad["error"].lower()
    api.queue_acknowledge_dry_run(item.id, actor="alex")
    good = api.queue_transition_ui(
        item.id,
        ReviewStatus.APPROVED,
        actor="alex",
        dry_run_acknowledged=True,
    )
    assert good["ok"] is True
    assert good["item"]["approved_by"] == "alex"


def test_apply_executor_ready_matches_aggregate_blockers(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        execution_enabled=True,
    )
    q = ReviewQueueStore(settings=s)
    item = _pend_item(q)
    assert apply_executor_ready(s, item) is False
    assert list_apply_operator_blockers(s, item)


def test_acknowledge_dry_run_idempotent_returns_same_item(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
    )
    q = ReviewQueueStore(settings=s)
    item = _pend_item(q)
    a1 = q.acknowledge_dry_run(item.id, actor="p")
    a2 = q.acknowledge_dry_run(item.id, actor="p")
    assert a1.id == a2.id
    assert a2.dry_run_acknowledged is True


def test_acknowledge_fails_when_not_pending(tmp_path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        sync_history_path=tmp_path / "h.jsonl",
        require_dry_run_acknowledgement=True,
    )
    q = ReviewQueueStore(settings=s)
    item = _pend_item(q)
    q.acknowledge_dry_run(item.id, actor="u")
    q.transition(item.id, ReviewStatus.APPROVED, actor="u", dry_run_acknowledged=True)
    with pytest.raises(QueueTransitionError):
        q.acknowledge_dry_run(item.id, actor="u")
