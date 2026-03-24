from datetime import datetime, timezone
from pathlib import Path

from ebay_claw.config.settings import Settings
from ebay_claw.execution.mock_executor import MockExecutor
from ebay_claw.models.domain import (
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.models.runtime_mode import ClawRuntimeMode


def test_sandbox_executor_can_succeed_when_unlocked(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = Settings(
        runtime_mode=ClawRuntimeMode.LIVE_GUARDED_WRITE,
        guarded_write_enabled=True,
        execution_enabled=True,
        ebay_use_sandbox=True,
        audit_log_path=tmp_path / "audit.jsonl",
        policy_structured_log_path=tmp_path / "pol.jsonl",
        policy_log_path=tmp_path / "pol.log",
        review_queue_path=tmp_path / "q.json",
    )
    ex = MockExecutor(settings=s)
    item = ReviewQueueItem(
        id="1",
        listing_id="L",
        listing_title="Enough length title here",
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={"title_before": "a", "title_after": "b"},
        confidence=0.5,
        rationale="r",
        expected_impact_90d="x",
        created_at=now,
        status=ReviewStatus.APPROVED,
        dry_run_acknowledged=True,
        approved_by="operator_alpha",
        reviewed_at=now,
        approved_at=now,
        compliance_warnings=[],
        compliance_issues=[],
    )
    r = ex.apply(item)
    assert r.success
