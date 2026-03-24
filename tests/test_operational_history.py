"""Operational history store, velocity rollups, and ranking hooks — read-only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ebay_claw.analytics.history_scoring import compute_action_track_scores
from ebay_claw.analytics.velocity_metrics import compute_velocity_metrics
from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import ListingRecord, ProposedActionType, ReviewStatus, StrategicPath
from ebay_claw.models.recovery import VelocityMetrics
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.review_queue.store import ReviewQueueStore
from ebay_claw.services.daily_priority_actions import build_daily_priority_actions
from ebay_claw.services.inventory_movement_recorder import InventoryMovementRecorder
from ebay_claw.services.operational_history_store import OperationalHistoryStore
from ebay_claw.services.orchestrator import ClawOrchestrator
from ebay_claw.analytics.inventory_analyst import InventoryAnalyst


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_mode=ClawRuntimeMode.FIXTURE,
        require_dry_run_acknowledgement=False,
        operational_history_path=tmp_path / "op_hist.jsonl",
        inventory_movement_snapshot_path=tmp_path / "inv_snap.json",
        audit_log_path=tmp_path / "audit.jsonl",
        review_queue_path=tmp_path / "q.json",
        sync_history_path=tmp_path / "sh.jsonl",
        sync_state_path=tmp_path / "ss.json",
        policy_structured_log_path=tmp_path / "pol.jsonl",
        policy_log_path=tmp_path / "pol.log",
        fixture_path=Path("fixtures/sample_listings.json"),
    )


def test_operational_history_append_and_query(tmp_path: Path):
    s = _settings(tmp_path)
    store = OperationalHistoryStore(settings=s)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    store.append_event(
        "listing_created",
        source="test",
        listing_id="L1",
        payload={"x": 1},
        occurred_at_utc=t0,
    )
    store.append_event(
        "listing_synced",
        source="test",
        payload={"listing_count": 3},
        occurred_at_utc=t0 + timedelta(days=1),
    )
    lines = s.operational_history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rows = [json.loads(x) for x in lines]
    assert rows[0]["event_type"] == "listing_created"
    assert rows[1]["event_type"] == "listing_synced"
    c = store.count_events(
        "listing_created",
        since_utc=t0 - timedelta(hours=1),
        until_utc=t0 + timedelta(hours=2),
    )
    assert c == 1


def test_velocity_rollups_use_events_when_coverage(tmp_path: Path):
    s = _settings(tmp_path)
    store = OperationalHistoryStore(settings=s)
    end = datetime(2026, 3, 10, 23, 59, 59, tzinfo=timezone.utc)
    start = end - timedelta(days=7)
    store.append_event(
        "listing_synced",
        source="test",
        payload={"listing_count": 5},
        occurred_at_utc=end,
    )
    store.append_event(
        "listing_created",
        source="test",
        listing_id="A",
        occurred_at_utc=start + timedelta(days=1),
    )
    store.append_event(
        "listing_sold",
        source="test",
        listing_id="B",
        payload={"units": 2.0},
        occurred_at_utc=start + timedelta(days=2),
    )
    listings = [
        ListingRecord(
            listing_id="X",
            title="t",
            price_amount=10.0,
            listed_on=date(2026, 3, 1),
        )
    ]
    vm = compute_velocity_metrics(
        listings,
        as_of=date(2026, 3, 10),
        settings=s,
        history_store=store,
    )
    assert isinstance(vm, VelocityMetrics)
    assert vm.historical_coverage_ok is True
    assert vm.listings_created_last_7d >= 1
    assert vm.sold_units_event_last_7d is not None
    assert vm.weekly_trend_last_4


def test_velocity_falls_back_without_sync_coverage(tmp_path: Path):
    s = _settings(tmp_path)
    store = OperationalHistoryStore(settings=s)
    store.append_event(
        "listing_created",
        source="test",
        listing_id="Z",
        occurred_at_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    listings = [
        ListingRecord(
            listing_id="X",
            title="t",
            price_amount=10.0,
            listed_on=date(2026, 3, 1),
        )
    ]
    vm = compute_velocity_metrics(
        listings,
        as_of=date(2026, 3, 10),
        settings=s,
        history_store=store,
    )
    assert vm.historical_coverage_ok is False
    assert vm.metric_sources.get("listings_created_last_7d") == "estimated"


def test_inventory_cold_start_then_created_event(tmp_path: Path):
    s = _settings(tmp_path)
    hist = OperationalHistoryStore(settings=s)
    analyst = InventoryAnalyst(settings=s)
    rec = InventoryMovementRecorder(settings=s)

    a = ListingRecord(
        listing_id="L1",
        title="a",
        price_amount=1.0,
        listed_on=date(2026, 1, 1),
    )
    rec.record_after_ingest([a], as_of=date(2026, 3, 1), analyst=analyst, data_source="fixture")
    n0 = hist.count_events(
        "listing_created",
        since_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert n0 == 0

    b = ListingRecord(
        listing_id="L2",
        title="b",
        price_amount=1.0,
        listed_on=date(2026, 3, 2),
    )
    rec.record_after_ingest(
        [a, b], as_of=date(2026, 3, 3), analyst=analyst, data_source="fixture"
    )
    n1 = hist.count_events(
        "listing_created",
        since_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until_utc=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert n1 == 1


def test_daily_priority_history_boost_with_events(tmp_path: Path):
    s = _settings(tmp_path)
    store = OperationalHistoryStore(settings=s)
    end_d = date(2026, 3, 10)
    for i in range(3):
        store.append_event(
            "relist_proposed",
            source="test",
            listing_id="Lx",
            payload={"proposed_action_type": ProposedActionType.RELIST_CANDIDATE.value},
            occurred_at_utc=datetime(2026, 3, 5 + i, tzinfo=timezone.utc),
        )
        store.append_event(
            "queue_approved",
            source="test",
            listing_id="Lx",
            payload={"proposed_action_type": ProposedActionType.RELIST_CANDIDATE.value},
            occurred_at_utc=datetime(2026, 3, 5 + i, 12, tzinfo=timezone.utc),
        )
    for j in range(15):
        store.append_event(
            "listing_synced",
            source="test",
            payload={"listing_count": 1},
            occurred_at_utc=datetime(
                2026, 3, 1, 10, j, tzinfo=timezone.utc
            ),
        )
    tr = compute_action_track_scores(store, end_d)
    assert tr[ProposedActionType.RELIST_CANDIDATE.value] > 0.4

    listings = [
        ListingRecord(
            listing_id="L1001",
            title="t",
            price_amount=100.0,
            listed_on=date(2025, 1, 1),
        )
    ]
    analyst = InventoryAnalyst(settings=s)

    def enriched(lst: ListingRecord):
        return analyst.analyze(lst, as_of=end_d)

    with_hist = build_daily_priority_actions(
        listings,
        enriched_fn=enriched,
        as_of=end_d,
        top_n=3,
        settings=s,
        history_store=store,
    )
    no_hist = build_daily_priority_actions(
        listings,
        enriched_fn=enriched,
        as_of=end_d,
        top_n=3,
        settings=s,
        history_store=OperationalHistoryStore(
            path=tmp_path / "empty.jsonl", settings=s
        ),
    )
    assert with_hist[0].score_breakdown.get("history_action_track", 0) > 0
    assert no_hist[0].score_breakdown.get("history_data_quality") == 1.0


def test_queue_transition_emits_operational_events(tmp_path: Path):
    s = _settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    now = datetime.now(timezone.utc)
    item = q.create_deduped(
        listing_id="L1",
        listing_title="t",
        snapshot={"title": "t", "price_amount": 10.0, "item_specifics": {}},
        proposed=ProposedActionType.UPDATE_TITLE,
        strategy=StrategicPath.FAST_MOVE,
        diff={},
        confidence=0.8,
        rationale="r",
        impact_90="x",
        listing_snapshot_fingerprint="abc",
        policy_flags=[],
    )
    q.transition(item.id, ReviewStatus.APPROVED, actor="op")
    store = OperationalHistoryStore(settings=s)
    assert (
        store.count_events(
            "queue_approved",
            since_utc=now - timedelta(days=1),
            until_utc=now + timedelta(days=1),
        )
        == 1
    )
