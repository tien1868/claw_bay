from datetime import datetime, timezone
from pathlib import Path

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ListingRecord, ProposedActionType, ReviewQueueItem, ReviewStatus, StrategicPath
from ebay_claw.policies.safety import PolicyEngine


def test_blocks_low_confidence_material_addition(tmp_path: Path):
    eng = PolicyEngine(
        settings=Settings(
            audit_log_path=tmp_path / "a.jsonl",
            policy_structured_log_path=tmp_path / "p.jsonl",
            policy_log_path=tmp_path / "p.log",
        )
    )
    item = ReviewQueueItem(
        id="1",
        listing_id="x",
        listing_title="t",
        proposed_action_type=ProposedActionType.UPDATE_ITEM_SPECIFICS,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={
            "additions": [
                {"name": "Material", "confidence": 0.5, "proposed_value": "Cashmere"}
            ]
        },
        confidence=0.5,
        rationale="r",
        expected_impact_90d="x",
        created_at=datetime.now(timezone.utc),
        status=ReviewStatus.PENDING,
    )
    out = eng.evaluate_review_item(item, ListingRecord(listing_id="x", title="t", price_amount=1.0))
    assert not out.allowed
    assert any("material" in b for b in out.blocked_reasons)
