from datetime import date
from pathlib import Path

from ebay_claw.adapters.comps_fixture import FixtureSoldCompsAdapter, normalize_comp_row
from ebay_claw.config.settings import Settings
from ebay_claw.models.comps import MarketPricePosition
from ebay_claw.models.domain import ListingRecord
from ebay_claw.services.comp_market import summarize_sold_comps


def test_normalize_comp_row_roundtrip():
    r = normalize_comp_row(
        {"sold_price": 22.5, "sold_date": "2025-06-01", "currency": "USD", "match_quality": 0.8}
    )
    assert r is not None
    assert r.sold_price == 22.5
    assert r.sold_date == date(2025, 6, 1)
    assert r.match_quality == 0.8


def test_normalize_rejects_bad_date():
    assert normalize_comp_row({"sold_price": 10, "sold_date": "nope"}) is None


def test_fixture_adapter_merges_listing_and_group(tmp_path):
    p = tmp_path / "comps.json"
    p.write_text(
        """{
  "listing_comps": {"L1": [{"sold_price": 10, "sold_date": "2025-01-01", "match_quality": 0.9}]},
  "group_comps": {"acme|tee|m": [{"sold_price": 12, "sold_date": "2025-02-01", "match_quality": 0.7}]}
}""",
        encoding="utf-8",
    )
    s = Settings(comps_fixture_path=p)
    ad = FixtureSoldCompsAdapter(p, settings=s)
    lst = ListingRecord(
        listing_id="L1",
        title="x",
        price_amount=15.0,
        listed_on=date(2025, 1, 1),
        brand="Acme",
        garment_type="Tee",
        size="M",
    )
    comps = ad.fetch_comps_for_listing(lst)
    assert len(comps) == 2


def test_summarize_median_and_position_above():
    s = Settings(comps_recency_default_days=90)
    lst = ListingRecord(
        listing_id="x",
        title="t",
        price_amount=50.0,
        listed_on=date(2025, 1, 1),
    )
    from ebay_claw.models.comps import SoldCompRecord

    comps = [
        SoldCompRecord(sold_price=30.0, sold_date=date(2025, 11, 1), match_quality=0.85),
        SoldCompRecord(sold_price=32.0, sold_date=date(2025, 11, 15), match_quality=0.85),
    ]
    m = summarize_sold_comps(lst, comps, date(2025, 12, 1), s)
    assert m.median_sold_price == 31.0
    assert m.price_position == MarketPricePosition.ABOVE_MARKET
    assert m.comp_count == 2


def test_project_fixture_patagonia_above_median():
    root = Path(__file__).resolve().parents[1]
    fx = root / "fixtures" / "sold_comps.json"
    if not fx.exists():
        return
    s = Settings(comps_fixture_path=fx, comps_enabled=True)
    ad = FixtureSoldCompsAdapter(fx, settings=s)
    lst = ListingRecord(
        listing_id="L1001",
        title="nice jacket mens L",
        price_amount=45.0,
        listed_on=date(2025, 12, 1),
        brand="Patagonia",
        size="L",
        garment_type="Jacket",
    )
    raw = ad.fetch_comps_for_listing(lst)
    m = summarize_sold_comps(lst, raw, date(2026, 1, 15), s)
    assert m.comp_count >= 2
    assert m.median_sold_price is not None
    assert m.price_position == MarketPricePosition.ABOVE_MARKET
