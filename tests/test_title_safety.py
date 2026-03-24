from ebay_claw.agents.title_agent import TitleAgent, build_deterministic_title
from ebay_claw.models.domain import ListingRecord


def test_preserves_flaw_disclosure():
    lst = ListingRecord(
        listing_id="f",
        title="wool sweater damage hole disclosed",
        price_amount=15,
        brand="J.Crew",
        size="M",
        department="Men",
        garment_type="Sweater",
        condition="Pre-owned - Fair — hole near hem",
    )
    t = build_deterministic_title(lst)
    assert "Flaws" in t or "flaw" in t.lower() or "hole" in t.lower()


def test_title_length():
    lst = ListingRecord(
        listing_id="f",
        title="x",
        price_amount=1,
        brand="A" * 40,
        garment_type="Jacket",
        size="M",
    )
    t = build_deterministic_title(lst)
    assert len(t) <= 80
