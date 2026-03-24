"""Guarded apply pipeline — identity, policy, audit, queue transitions (mock execution only)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ebay_claw.adapters.mock_json import raw_dict_to_listing
from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import (
    ApplyResult,
    ListingRecord,
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.execution.idempotency import ApplyIdempotencyStore, build_apply_idempotency_key
from ebay_claw.execution.ebay_write_executor import EbayWriteExecutor
from ebay_claw.services.apply_api import ApplyApiError, ApplyApiService
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.review_queue.fingerprint import listing_snapshot_fingerprint
from ebay_claw.review_queue.store import ReviewQueueStore
from ebay_claw.services.dashboard_api import DashboardAPI
from ebay_claw.services.guarded_apply import (
    GuardedApplyService,
    build_guarded_apply_for_orchestrator,
)
from ebay_claw.services.orchestrator import ClawOrchestrator


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_mode=ClawRuntimeMode.LIVE_GUARDED_WRITE,
        guarded_write_enabled=True,
        execution_enabled=True,
        require_policy_pass_for_write=True,
        ebay_access_token="unit-test-token-not-for-production",
        audit_log_path=tmp_path / "audit.jsonl",
        review_queue_path=tmp_path / "q.json",
        policy_structured_log_path=tmp_path / "pol.jsonl",
        policy_log_path=tmp_path / "pol.log",
        sync_history_path=tmp_path / "sync_hist.jsonl",
        apply_idempotency_store_path=tmp_path / "apply_idempotency.jsonl",
        fixture_path=Path("fixtures/sample_listings.json"),
    )


def _fixture_listings() -> list[ListingRecord]:
    data = json.loads(
        Path("fixtures/sample_listings.json").read_text(encoding="utf-8")
    )
    return [raw_dict_to_listing(x) for x in data["listings"]]


def _service(tmp_path: Path) -> tuple[Settings, ReviewQueueStore, list[ListingRecord], GuardedApplyService]:
    s = _settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listings = _fixture_listings()

    def _resolve(lid: str) -> ListingRecord | None:
        return next((x for x in listings if x.listing_id == lid), None)

    svc = GuardedApplyService(settings=s, queue=q, resolve_live_listing=_resolve)
    return s, q, listings, svc


def _approved_title_item(listing, item_id: str, fp: str, now) -> ReviewQueueItem:
    return ReviewQueueItem(
        id=item_id,
        listing_id=listing.listing_id,
        listing_title=listing.title,
        current_state_snapshot=listing.model_dump(),
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={"title_before": listing.title, "title_after": listing.title},
        confidence=0.9,
        rationale="r",
        expected_impact_90d="x",
        created_at=now,
        status=ReviewStatus.APPROVED,
        approved_by="approver",
        reviewed_at=now,
        approved_at=now,
        dry_run_acknowledged=True,
        listing_snapshot_fingerprint=fp,
        compliance_issues=[],
        policy_flags=[],
    )


def _audit_event_types(audit_path: Path) -> list[str]:
    if not audit_path.exists():
        return []
    out = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line)["event_type"])
    return out


def test_simulated_apply_success_when_identity_and_policy_pass(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "q-success", fp, now)
    q.add(item)

    res = svc.simulate_apply(item.id, actor="operator-1")
    assert res.ok
    assert res.execution and res.execution.success
    assert res.execution.idempotency_key == build_apply_idempotency_key(item)
    assert res.execution.attempted_action == item.proposed_action_type
    assert res.execution.simulated is True
    assert res.execution.correlation_id
    assert res.execution.user_safe_message
    assert q.get(item.id).status == ReviewStatus.APPLIED

    types = _audit_event_types(s.audit_log_path)
    assert "apply_requested" in types
    assert "apply_revalidated" in types
    assert "apply_simulated_success" in types
    assert types.count("apply_blocked") == 0


def test_fingerprint_mismatch_blocks_apply(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp_wrong = "0" * 40
    item = _approved_title_item(listing, "q-drift", fp_wrong, now)
    q.add(item)

    res = svc.simulate_apply(item.id, actor="operator-2")
    assert not res.ok
    assert res.blocked_stage == "identity"
    assert any("fingerprint" in b.lower() for b in res.identity_blockers)
    assert q.get(item.id).status == ReviewStatus.APPROVED

    types = _audit_event_types(s.audit_log_path)
    assert "apply_blocked" in types
    assert "apply_revalidated" not in types


def test_stale_queue_row_blocks_before_identity(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "q-stale", fp, now)
    item = item.model_copy(update={"is_stale_vs_live": True})
    q.add(item)

    res = svc.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "state_machine"
    assert q.get(item.id).status == ReviewStatus.APPROVED


def test_policy_failure_surfaces_distinct_blockers(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    assert (listing.watchers or 0) >= 1
    fp = listing_snapshot_fingerprint(listing)
    item = ReviewQueueItem(
        id="q-pol",
        listing_id=listing.listing_id,
        listing_title=listing.title,
        current_state_snapshot=listing.model_dump(),
        proposed_action_type=ProposedActionType.END_AND_SELL_SIMILAR,
        recommended_strategy=StrategicPath.END_AND_SELL_SIMILAR,
        before_after_diff={},
        confidence=0.9,
        rationale="r",
        expected_impact_90d="x",
        created_at=now,
        status=ReviewStatus.APPROVED,
        approved_by="a",
        reviewed_at=now,
        approved_at=now,
        dry_run_acknowledged=True,
        listing_snapshot_fingerprint=fp,
        compliance_issues=[],
        policy_flags=[],
    )
    q.add(item)

    res = svc.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "policy"
    assert res.policy_blockers
    assert "watchers" in " ".join(res.policy_blockers).lower()


def test_dashboard_api_has_no_simulated_apply_exposed(tmp_path: Path):
    """Operators apply via orchestrator / future API — not Streamlit dashboard helpers."""
    s = Settings(
        runtime_mode=ClawRuntimeMode.FIXTURE,
        audit_log_path=tmp_path / "a.jsonl",
        review_queue_path=tmp_path / "q.json",
        sync_history_path=tmp_path / "h.jsonl",
        policy_structured_log_path=tmp_path / "p.jsonl",
        policy_log_path=tmp_path / "p.log",
        fixture_path=Path("fixtures/sample_listings.json"),
    )
    orch = ClawOrchestrator(settings=s)
    api = DashboardAPI(orch)
    assert not hasattr(api, "simulate_guarded_apply")
    assert not hasattr(api, "guarded_apply_service")


def test_mock_executor_does_not_apply_queue_without_explicit_flag(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    from ebay_claw.execution.mock_executor import MockExecutor

    ex = MockExecutor(settings=s, queue=q)
    item = ReviewQueueItem(
        id="mx",
        listing_id="L1001",
        listing_title="t",
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={"title_before": "a", "title_after": "b"},
        confidence=0.5,
        rationale="r",
        expected_impact_90d="x",
        created_at=now,
        status=ReviewStatus.APPROVED,
        approved_by="op",
        reviewed_at=now,
        approved_at=now,
        dry_run_acknowledged=True,
        compliance_issues=[],
    )
    q.add(item)
    r = ex.apply(item, legacy_audit=False, transition_queue=False)
    assert r.success
    assert q.get(item.id).status == ReviewStatus.APPROVED


def test_build_guarded_apply_resolver_finds_listing(tmp_path: Path):
    s, q, listings, _ = _service(tmp_path)
    svc = build_guarded_apply_for_orchestrator(
        settings=s,
        queue=q,
        load_listings=lambda: listings,
    )
    assert isinstance(svc, GuardedApplyService)


def test_simulated_executor_failure_marks_queue_failed_and_audits(tmp_path: Path, monkeypatch):
    s, q, listings, svc = _service(tmp_path)
    now = datetime.now(timezone.utc)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "q-fail-ex", fp, now)
    q.add(item)

    def _fake_apply(*_a, **kwargs):
        return ApplyResult(
            listing_id=item.listing_id,
            attempted_action=item.proposed_action_type,
            success=False,
            user_safe_message="injected_simulated_failure",
            idempotency_key=kwargs.get("idempotency_key", ""),
            retryable=False,
            simulated=True,
            adapter_detail={"injected": True},
            changed_specific_keys=[],
        )

    monkeypatch.setattr(svc._executor, "apply", _fake_apply)
    res = svc.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "executor"
    assert q.get(item.id).status == ReviewStatus.FAILED
    types = _audit_event_types(s.audit_log_path)
    assert "apply_simulated_failure" in types


def test_orchestrator_simulate_guarded_apply_delegates_to_service(tmp_path: Path):
    s = _settings(tmp_path)
    listings = _fixture_listings()
    orch = ClawOrchestrator(settings=s)
    orch.load_listings = lambda: list(listings)  # type: ignore[method-assign, assignment]
    now = datetime.now(timezone.utc)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "orch-int", fp, now)
    orch.queue.add(item)
    res = orch.simulate_guarded_apply(item.id, actor="orch-op")
    assert res.ok
    assert orch.queue.get(item.id).status == ReviewStatus.APPLIED


def test_idempotency_precheck_blocks_duplicate(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "q-idem", fp, now)
    q.add(item)
    key = build_apply_idempotency_key(item)
    ApplyIdempotencyStore(s.apply_idempotency_store_path).record_success(
        idempotency_key=key,
        review_item_id=item.id,
        listing_id=item.listing_id,
    )
    res = svc.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "idempotency"
    assert res.idempotency_blockers


def test_missing_enqueue_fingerprint_blocks_identity(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "q-nofp", fp, now)
    item = item.model_copy(update={"listing_snapshot_fingerprint": ""})
    q.add(item)

    res = svc.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "identity"
    assert any("fingerprint_missing" in b for b in res.identity_blockers)


def test_strict_snapshot_sku_missing_when_live_has_sku(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, _ = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    live_with_sku = listing.model_copy(update={"sku": "SKU-strict-1"})
    fp = listing_snapshot_fingerprint(live_with_sku)
    listings2 = [live_with_sku if x.listing_id == "L1001" else x for x in listings]

    def _resolve(lid: str) -> ListingRecord | None:
        return next((x for x in listings2 if x.listing_id == lid), None)

    svc2 = GuardedApplyService(settings=s, queue=q, resolve_live_listing=_resolve)
    item = _approved_title_item(live_with_sku, "q-sku-miss", fp, now)
    item = item.model_copy(update={"current_state_snapshot": listing.model_dump()})
    q.add(item)

    res = svc2.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "identity"
    assert any("snapshot_sku_required" in b for b in res.identity_blockers)


def test_strict_snapshot_offer_missing_when_live_has_offer(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, _ = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    live_offer = listing.model_copy(
        update={"extra": {"ebay_offer_id": "offer-live-1"}}
    )
    listings2 = [live_offer if x.listing_id == "L1001" else x for x in listings]

    def _resolve(lid: str) -> ListingRecord | None:
        return next((x for x in listings2 if x.listing_id == lid), None)

    svc2 = GuardedApplyService(settings=s, queue=q, resolve_live_listing=_resolve)
    fp = listing_snapshot_fingerprint(listing)
    snap = listing.model_dump()
    if "extra" in snap:
        del snap["extra"]
    item = _approved_title_item(listing, "q-offer-miss", fp, now)
    item = item.model_copy(update={"current_state_snapshot": snap})
    q.add(item)

    res = svc2.simulate_apply(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "identity"
    assert any("snapshot_ebay_offer_id_required" in b for b in res.identity_blockers)


def test_ebay_write_executor_init_fail_closed(tmp_path: Path):
    with pytest.raises(RuntimeError, match="refused"):
        EbayWriteExecutor(Settings())


def test_apply_approved_item_matches_simulate_apply_pipeline(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    i1 = _approved_title_item(listing, "q-alias-sim", fp, now)
    i2 = _approved_title_item(listing, "q-alias-api", fp, now)
    q.add(i1)
    q.add(i2)
    r_sim = svc.simulate_apply(i1.id, actor="a")
    r_alias = svc.apply_approved_item(i2.id, actor="a")
    assert r_sim.ok and r_alias.ok
    assert q.get(i1.id).status == q.get(i2.id).status == ReviewStatus.APPLIED


def test_apply_api_requires_enable_and_secret(tmp_path: Path):
    s, q, listings, svc = _service(tmp_path)
    with pytest.raises(ApplyApiError, match="disabled"):
        ApplyApiService.invoke_apply(
            s,
            shared_secret="x",
            guarded=svc,
            review_item_id="i",
            actor="a",
        )
    s2 = s.model_copy(update={"apply_api_enabled": True, "apply_api_shared_secret": ""})
    with pytest.raises(ApplyApiError, match="secret"):
        ApplyApiService.invoke_apply(
            s2,
            shared_secret="x",
            guarded=svc,
            review_item_id="i",
            actor="a",
        )
    s3 = s2.model_copy(update={"apply_api_shared_secret": "good"})
    with pytest.raises(ApplyApiError, match="unauthorized"):
        ApplyApiService.invoke_apply(
            s3,
            shared_secret="wrong",
            guarded=svc,
            review_item_id="i",
            actor="a",
        )


def test_apply_api_happy_path_delegates_to_guarded_service(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s, q, listings, svc = _service(tmp_path)
    s_api = s.model_copy(
        update={"apply_api_enabled": True, "apply_api_shared_secret": "secret"}
    )
    listing = next(x for x in listings if x.listing_id == "L1001")
    fp = listing_snapshot_fingerprint(listing)
    item = _approved_title_item(listing, "api-happy", fp, now)
    q.add(item)
    res = ApplyApiService.invoke_apply(
        s_api,
        shared_secret="secret",
        guarded=svc,
        review_item_id=item.id,
        actor="cron",
    )
    assert res.ok
    assert res.execution and res.execution.success
