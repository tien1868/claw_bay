from ebay_claw.adapters.ebay_normalize import (
    merge_inventory_and_offer,
    offer_is_active_published,
)


def test_merge_inventory_and_offer():
    inv = {
        "sku": "SKU1",
        "condition": "USED_GOOD",
        "product": {
            "title": "Patagonia Jacket Mens L Navy",
            "description": "Warm layer.",
            "aspects": {"Brand": ["Patagonia"], "Size": ["L"], "Color": ["Navy"]},
        },
    }
    offer = {
        "offerId": "O1",
        "sku": "SKU1",
        "status": "PUBLISHED",
        "format": "FIXED_PRICE",
        "categoryId": "3001",
        "listing": {
            "listingId": "110099",
            "listingStatus": "ACTIVE",
            "soldQuantity": 2,
        },
        "pricingSummary": {"price": {"value": "89.00", "currency": "USD"}},
        "availableQuantity": 1,
        "listingStartDate": "2024-06-01T12:00:00Z",
    }
    rec = merge_inventory_and_offer(inv, offer)
    assert rec.listing_id == "110099"
    assert rec.sku == "SKU1"
    assert rec.price_amount == 89.0
    assert rec.brand == "Patagonia"
    assert rec.size == "L"
    assert "Brand" in rec.item_specifics
    assert rec.sold_quantity_last_90_days == 2


def test_offer_not_active_missing_listing():
    offer = {"status": "PUBLISHED", "listing": {"listingStatus": "ENDED"}}
    assert not offer_is_active_published(offer)


def test_missing_price_defaults_zero():
    inv = {"sku": "S", "product": {"title": "T"}}
    offer = {
        "status": "PUBLISHED",
        "listing": {"listingId": "1", "listingStatus": "ACTIVE"},
        "pricingSummary": {},
    }
    rec = merge_inventory_and_offer(inv, offer)
    assert rec.price_amount == 0.0
    assert rec.title
