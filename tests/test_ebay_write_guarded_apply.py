"""Guarded apply + EbayWriteExecutor (UPDATE_TITLE / IMPROVE_TITLE) with mocked HTTP."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ebay_claw.adapters.ebay_inventory_mutation import EbayInventoryMutationClient
from ebay_claw.adapters.mock_json import raw_dict_to_listing
from ebay_claw.config.settings import Settings
from ebay_claw.execution.ebay_write_executor import EbayWriteExecutor
from ebay_claw.execution.idempotency import ApplyIdempotencyStore, build_apply_idempotency_key
from ebay_claw.models.domain import (
    ListingRecord,
    ProposedActionType,
    ReviewQueueItem,
    ReviewStatus,
    StrategicPath,
)
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.review_queue.fingerprint import listing_snapshot_fingerprint
from ebay_claw.review_queue.store import ReviewQueueStore
from ebay_claw.services.guarded_apply import GuardedApplyService


def _write_settings(tmp_path: Path) -> Settings:
    return Settings(
        runtime_mode=ClawRuntimeMode.LIVE_GUARDED_WRITE,
        guarded_write_enabled=True,
        execution_enabled=True,
        require_policy_pass_for_write=True,
        require_dry_run_acknowledgement=False,
        ebay_real_writes_enabled=True,
        apply_api_allow_live_executor=True,
        ebay_access_token="test-bearer-token",
        audit_log_path=tmp_path / "audit.jsonl",
        review_queue_path=tmp_path / "q.json",
        policy_structured_log_path=tmp_path / "pol.jsonl",
        policy_log_path=tmp_path / "pol.log",
        sync_history_path=tmp_path / "sh.jsonl",
        apply_idempotency_store_path=tmp_path / "idem.jsonl",
        fixture_path=Path("fixtures/sample_listings.json"),
    )


def _fixture_l1001() -> ListingRecord:
    data = json.loads(Path("fixtures/sample_listings.json").read_text(encoding="utf-8"))
    row = next(x for x in data["listings"] if x["listing_id"] == "L1001")
    return raw_dict_to_listing(row).model_copy(update={"sku": "SKU-L1001"})


def _mock_transport_get_put_204() -> httpx.MockTransport:
    inv = {
        "sku": "SKU-L1001",
        "product": {"title": "nice jacket mens L"},
        "condition": "USED_EXCELLENT",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "inventory_item" in str(request.url):
            return httpx.Response(200, json=inv)
        if request.method == "PUT" and "inventory_item" in str(request.url):
            return httpx.Response(204)
        return httpx.Response(404, text="unexpected in test")

    return httpx.MockTransport(handler)


def _approved_title_item(
    listing: ListingRecord, item_id: str, fp: str, now: datetime
) -> ReviewQueueItem:
    return ReviewQueueItem(
        id=item_id,
        listing_id=listing.listing_id,
        listing_title=listing.title,
        current_state_snapshot=listing.model_dump(),
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={
            "title_before": listing.title,
            "title_after": listing.title + " — light wear",
        },
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


def test_guarded_apply_title_write_success_normalized_result(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp = listing_snapshot_fingerprint(listing)

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    transport = _mock_transport_get_put_204()
    mc = EbayInventoryMutationClient(s, lambda: "tok", transport=transport)
    ex = EbayWriteExecutor(s, mutation_client=mc)

    item = _approved_title_item(listing, "gw-ok", fp, now)
    q.add(item)

    svc = GuardedApplyService(
        settings=s, queue=q, resolve_live_listing=resolve, executor=ex
    )
    res = svc.apply_approved_item(item.id, actor="op")
    assert res.ok
    assert res.execution
    assert res.execution.success
    assert res.execution.simulated is False
    assert res.execution.attempted_action == ProposedActionType.UPDATE_TITLE
    assert res.execution.idempotency_key == build_apply_idempotency_key(item)
    assert res.execution.target_sku == "SKU-L1001"
    assert res.execution.correlation_id
    assert q.get(item.id).status == ReviewStatus.APPLIED


def test_guarded_apply_unsupported_action_marks_failed(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp = listing_snapshot_fingerprint(listing)

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    transport = _mock_transport_get_put_204()
    mc = EbayInventoryMutationClient(s, lambda: "tok", transport=transport)
    ex = EbayWriteExecutor(s, mutation_client=mc)

    item = _approved_title_item(listing, "gw-bad", fp, now)
    item = item.model_copy(
        update={
            "proposed_action_type": ProposedActionType.MARKDOWN_LISTING,
            "before_after_diff": {"markdown_pct": 10, "price_before": 45.0, "price_after": 40.5},
        }
    )
    q.add(item)

    svc = GuardedApplyService(
        settings=s, queue=q, resolve_live_listing=resolve, executor=ex
    )
    res = svc.apply_approved_item(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "executor"
    assert res.execution and not res.execution.success
    m = res.execution.user_safe_message.lower()
    assert "update_title" in m and "update_safe_specifics" in m
    assert res.execution.adapter_detail.get("unsupported_action") is True
    assert q.get(item.id).status == ReviewStatus.FAILED


def test_guarded_apply_missing_sku_identity_block(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    live = listing
    fp = listing_snapshot_fingerprint(listing)

    snap = listing.model_copy(update={"sku": None}).model_dump()

    def resolve(lid: str) -> ListingRecord | None:
        return live if lid == live.listing_id else None

    item = _approved_title_item(listing, "gw-sku", fp, now)
    item = item.model_copy(update={"current_state_snapshot": snap})
    q.add(item)

    ex = EbayWriteExecutor(
        s,
        mutation_client=EbayInventoryMutationClient(
            s, lambda: "t", transport=_mock_transport_get_put_204()
        ),
    )
    svc = GuardedApplyService(
        settings=s, queue=q, resolve_live_listing=resolve, executor=ex
    )
    res = svc.apply_approved_item(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "identity"
    assert q.get(item.id).status == ReviewStatus.APPROVED


def test_guarded_apply_fingerprint_mismatch_blocks(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp_wrong = "0" * 40

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    ex = EbayWriteExecutor(
        s,
        mutation_client=EbayInventoryMutationClient(
            s, lambda: "t", transport=_mock_transport_get_put_204()
        ),
    )
    item = _approved_title_item(listing, "gw-fp", fp_wrong, now)
    q.add(item)
    svc = GuardedApplyService(
        settings=s, queue=q, resolve_live_listing=resolve, executor=ex
    )
    res = svc.apply_approved_item(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "identity"


def test_ebay_write_executor_inventory_title_drift_fails_closed(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    inv = {
        "sku": "SKU-L1001",
        "product": {"title": "Wrong inventory title"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=inv)
        return httpx.Response(500)

    mc = EbayInventoryMutationClient(s, lambda: "tok", transport=httpx.MockTransport(handler))
    ex = EbayWriteExecutor(s, mutation_client=mc)
    listing = _fixture_l1001()
    item = ReviewQueueItem(
        id="drift",
        listing_id=listing.listing_id,
        listing_title=listing.title,
        current_state_snapshot=listing.model_dump(),
        proposed_action_type=ProposedActionType.UPDATE_TITLE,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={
            "title_before": listing.title,
            "title_after": listing.title + " x",
        },
        confidence=0.9,
        rationale="r",
        expected_impact_90d="x",
        created_at=now,
        status=ReviewStatus.APPROVED,
        dry_run_acknowledged=True,
        compliance_issues=[],
        policy_flags=[],
    )
    r = ex.apply(
        item,
        listing_snapshot=listing.model_dump(),
        idempotency_key="k",
    )
    assert not r.success
    assert not r.retryable
    assert "does not match" in r.user_safe_message.lower()


def test_guarded_apply_idempotency_duplicate_before_put(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp = listing_snapshot_fingerprint(listing)

    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    mc = EbayInventoryMutationClient(s, lambda: "tok", transport=transport)
    ex = EbayWriteExecutor(s, mutation_client=mc)

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    item = _approved_title_item(listing, "gw-idem", fp, now)
    q.add(item)
    key = build_apply_idempotency_key(item)
    ApplyIdempotencyStore(s.apply_idempotency_store_path).record_success(
        idempotency_key=key,
        review_item_id=item.id,
        listing_id=item.listing_id,
    )

    svc = GuardedApplyService(
        settings=s, queue=q, resolve_live_listing=resolve, executor=ex
    )
    res = svc.apply_approved_item(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "idempotency"
    assert called["n"] == 0
