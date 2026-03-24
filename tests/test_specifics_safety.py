from ebay_claw.agents.specifics_agent import SpecificsAgent
from ebay_claw.models.domain import ListingRecord


def test_no_hallucinated_material():
    lst = ListingRecord(
        listing_id="s",
        title="shirt",
        price_amount=10,
        brand="Gap",
        size="M",
        material=None,
    )
    sp = SpecificsAgent().suggest(lst)
    mats = [x for x in sp.proposed_additions if x.name == "Material"]
    assert mats == []
