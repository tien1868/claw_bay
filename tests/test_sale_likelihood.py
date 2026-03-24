from datetime import date

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.models.domain import ListingRecord


def test_sale_likelihood_bounded():
    a = InventoryAnalyst()
    s = StrategyScorer()
    lst = ListingRecord(
        listing_id="q",
        title="Some Brand Mens Jeans 32 Blue Denim Straight Leg",
        price_amount=40,
        listed_on=date(2025, 9, 1),
        brand="Gap",
        size="32",
        department="Men",
        garment_type="Jeans",
    )
    an = a.analyze(lst, as_of=date(2025, 12, 1))
    sc = s.score(lst, an, as_of=date(2025, 12, 1))
    assert 0.0 <= sc.sale_likelihood_before_90_days <= 1.0
