"""Map eBay Inventory + Offer payloads → ListingRecord. No domain leakage of raw dicts upward."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from ebay_claw.models.domain import ListingRecord


def _aspects_to_specifics(aspects: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not aspects:
        return {}
    out: Dict[str, str] = {}
    for k, v in aspects.items():
        if isinstance(v, list):
            out[str(k)] = ", ".join(str(x) for x in v if x is not None)
        elif v is not None:
            out[str(k)] = str(v)
    return out


def _parse_listing_start(val: Optional[str]) -> Tuple[Optional[datetime], Optional[date]]:
    if not val:
        return None, None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt, dt.date()
    except ValueError:
        return None, None


def _pick_price(offer: Dict[str, Any]) -> Tuple[float, str]:
    ps = offer.get("pricingSummary") or {}
    price = ps.get("price") or {}
    val = price.get("value")
    cur = str(price.get("currency") or "USD")
    try:
        return float(val), cur
    except (TypeError, ValueError):
        return 0.0, cur


def _first_brand(specifics: Dict[str, str]) -> Optional[str]:
    for key in ("Brand", "Manufacturer", "Designer"):
        if key in specifics and specifics[key]:
            return specifics[key]
    return None


def _aspect_specific(
    specifics: Dict[str, str],
    *names: str,
) -> Optional[str]:
    for n in names:
        if n in specifics and specifics[n]:
            return specifics[n]
    return None


def merge_inventory_and_offer(
    inventory_item: Dict[str, Any],
    offer: Dict[str, Any],
) -> ListingRecord:
    """
    Build a single ListingRecord from one inventory_item JSON object and one offer JSON object.
    Caller must ensure offer matches inventory SKU and is an active published listing.
    """
    sku = str(inventory_item.get("sku") or offer.get("sku") or "")
    product = inventory_item.get("product") or {}
    title = str(product.get("title") or "").strip()
    description = product.get("description")
    if description is not None:
        description = str(description)

    aspects = product.get("aspects")
    specifics = _aspects_to_specifics(aspects if isinstance(aspects, dict) else None)

    listing_block = offer.get("listing") or {}
    listing_id = str(listing_block.get("listingId") or sku or "unknown")
    if not listing_id or listing_id == "unknown":
        listing_id = f"sku:{sku}" if sku else "unknown"

    if not title:
        title = str(offer.get("listingDescription") or "").strip() or "(no title)"

    price_amount, currency = _pick_price(offer)

    qty = offer.get("availableQuantity")
    if qty is None:
        avail = inventory_item.get("availability") or {}
        st = avail.get("shipToLocationAvailability") or {}
        qty = st.get("quantity")
    quantity = int(qty) if qty is not None else 1

    start_raw = offer.get("listingStartDate") or listing_block.get("listingStartDate")
    listed_at, listed_on = _parse_listing_start(
        str(start_raw) if start_raw else None
    )

    sold_q = listing_block.get("soldQuantity")

    brand = _first_brand(specifics)
    size = _aspect_specific(specifics, "Size", "Waist", "Inseam")
    dept = _aspect_specific(specifics, "Department", "Gender")
    garment = _aspect_specific(specifics, "Type", "Style")
    color = _aspect_specific(specifics, "Color", "Colour")
    material = _aspect_specific(specifics, "Material", "Fabric Type")

    cond_enum = inventory_item.get("condition")
    cond_desc = inventory_item.get("conditionDescription")
    condition_parts: List[str] = []
    if cond_enum:
        condition_parts.append(str(cond_enum))
    if cond_desc:
        condition_parts.append(str(cond_desc))
    condition = " — ".join(condition_parts) if condition_parts else None

    category_id = offer.get("categoryId")
    if category_id is not None:
        category_id = str(category_id)

    extra: Dict[str, Any] = {
        "ebay_offer_id": offer.get("offerId"),
        "ebay_offer_status": offer.get("status"),
        "ebay_listing_status": listing_block.get("listingStatus"),
        "ebay_format": offer.get("format"),
    }

    return ListingRecord(
        listing_id=listing_id,
        title=title[:80],
        sku=sku or None,
        category_id=category_id,
        price_amount=max(0.0, price_amount),
        currency=currency,
        quantity=max(0, quantity),
        listed_at=listed_at,
        listed_on=listed_on,
        watchers=None,
        view_count=None,
        sold_quantity_last_90_days=int(sold_q) if sold_q is not None else None,
        brand=brand,
        size=size,
        department=dept,
        garment_type=garment,
        color=color,
        material=material,
        condition=condition,
        description=description,
        item_specifics=specifics,
        source_payload_ref=f"ebay:inventory:{sku}:offer:{offer.get('offerId') or ''}",
        extra=extra,
    )


def offer_is_active_published(offer: Dict[str, Any]) -> bool:
    if str(offer.get("status") or "").upper() != "PUBLISHED":
        return False
    listing = offer.get("listing") or {}
    st = str(listing.get("listingStatus") or "").upper()
    if st and st != "ACTIVE":
        return False
    if not listing.get("listingId"):
        return False
    return True
