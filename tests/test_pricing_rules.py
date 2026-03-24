from datetime import date

from ebay_claw.agents.pricing_agent import PricingAgent
from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.models.domain import ListingRecord, PricingAction, StrategicPath


def test_end_and_sell_similar():
    a = InventoryAnalyst()
    lst = ListingRecord(
        listing_id="e",
        title="old thing",
        price_amount=10,
        listed_on=date(2023, 1, 1),
        watchers=0,
    )
    an = a.analyze(lst, as_of=date(2025, 6, 1))
    pr = PricingAgent().recommend(lst, an, StrategicPath.END_AND_SELL_SIMILAR)
    assert pr.recommended_action == PricingAction.END_AND_SELL_SIMILAR
