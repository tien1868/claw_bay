"""
Low-risk inventory specifics (aspects) — whitelist validation for ``UPDATE_SAFE_SPECIFICS`` only.

Read-only policy aid + executor pre-check: not a marketplace gate on its own.

**In scope for this pass (whitelist):** department, size, color, garment type, sleeve length,
closure — only when confidence meets ``MIN_PER_KEY_CONFIDENCE`` and keys are not blocked.

**Explicitly out of scope:** brand, category, condition, material/fabric, measurements,
title, price, MPN/UPC, or any key not in the whitelist (fail-closed).

Queue contract — ``before_after_diff["safe_inventory_specifics_patch"]``::

    {
      "version": 1,
      "aspects": {"Department": "Men", "Size": "M"},
      "per_key_confidence": {"Department": 0.92, "Size": 0.88},
      "expected_prior_values": {"Department": "Unisex"}   // optional drift guard
    }
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

PATCH_VERSION = 1
PATCH_KEY = "safe_inventory_specifics_patch"
MIN_PER_KEY_CONFIDENCE = 0.85

# Normalized (lowercase, collapsed space) allowed aspect names for the safe pass.
SAFE_ASPECT_KEYS_WHITELIST_NORMALIZED = frozenset(
    {
        "department",
        "size",
        "color",
        "garment type",
        "sleeve length",
        "closure",
    }
)

# Always blocked for this pass (normalized names).
BLOCKED_ASPECT_KEYS_NORMALIZED = frozenset(
    {
        "brand",
        "material",
        "fabric",
        "materials",
        "condition",
        "category",
        "categories",
        "title",
        "price",
        "mpn",
        "upc",
        "measurements",
        "inseam",
        "waist",
    }
)


def normalize_aspect_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def validate_safe_inventory_specifics_patch(
    diff: Optional[Mapping[str, Any]],
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """
    Validate ``safe_inventory_specifics_patch`` on a queue diff.

    Returns (ok, blocked_reason_codes, normalized_payload_or_none).
    ``normalized_payload`` includes: aspects (display keys), per_key_confidence,
    expected_prior_values (optional).
    """
    if not diff:
        return False, ["safe_specifics_missing_diff"], None
    raw = diff.get(PATCH_KEY)
    if raw is None:
        return False, ["safe_specifics_patch_missing"], None
    if not isinstance(raw, dict):
        return False, ["safe_specifics_patch_not_object"], None

    ver = raw.get("version")
    if ver != PATCH_VERSION:
        return False, [f"safe_specifics_unsupported_version:{ver!r}"], None

    aspects = raw.get("aspects")
    if not isinstance(aspects, dict) or not aspects:
        return False, ["safe_specifics_empty_aspects"], None

    conf_map = raw.get("per_key_confidence")
    if not isinstance(conf_map, dict):
        return False, ["safe_specifics_confidence_not_object"], None

    prior_vals = raw.get("expected_prior_values")
    if prior_vals is not None and not isinstance(prior_vals, dict):
        return False, ["safe_specifics_expected_prior_not_object"], None

    blocked: List[str] = []
    norm_aspects: Dict[str, str] = {}
    norm_conf: Dict[str, float] = {}

    for k, v in aspects.items():
        if not isinstance(k, str) or not k.strip():
            blocked.append("safe_specifics_invalid_aspect_key")
            continue
        nv = normalize_aspect_name(k)
        if nv in BLOCKED_ASPECT_KEYS_NORMALIZED:
            blocked.append(f"safe_specifics_blocked_key:{nv}")
            continue
        if nv not in SAFE_ASPECT_KEYS_WHITELIST_NORMALIZED:
            blocked.append(f"safe_specifics_key_not_whitelisted:{nv}")
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            blocked.append(f"safe_specifics_empty_value:{nv}")
            continue
        str_val = str(v).strip()
        if len(str_val) > 240:
            blocked.append(f"safe_specifics_value_too_long:{nv}")

        c_raw = conf_map.get(k)
        if c_raw is None:
            c_raw = conf_map.get(k.strip())
        if c_raw is None:
            blocked.append(f"safe_specifics_missing_confidence:{nv}")
            continue
        try:
            c = float(c_raw)
        except (TypeError, ValueError):
            blocked.append(f"safe_specifics_invalid_confidence:{nv}")
            continue
        if c < MIN_PER_KEY_CONFIDENCE:
            blocked.append(f"safe_specifics_low_confidence:{nv}")

        norm_aspects[k.strip()] = str_val
        norm_conf[k.strip()] = c

    if blocked:
        return False, blocked, None

    out: Dict[str, Any] = {
        "version": PATCH_VERSION,
        "aspects": norm_aspects,
        "per_key_confidence": norm_conf,
    }
    if prior_vals:
        out["expected_prior_values"] = {str(a).strip(): str(b).strip() for a, b in prior_vals.items()}

    return True, [], out


def merge_safe_aspects_into_inventory_body(
    inventory_item: Dict[str, Any],
    *,
    patch_aspects: Mapping[str, str],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Deep-copy ``inventory_item`` and merge only patch aspect keys into ``product.aspects``.

    eBay Inventory API uses ``product.aspects`` as a map of localized name -> list of strings.
    Keys are matched case-insensitively to avoid duplicating aspect names.

    Returns (new_body, changed_keys) where changed_keys uses the canonical key name from inventory.
    """
    import copy

    body = copy.deepcopy(inventory_item)
    product = body.get("product")
    if not isinstance(product, dict):
        raise ValueError("missing_product")

    aspects = product.get("aspects")
    if aspects is None:
        aspects = {}
        product["aspects"] = aspects
    if not isinstance(aspects, dict):
        raise ValueError("aspects_not_object")

    def _resolve_key(inv: Mapping[str, Any], want: str) -> str:
        wn = normalize_aspect_name(want)
        for ek in inv.keys():
            if isinstance(ek, str) and normalize_aspect_name(ek) == wn:
                return ek
        return want.strip()

    changed: List[str] = []
    for pk, val in patch_aspects.items():
        canon = _resolve_key(aspects, pk)
        new_list = [str(val).strip()]
        cur = aspects.get(canon)
        if isinstance(cur, list) and cur == new_list:
            continue
        aspects[canon] = new_list
        changed.append(canon)

    return body, changed


def safe_inventory_patch_from_specifics_suggestion(spec_s: Any) -> Optional[Dict[str, Any]]:
    """
    Build ``safe_inventory_specifics_patch`` from agent output — only whitelist keys,
    min confidence, propose_add / propose_correct with non-empty proposed_value.
    """
    from ebay_claw.models.domain import SpecificsFieldOp

    aspects: Dict[str, str] = {}
    conf: Dict[str, float] = {}

    def _consider(sug: Any, *, is_correction: bool) -> None:
        name = str(sug.name or "").strip()
        if not name:
            return
        nv = normalize_aspect_name(name)
        if nv in BLOCKED_ASPECT_KEYS_NORMALIZED or nv not in SAFE_ASPECT_KEYS_WHITELIST_NORMALIZED:
            return
        pv = sug.proposed_value
        if pv is None or (isinstance(pv, str) and not str(pv).strip()):
            return
        if float(sug.confidence or 0.0) < MIN_PER_KEY_CONFIDENCE:
            return
        if is_correction and sug.operation != SpecificsFieldOp.PROPOSE_CORRECT:
            return
        if not is_correction and sug.operation != SpecificsFieldOp.PROPOSE_ADD:
            return
        aspects[name] = str(pv).strip()
        conf[name] = float(sug.confidence)

    for a in spec_s.proposed_additions:
        _consider(a, is_correction=False)
    for c in spec_s.proposed_corrections:
        _consider(c, is_correction=True)

    if not aspects:
        return None
    return {
        "version": PATCH_VERSION,
        "aspects": aspects,
        "per_key_confidence": conf,
    }


def current_aspect_scalar(aspects: Mapping[str, Any], key_hint: str) -> Optional[str]:
    """Best-effort single string for an aspect (first value) for drift checks."""
    ck = None
    wn = normalize_aspect_name(key_hint)
    for ek in aspects.keys():
        if isinstance(ek, str) and normalize_aspect_name(ek) == wn:
            ck = ek
            break
    if ck is None:
        return None
    raw = aspects.get(ck)
    if isinstance(raw, list) and raw:
        return str(raw[0]).strip()
    if isinstance(raw, str):
        return raw.strip()
    return None
