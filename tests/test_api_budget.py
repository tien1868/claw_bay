from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from ebay_claw.adapters.ebay_readonly_http import ApiCallBudget, ReadOnlyEbayInventoryClient
from ebay_claw.adapters.ebay_rest import EbayInventoryListingAdapter
from ebay_claw.config.settings import Settings
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.services.sync_state import SyncStateStore


def test_budget_exceeded():
    b = ApiCallBudget(2)
    b.consume()
    b.consume()
    with pytest.raises(RuntimeError, match="budget"):
        b.consume()


def test_live_sync_partial_when_budget_exhausted_mid_run(tmp_path: Path):
    """After max API calls, adapter returns partial listings and records partial sync state."""

    def _offer_body(sku: str, listing_id: str):
        return {
            "offers": [
                {
                    "offerId": f"o-{listing_id}",
                    "sku": sku,
                    "status": "PUBLISHED",
                    "format": "FIXED_PRICE",
                    "categoryId": "30120",
                    "listing": {
                        "listingId": listing_id,
                        "listingStatus": "ACTIVE",
                        "soldQuantity": 0,
                    },
                    "pricingSummary": {"price": {"value": "25", "currency": "USD"}},
                    "availableQuantity": 1,
                }
            ]
        }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        q = parse_qs(urlparse(str(request.url)).query)
        if path.endswith("/inventory_item"):
            return httpx.Response(
                200,
                json={
                    "size": 2,
                    "inventoryItems": [
                        {"sku": "A", "product": {"title": "Shirt A"}},
                        {"sku": "B", "product": {"title": "Shirt B"}},
                    ],
                },
            )
        if path.endswith("/offer"):
            sku = (q.get("sku") or [None])[0]
            if sku == "A":
                return httpx.Response(200, json=_offer_body("A", "L100"))
            if sku == "B":
                return httpx.Response(200, json=_offer_body("B", "L200"))
        return httpx.Response(404, json={"errors": [{"message": "nope"}]})

    transport = httpx.MockTransport(handler)
    settings = Settings(
        fixture_path=tmp_path / "f.json",
        review_queue_path=tmp_path / "q.json",
        policy_log_path=tmp_path / "pol.log",
        sync_state_path=tmp_path / "sync.json",
        audit_log_path=tmp_path / "audit.jsonl",
        ebay_access_token="tok",
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        api_budget_max_calls_per_run=2,
        ebay_max_retries=2,
        ebay_base_backoff_sec=0.1,
    )

    budget = ApiCallBudget(settings.api_budget_max_calls_per_run)
    client = ReadOnlyEbayInventoryClient(
        settings,
        lambda: "tok",
        transport=transport,
        budget=budget,
    )
    adapter = EbayInventoryListingAdapter(
        settings,
        sync_store=SyncStateStore(path=tmp_path / "sync.json", settings=settings),
        http_client=client,
    )
    out = adapter.fetch_active_listings()
    assert len(out) == 1
    assert out[0].listing_id == "L100"
    st = SyncStateStore(path=tmp_path / "sync.json", settings=settings).read()
    assert st.partial_sync
    assert st.status == "partial"
