from datetime import datetime, timezone
from pathlib import Path

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import (
    ListingRecord,
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.policies.safety import PolicyEngine


def test_blocks_title_stripping_flaws(tmp_path: Path):
    eng = PolicyEngine(
        settings=Settings(
            audit_log_path=tmp_path / "a.jsonl",
            policy_structured_log_path=tmp_path / "p.jsonl",
            policy_log_path=tmp_path / "p.log",
        )
    )
    lst = ListingRecord(
        listing_id="x",
        title="damage hole",
        price_amount=10,
    )
    item = ReviewQueueItem(
        id="1",
        listing_id="x",
        listing_title="damage hole",
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={
            "title_before": "damage hole",
            "title_after": "nice shirt",
        },
        confidence=0.9,
        rationale="t",
        expected_impact_90d="x",
        created_at=datetime.now(timezone.utc),
        status=ReviewStatus.PENDING,
    )
    out = eng.evaluate_review_item(item, lst)
    assert not out.allowed
