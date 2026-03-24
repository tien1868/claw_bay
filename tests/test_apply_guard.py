from datetime import datetime, timezone

import pytest

from ebay_claw.config.settings import Settings
from ebay_claw.execution.mock_executor import MockExecutor
from ebay_claw.models.compliance_issue import ComplianceIssueRecord, ComplianceSeverity
from ebay_claw.models.domain import ProposedActionType, ReviewQueueItem, ReviewStatus, StrategicPath
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.review_queue.apply_guard import ApplyPreconditionError, assert_apply_state_machine_satisfied


def test_apply_guard_blocks_on_compliance_blocking_issue():
    now = datetime.now(timezone.utc)
    s = Settings(runtime_mode=ClawRuntimeMode.LIVE_GUARDED_WRITE, guarded_write_enabled=True)
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
        status=ReviewStatus.APPROVED,
        approved_by="op",
        reviewed_at=now,
        approved_at=now,
        dry_run_acknowledged=True,
        compliance_issues=[
            ComplianceIssueRecord(
                code="x",
                severity=ComplianceSeverity.BLOCKING,
                message="blocked",
                blocks_guarded_write=True,
            )
        ],
    )
    with pytest.raises(ApplyPreconditionError, match="Compliance blocking"):
        assert_apply_state_machine_satisfied(s, item)
