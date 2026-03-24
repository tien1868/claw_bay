from datetime import date

from ebay_claw.analytics.inventory_analyst import age_to_bucket, compute_days_active
from ebay_claw.config.settings import Settings
from ebay_claw.models.domain import AgeBucket, ListingRecord


def test_days_active():
    lst = ListingRecord(
        listing_id="a",
        title="x",
        price_amount=1,
        listed_on=date(2025, 1, 1),
    )
    assert compute_days_active(lst, date(2025, 1, 10)) == 9


def test_age_buckets():
    s = Settings()
    assert age_to_bucket(10, s) == AgeBucket.D0_29
    assert age_to_bucket(45, s) == AgeBucket.D30_59
    assert age_to_bucket(200, s) == AgeBucket.D180_PLUS
