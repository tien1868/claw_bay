from datetime import datetime, timezone
from pathlib import Path

import pytest

from ebay_claw.config.settings import Settings
from ebay_claw.execution.mock_executor import MockExecutor
from ebay_claw.models.domain import (
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.security.read_only import WriteForbiddenError, assert_write_path_allowed, is_write_blocked


def test_assert_write_raises(tmp_path: Path):
    s = Settings(
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        ebay_access_token="tok",
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
    )
    assert is_write_blocked(s)
    with pytest.raises(WriteForbiddenError):
        assert_write_path_allowed(s, reason="unit test")


def test_executor_respects_read_only_even_with_execution_flag(tmp_path: Path):
    s = Settings(
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        execution_enabled=True,
        ebay_access_token="tok",
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
    )
    ex = MockExecutor(settings=s)
    item = ReviewQueueItem(
        id="1",
        listing_id="L",
        listing_title="Enough length title here",
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={},
        confidence=0.5,
        rationale="r",
        expected_impact_90d="x",
        created_at=datetime.now(timezone.utc),
        status=ReviewStatus.APPROVED,
    )
    r = ex.apply(item)
    assert not r.success


def test_is_write_blocked_default_fixture(tmp_path: Path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
    )
    assert is_write_blocked(s)
