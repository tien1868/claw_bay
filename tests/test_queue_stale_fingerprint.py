from pathlib import Path

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ListingRecord, ProposedActionType, StrategicPath
from ebay_claw.review_queue.store import ReviewQueueStore


def test_flag_stale_when_live_price_changes(tmp_path: Path):
    s = Settings(review_queue_path=tmp_path / "q.json", audit_log_path=tmp_path / "a.jsonl")
    q = ReviewQueueStore(path=tmp_path / "q.json", settings=s)
    snap_listing = ListingRecord(
        listing_id="L1",
        title="Shirt",
        price_amount=20.0,
        item_specifics={"Brand": "Gap"},
    )
    item = q.create(
        listing_id="L1",
        listing_title="Shirt",
        snapshot=snap_listing.model_dump(),
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )
    live = snap_listing.model_copy(update={"price_amount": 15.0})
    q.flag_stale_vs_live({"L1": live})
    updated = q.get(item.id)
    assert updated is not None
    assert updated.is_stale_vs_live
