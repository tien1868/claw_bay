from datetime import datetime, timezone

from ebay_claw.config.settings import Settings
from ebay_claw.execution.mock_executor import MockExecutor
from ebay_claw.models.domain import (
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.models.runtime_mode import ClawRuntimeMode


def test_executor_blocks_live_writes_even_when_enabled():
    s = Settings(
        execution_enabled=True,
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        ebay_access_token="x",
    )
    ex = MockExecutor(settings=s)
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
        created_at=datetime.now(timezone.utc),
        status=ReviewStatus.APPROVED,
    )
    r = ex.apply(item)
    assert not r.success
    assert "live_read_only" in r.message.lower() or "blocked" in r.message.lower()
