from datetime import date

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.models.domain import ListingRecord


def test_stale_old_listing():
    a = InventoryAnalyst()
    lst = ListingRecord(
        listing_id="x",
        title="short",
        price_amount=20,
        listed_on=date(2024, 1, 1),
        watchers=0,
    )
    out = a.analyze(lst, as_of=date(2025, 6, 1))
    assert out.is_stale
    assert out.days_active > 75
