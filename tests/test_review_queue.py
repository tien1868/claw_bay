from pathlib import Path

from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ProposedActionType, ReviewStatus, StrategicPath
from ebay_claw.review_queue.store import ReviewQueueStore


def test_status_transition(tmp_path: Path):
    s = Settings(review_queue_path=tmp_path / "q.json", audit_log_path=tmp_path / "a.jsonl")
    q = ReviewQueueStore(path=tmp_path / "q.json", settings=s)
    item = q.create(
        listing_id="L",
        listing_title="t",
        snapshot={},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={},
        confidence=0.5,
        rationale="r",
        impact_90="i",
    )
    q.acknowledge_dry_run(item.id, actor="tester")
    q.transition(
        item.id,
        ReviewStatus.APPROVED,
        actor="tester",
        dry_run_acknowledged=True,
    )
    assert q.get(item.id).status == ReviewStatus.APPROVED
    assert q.get(item.id).approved_by == "tester"
    assert q.get(item.id).approved_at is not None
