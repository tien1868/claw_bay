from pathlib import Path

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ProposedActionType, ReviewStatus, StrategicPath
from ebay_claw.review_queue.store import ReviewQueueStore


def test_pending_dedupe_supersedes_older(tmp_path: Path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    q = ReviewQueueStore(path=tmp_path / "q.json", settings=s)
    first = q.create(
        listing_id="L1",
        listing_title="t",
        snapshot={"title": "t", "price_amount": 10.0, "item_specifics": {}},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={"a": 1},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )
    second = q.create(
        listing_id="L1",
        listing_title="t",
        snapshot={"title": "t", "price_amount": 10.0, "item_specifics": {}},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={"a": 2},
        confidence=0.6,
        rationale="r2",
        impact_90="i2",
    )
    updated_first = q.get(first.id)
    assert updated_first is not None
    assert updated_first.status == ReviewStatus.SUPERSEDED
    assert updated_first.superseded_by == second.id
    assert second.version == 2
