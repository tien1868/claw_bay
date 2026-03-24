from pathlib import Path

import httpx

from ebay_claw.adapters.ebay_readonly_http import ReadOnlyEbayInventoryClient
from ebay_claw.config.settings import Settings


def test_429_then_success(tmp_path: Path):
    n = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"inventoryItems": []})

    transport = httpx.MockTransport(handler)
    s = Settings(
        fixture_path=tmp_path / "f.json",
        ebay_access_token="tok",
        ebay_max_retries=3,
        ebay_base_backoff_sec=0.1,
        policy_log_path=tmp_path / "p.log",
    )
    client = ReadOnlyEbayInventoryClient(s, lambda: "tok", transport=transport)
    out = client.get_json("/sell/inventory/v1/inventory_item", {"limit": "1", "offset": "0"})
    assert out.get("inventoryItems") == []
    assert n["calls"] >= 2
