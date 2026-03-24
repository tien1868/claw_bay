from datetime import date

from ebay_claw.compliance.boundary import EbayComplianceBoundary
from ebay_claw.config.settings import Settings
from ebay_claw.models.compliance_issue import ComplianceSeverity
from ebay_claw.models.domain import ListingRecord


def test_empty_title_is_blocking_guarded_write():
    s = Settings(compliance_checks_enabled=True)
    b = EbayComplianceBoundary(settings=s)
    lst = ListingRecord(
        listing_id="X",
        title="",
        price_amount=1.0,
        listed_on=date(2025, 1, 1),
    )
    r = b.check_listing(lst)
    blocking = [i for i in r.issues if i.blocks_guarded_write]
    assert blocking
    assert all(i.severity == ComplianceSeverity.BLOCKING for i in blocking)
    assert "guarded write" in r.guarded_write_block_reason().lower()


def test_reasonable_title_has_no_blocking():
    s = Settings(compliance_checks_enabled=True)
    b = EbayComplianceBoundary(settings=s)
    lst = ListingRecord(
        listing_id="X",
        title="Patagonia fleece jacket mens M blue",
        price_amount=1.0,
        listed_on=date(2025, 1, 1),
        description="A" * 50,
    )
    r = b.check_listing(lst)
    assert not any(i.blocks_guarded_write for i in r.issues)


def test_summarize_counts_by_severity():
    s = Settings(compliance_checks_enabled=True)
    b = EbayComplianceBoundary(settings=s)
    rows = [
        ListingRecord(listing_id="1", title="", price_amount=1.0, listed_on=date(2025, 1, 1)),
        ListingRecord(listing_id="2", title="Short", price_amount=1.0, listed_on=date(2025, 1, 1)),
    ]
    results = [b.check_listing(x) for x in rows]
    summ = b.summarize_for_dashboard(results)
    assert summ["blocking_listing_count"] >= 1
    assert "issues_by_code" in summ
