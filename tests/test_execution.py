from datetime import datetime, timezone

from ebay_claw.config.settings import Settings
from ebay_claw.execution.mock_executor import MockExecutor
from ebay_claw.models.domain import (
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)


def test_execution_disabled_by_default():
    ex = MockExecutor(settings=Settings(execution_enabled=False))
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
