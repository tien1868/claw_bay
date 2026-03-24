from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from ebay_claw.adapters.ebay_readonly_http import ReadOnlyEbayInventoryClient
from ebay_claw.adapters.ebay_rest import EbayInventoryListingAdapter
from ebay_claw.config.settings import Settings
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.services.sync_state import SyncStateStore


def _inv_page(offset_marker: int, items: list, size: int = 2):
    return {
        "size": size,
        "inventoryItems": items,
        "offset": str(offset_marker),
    }


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


def test_pagination_fetches_second_inventory_page(tmp_path: Path):
    page_calls = {"inventory": 0, "offer": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        q = parse_qs(urlparse(str(request.url)).query)
        if path.endswith("/inventory_item"):
            page_calls["inventory"] += 1
            off = (q.get("offset") or ["0"])[0]
            if off == "0":
                return httpx.Response(
                    200,
                    json=_inv_page(
                        0,
                        [{"sku": "A", "product": {"title": "Shirt A"}}],
                        size=2,
                    ),
                )
            if off == "1":
                return httpx.Response(
                    200,
                    json=_inv_page(1, [{"sku": "B", "product": {"title": "Shirt B"}}], size=2),
                )
            return httpx.Response(200, json=_inv_page(int(off), [], size=2))
        if path.endswith("/offer"):
            page_calls["offer"] += 1
            sku = (q.get("sku") or [None])[0]
            if sku == "A":
                return httpx.Response(200, json=_offer_body("A", "L100"))
            if sku == "B":
                return httpx.Response(200, json=_offer_body("B", "L200"))
        return httpx.Response(404, json={"errors": [{"message": "not found"}]})

    transport = httpx.MockTransport(handler)
    settings = Settings(
        fixture_path=tmp_path / "f.json",
        review_queue_path=tmp_path / "q.json",
        policy_log_path=tmp_path / "pol.log",
        sync_state_path=tmp_path / "sync.json",
        ebay_client_id="c",
        ebay_client_secret="s",
        ebay_access_token="tok",
        ebay_inventory_page_size=1,
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
    )

    def tok():
        return "tok"

    client = ReadOnlyEbayInventoryClient(settings, tok, transport=transport)
    adapter = EbayInventoryListingAdapter(
        settings,
        sync_store=SyncStateStore(path=tmp_path / "sync.json", settings=settings),
        http_client=client,
    )

    listings = adapter.fetch_active_listings()
    assert len(listings) == 2
    assert {x.listing_id for x in listings} == {"L100", "L200"}
    assert page_calls["inventory"] == 2
    assert page_calls["offer"] == 2
