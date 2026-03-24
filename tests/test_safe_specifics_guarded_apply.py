"""UPDATE_SAFE_SPECIFICS — policy, merge, guarded apply + executor (mocked HTTP)."""

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
from ebay_claw.policies.safe_inventory_specifics import (
    merge_safe_aspects_into_inventory_body,
    validate_safe_inventory_specifics_patch,
)
from ebay_claw.policies.safety import PolicyEngine
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


def _safe_patch_item(
    listing: ListingRecord, item_id: str, fp: str, now: datetime, **patch_kw
) -> ReviewQueueItem:
    patch = {
        "version": 1,
        "aspects": {"Department": "Men"},
        "per_key_confidence": {"Department": 0.92},
    }
    patch.update(patch_kw)
    return ReviewQueueItem(
        id=item_id,
        listing_id=listing.listing_id,
        listing_title=listing.title,
        current_state_snapshot=listing.model_dump(),
        proposed_action_type=ProposedActionType.UPDATE_SAFE_SPECIFICS,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={
            "safe_inventory_specifics_patch": patch,
            "additions": [],
            "corrections": [],
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


def test_validate_patch_rejects_brand_and_empty_aspects():
    ok, reasons, _ = validate_safe_inventory_specifics_patch(
        {
            "safe_inventory_specifics_patch": {
                "version": 1,
                "aspects": {"Brand": "X"},
                "per_key_confidence": {"Brand": 0.99},
            }
        }
    )
    assert not ok
    assert any("blocked" in r for r in reasons)

    ok2, reasons2, _ = validate_safe_inventory_specifics_patch(
        {"safe_inventory_specifics_patch": {"version": 1, "aspects": {}, "per_key_confidence": {}}}
    )
    assert not ok2
    assert reasons2


def test_merge_preserves_unrelated_aspects():
    inv = {
        "sku": "S1",
        "product": {
            "title": "T",
            "aspects": {"Brand": ["Patagonia"], "Department": ["Unisex"], "Size": ["S"]},
        },
    }
    body, changed = merge_safe_aspects_into_inventory_body(
        inv, patch_aspects={"Department": "Men", "Size": "M"}
    )
    assert "Brand" in body["product"]["aspects"]
    assert body["product"]["aspects"]["Brand"] == ["Patagonia"]
    assert body["product"]["aspects"]["Department"] == ["Men"]
    assert set(changed) >= {"Department", "Size"}


def test_policy_blocks_low_confidence_safe_specifics(tmp_path: Path):
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
        proposed_action_type=ProposedActionType.UPDATE_SAFE_SPECIFICS,
        recommended_strategy=StrategicPath.FAST_MOVE,
        before_after_diff={
            "safe_inventory_specifics_patch": {
                "version": 1,
                "aspects": {"Department": "Men"},
                "per_key_confidence": {"Department": 0.5},
            }
        },
        confidence=0.9,
        rationale="r",
        expected_impact_90d="x",
        created_at=datetime.now(timezone.utc),
        status=ReviewStatus.PENDING,
    )
    out = eng.evaluate_review_item(item, ListingRecord(listing_id="x", title="t", price_amount=1.0))
    assert not out.allowed


def test_guarded_apply_safe_specifics_success_merges_put(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp = listing_snapshot_fingerprint(listing)

    captured: dict = {}

    inv = {
        "sku": "SKU-L1001",
        "product": {
            "title": "nice jacket mens L",
            "aspects": {"Brand": ["Patagonia"], "Department": ["Unisex"], "Size": ["M"]},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "inventory_item" in str(request.url):
            return httpx.Response(200, json=inv)
        if request.method == "PUT" and "inventory_item" in str(request.url):
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(204)
        return httpx.Response(404, text="unexpected")

    transport = httpx.MockTransport(handler)
    mc = EbayInventoryMutationClient(s, lambda: "tok", transport=transport)
    ex = EbayWriteExecutor(s, mutation_client=mc)

    item = _safe_patch_item(listing, "ss-ok", fp, now)
    q.add(item)

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    svc = GuardedApplyService(settings=s, queue=q, resolve_live_listing=resolve, executor=ex)
    res = svc.apply_approved_item(item.id, actor="op")
    assert res.ok
    assert res.execution
    assert res.execution.success
    assert res.execution.changed_specific_keys
    assert "Department" in res.execution.changed_specific_keys
    assert q.get(item.id).status == ReviewStatus.APPLIED
    put_body = captured["body"]
    assert put_body["product"]["aspects"]["Brand"] == ["Patagonia"]
    assert put_body["product"]["aspects"]["Department"] == ["Men"]


def test_guarded_apply_malformed_patch_policy_block(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp = listing_snapshot_fingerprint(listing)

    item = _safe_patch_item(listing, "ss-bad", fp, now)
    item = item.model_copy(
        update={"before_after_diff": {"safe_inventory_specifics_patch": {"version": 99}}}
    )
    q.add(item)

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    ex = EbayWriteExecutor(
        s,
        mutation_client=EbayInventoryMutationClient(
            s, lambda: "t", transport=httpx.MockTransport(lambda r: httpx.Response(404))
        ),
    )
    svc = GuardedApplyService(settings=s, queue=q, resolve_live_listing=resolve, executor=ex)
    res = svc.apply_approved_item(item.id, actor="op")
    assert not res.ok
    assert res.blocked_stage == "policy"
    assert q.get(item.id).status == ReviewStatus.APPROVED


def test_success_records_idempotency_prevents_duplicate_apply(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    q = ReviewQueueStore(settings=s)
    listing = _fixture_l1001()
    fp = listing_snapshot_fingerprint(listing)

    inv = {
        "sku": "SKU-L1001",
        "product": {"title": "nice jacket mens L", "aspects": {"Department": ["Unisex"]}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=inv)
        if request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    mc = EbayInventoryMutationClient(s, lambda: "tok", transport=transport)
    ex = EbayWriteExecutor(s, mutation_client=mc)

    item = _safe_patch_item(listing, "ss-idem", fp, now)
    q.add(item)

    def resolve(lid: str) -> ListingRecord | None:
        return listing if lid == listing.listing_id else None

    svc = GuardedApplyService(settings=s, queue=q, resolve_live_listing=resolve, executor=ex)
    res1 = svc.apply_approved_item(item.id, actor="op")
    assert res1.ok
    key = build_apply_idempotency_key(item)
    idem = ApplyIdempotencyStore(s.apply_idempotency_store_path)
    assert idem.has_successful_apply(key)
    res2 = svc.apply_approved_item(item.id, actor="op")
    assert not res2.ok
    assert res2.blocked_stage == "state_machine"


def test_executor_title_drift_blocks_specifics(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    listing = _fixture_l1001()
    inv = {
        "sku": "SKU-L1001",
        "product": {"title": "different title", "aspects": {"Department": ["Unisex"]}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=inv)
        return httpx.Response(404)

    ex = EbayWriteExecutor(
        s,
        mutation_client=EbayInventoryMutationClient(
            s, lambda: "t", transport=httpx.MockTransport(handler)
        ),
    )
    item = _safe_patch_item(listing, "drift", listing_snapshot_fingerprint(listing), now)
    snap = listing.model_dump()
    res = ex.apply(item, listing_snapshot=snap, idempotency_key="k", legacy_audit=False)
    assert not res.success
    assert res.adapter_detail.get("validation") == "inventory_title_drift"


def test_apply_result_shape_includes_changed_keys(tmp_path: Path):
    now = datetime.now(timezone.utc)
    s = _write_settings(tmp_path)
    listing = _fixture_l1001()
    inv = {
        "sku": "SKU-L1001",
        "product": {"title": listing.title, "aspects": {"Department": ["Unisex"]}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=inv)
        if request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(404)

    ex = EbayWriteExecutor(
        s,
        mutation_client=EbayInventoryMutationClient(
            s, lambda: "t", transport=httpx.MockTransport(handler)
        ),
    )
    item = _safe_patch_item(listing, "shape", listing_snapshot_fingerprint(listing), now)
    res = ex.apply(
        item, listing_snapshot=listing.model_dump(), idempotency_key="k", legacy_audit=False
    )
    assert res.success
    assert isinstance(res.changed_specific_keys, list)

