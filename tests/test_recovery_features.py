"""Operator recovery: velocity, daily rank, relist, price-to-sell, bundles — read-only."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ebay_claw.adapters.mock_json import raw_dict_to_listing
from ebay_claw.analytics.bundle_identifier import identify_bundle_candidates
from ebay_claw.analytics.price_to_sell import compute_price_to_sell
from ebay_claw.analytics.relist_accelerator import (
    build_relist_proposal,
    is_relist_candidate,
)
from ebay_claw.analytics.velocity_metrics import compute_velocity_metrics
from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.analytics.strategy_scoring import StrategyScorer
from ebay_claw.config.settings import Settings
from ebay_claw.models.comps import MarketCompSummary, MarketPricePosition
from ebay_claw.models.domain import ListingRecord, StrategicPath
from ebay_claw.models.recovery import ConfidenceBand
from ebay_claw.services.daily_priority_actions import build_daily_priority_actions


def _load_sample() -> list:
    data = json.loads(
        Path("fixtures/sample_listings.json").read_text(encoding="utf-8")
    )
    return [raw_dict_to_listing(x) for x in data["listings"]]


def test_velocity_metrics_counts_created_window():
    listings = _load_sample()
    vm = compute_velocity_metrics(listings, as_of=date(2026, 3, 1))
    assert vm.listings_created_last_7d >= 0
    assert vm.stale_inventory_count >= 0
    notes = " ".join(vm.trend_notes).lower()
    assert "estimated" in notes or "proxy" in notes or "sparse" in notes


def test_daily_priority_actions_sorted_by_score():
    listings = _load_sample()
    analyst = InventoryAnalyst()

    def enriched(lst: ListingRecord):
        return analyst.analyze(lst, as_of=date(2026, 3, 1))

    actions = build_daily_priority_actions(
        listings,
        enriched_fn=enriched,
        as_of=date(2026, 3, 1),
        top_n=5,
    )
    assert len(actions) <= 5
    scores = [a.score for a in actions]
    assert scores == sorted(scores, reverse=True)


def test_relist_candidate_long_age_low_engagement():
    analyst = InventoryAnalyst()
    scorer = StrategyScorer()
    lst = ListingRecord(
        listing_id="X1",
        title="Test Garment Mens M Blue Cotton Shirt Long Sleeve Everyday",
        price_amount=30.0,
        listed_on=date(2025, 8, 1),
        brand="Gap",
        size="M",
        department="Men",
        garment_type="Shirt",
        watchers=0,
        item_specifics={"Brand": "Gap"},
    )
    a = analyst.analyze(lst, as_of=date(2026, 3, 1))
    sc = scorer.score(lst, a, as_of=date(2026, 3, 1))
    assert is_relist_candidate(lst, a, sc)
    rp = build_relist_proposal(lst, a, sc)
    assert rp.suggested_refreshed_title
    assert rp.why_relist_vs_markdown_hold_bundle


def test_price_to_sell_directional_when_low_confidence():
    analyst = InventoryAnalyst()
    lst = _load_sample()[0]
    m = MarketCompSummary(
        median_sold_price=40.0,
        comp_count=1,
        recency_window_days=90,
        price_position=MarketPricePosition.ABOVE_MARKET,
        comp_match_confidence=0.28,
        pct_vs_median=12.0,
        comps_data_source="fixture",
    )
    a = analyst.analyze(lst, as_of=date(2026, 3, 1), market_summary=m)
    pts = compute_price_to_sell(lst, a)
    assert pts.is_directional_only is True
    assert pts.confidence_band == ConfidenceBand.LOW
    assert pts.caution_note


def test_price_to_sell_numeric_when_strong_comps():
    analyst = InventoryAnalyst()
    lst = _load_sample()[0]
    m = MarketCompSummary(
        median_sold_price=45.0,
        comp_count=4,
        recency_window_days=90,
        price_position=MarketPricePosition.AT_MARKET,
        comp_match_confidence=0.55,
        pct_vs_median=0.0,
        comps_data_source="fixture",
    )
    a = analyst.analyze(lst, as_of=date(2026, 3, 1), market_summary=m)
    pts = compute_price_to_sell(lst, a)
    assert pts.is_directional_only is False
    assert pts.recommended_range_low is not None
    assert pts.recommended_range_high is not None


def test_bundle_identifier_finds_low_asp_cluster():
    listings = [
        ListingRecord(
            listing_id=f"B{i}",
            title="Basic tee shirt mens M black",
            price_amount=12.0,
            listed_on=date.today() - timedelta(days=70),
            brand="Uni",
            size="M",
            department="Men",
            garment_type="T-Shirt",
            watchers=0,
            item_specifics={"Brand": "Uni", "Size": "M"},
        )
        for i in range(3)
    ]
    bundles = identify_bundle_candidates(listings, as_of=date.today(), max_asp=40.0, min_age_days=60)
    assert len(bundles) >= 1
    assert len(bundles[0].listing_ids) >= 2
