from datetime import date

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.models.domain import ListingRecord, StrategicPath


def test_premium_patience():
    a = InventoryAnalyst()
    s = StrategyScorer()
    lst = ListingRecord(
        listing_id="p",
        title="Patagonia Better Sweater Fleece Jacket Mens L Navy Full Zip",
        price_amount=90,
        listed_on=date(2025, 11, 1),
        brand="Patagonia",
        size="L",
        department="Men",
        garment_type="Jacket",
        watchers=3,
        item_specifics={"Brand": "Patagonia"},
    )
    an = a.analyze(lst, as_of=date(2025, 12, 1))
    sc = s.score(lst, an, as_of=date(2025, 12, 1))
    assert sc.recommended_strategy == StrategicPath.PREMIUM_PATIENCE
