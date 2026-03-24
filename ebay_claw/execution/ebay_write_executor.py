"""Live eBay writes — guarded apply only; supports UPDATE_TITLE (IMPROVE_TITLE queue flow)."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Dict, Optional

from ebay_claw.audit.store import new_event_id
from ebay_claw.logging_config import get_logger
from ebay_claw.models.domain import ApplyResult, ProposedActionType, ReviewQueueItem
from ebay_claw.policies.title_flaw_guard import title_flaw_disclosure_preserved
from ebay_claw.security.write_guard import WriteForbiddenError, assert_write_mutation_allowed

if TYPE_CHECKING:
    from ebay_claw.adapters.ebay_inventory_mutation import EbayInventoryMutationClient
    from ebay_claw.config.settings import Settings

logger = get_logger(__name__)


def ebay_write_executor_fully_enabled(settings: "Settings") -> tuple[bool, str]:
    """All gates required before the real executor may be constructed or used."""
    from ebay_claw.config.settings import Settings as S
    from ebay_claw.models.runtime_mode import ClawRuntimeMode

    s = settings
    if not isinstance(s, S):
        return False, "invalid_settings"
    if s.runtime_mode != ClawRuntimeMode.LIVE_GUARDED_WRITE:
        return False, "runtime_mode_must_be_live_guarded_write"
    if not s.guarded_write_enabled:
        return False, "guarded_write_disabled"
    if not s.execution_enabled:
        return False, "execution_disabled"
    if not s.ebay_real_writes_enabled:
        return False, "ebay_real_writes_disabled"
    if not s.apply_api_allow_live_executor:
        return False, "apply_api_allow_live_executor_false"
    return True, ""


def _norm_title(t: str) -> str:
    return (t or "").strip()[:80].lower()


def _norm_aspect_val(t: str) -> str:
    return (t or "").strip().lower()


def _snap_sku(snap: Optional[Dict[str, Any]]) -> Optional[str]:
    if not snap:
        return None
    v = snap.get("sku")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _snap_offer(snap: Optional[Dict[str, Any]]) -> Optional[str]:
    if not snap:
        return None
    ex = snap.get("extra")
    if not isinstance(ex, dict):
        return None
    v = ex.get("ebay_offer_id")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _failure(
    item: ReviewQueueItem,
    *,
    message: str,
    idempotency_key: str,
    sku: Optional[str],
    offer_id: Optional[str],
    retryable: bool,
    simulated: bool,
    detail: Optional[Dict[str, Any]] = None,
    external_request_id: Optional[str] = None,
) -> ApplyResult:
    cid = new_event_id()
    return ApplyResult(
        listing_id=item.listing_id,
        attempted_action=item.proposed_action_type,
        success=False,
        user_safe_message=message,
        idempotency_key=idempotency_key,
        target_sku=sku,
        target_offer_id=offer_id,
        external_request_id=external_request_id,
        correlation_id=cid,
        retryable=retryable,
        simulated=simulated,
        adapter_detail=detail or {},
        changed_specific_keys=[],
    )


class EbayWriteExecutor:
    """
    Live Inventory API writes behind guarded apply.

    **Supported live actions (only these two):**

    1. ``UPDATE_TITLE`` — ``GET /sell/inventory/v1/inventory_item/{sku}`` then
       ``PUT /sell/inventory/v1/inventory_item/{sku}`` with merged ``product.title``.

    2. ``UPDATE_SAFE_SPECIFICS`` — same GET/PUT endpoints; SKU is required to address the
       inventory item. ``GET`` loads current ``product.aspects``; approved whitelist-only
       keys are merged into aspects (list-of-strings per eBay), then the full inventory
       document is PUT back. Unrelated product fields are preserved via deep-copy merge.

    Pricing **IMPROVE_TITLE** maps to ``UPDATE_TITLE``; safe specifics use
    ``UPDATE_SAFE_SPECIFICS`` with ``before_after_diff.safe_inventory_specifics_patch``.
    """

    def __init__(
        self,
        settings: "Settings",
        *,
        mutation_client: Optional["EbayInventoryMutationClient"] = None,
    ):
        ok, reason = ebay_write_executor_fully_enabled(settings)
        if not ok:
            raise RuntimeError(
                f"EbayWriteExecutor refused: write mode not fully enabled ({reason}). "
                "Set EBAY_CLAW_EBAY_REAL_WRITES_ENABLED, EBAY_CLAW_APPLY_API_ALLOW_LIVE_EXECUTOR, "
                "guarded + execution flags, and LIVE_GUARDED_WRITE runtime."
            )
        self._s = settings
        self._mutation = mutation_client or self._default_mutation_client()

    def _default_mutation_client(self) -> "EbayInventoryMutationClient":
        from ebay_claw.adapters.ebay_inventory_mutation import EbayInventoryMutationClient
        from ebay_claw.adapters.ebay_oauth import recover_inventory_session_after_401

        holder: dict[str, object] = {"t": None}

        def token_getter() -> str:
            t = holder.get("t")
            if t:
                return str(t)
            if self._s.ebay_access_token and self._s.ebay_access_token.strip():
                return self._s.ebay_access_token.strip()
            from ebay_claw.adapters.ebay_oauth import resolve_access_token

            tok = resolve_access_token(self._s)
            holder["t"] = tok
            return tok

        def on_401() -> None:
            recover_inventory_session_after_401(self._s, holder)

        return EbayInventoryMutationClient(
            self._s,
            token_getter,
            on_unauthorized=on_401,
        )

    def apply(
        self,
        item: ReviewQueueItem,
        listing_snapshot: Optional[dict] = None,
        *,
        idempotency_key: str = "",
        legacy_audit: bool = True,
        transition_queue: bool = False,
    ) -> ApplyResult:
        ok, reason = ebay_write_executor_fully_enabled(self._s)
        if not ok:
            return _failure(
                item,
                message=f"eBay write executor not enabled ({reason}).",
                idempotency_key=idempotency_key,
                sku=_snap_sku(listing_snapshot),
                offer_id=_snap_offer(listing_snapshot),
                retryable=False,
                simulated=False,
                detail={"executor": "ebay_write", "fail_closed": True, "reason": reason},
            )

        try:
            assert_write_mutation_allowed(self._s, caller="EbayWriteExecutor.apply")
        except WriteForbiddenError as e:
            return _failure(
                item,
                message=str(e),
                idempotency_key=idempotency_key,
                sku=_snap_sku(listing_snapshot),
                offer_id=_snap_offer(listing_snapshot),
                retryable=False,
                simulated=False,
                detail={"write_guard": True},
            )

        correlation_id = new_event_id()
        sku = _snap_sku(listing_snapshot)
        offer_id = _snap_offer(listing_snapshot)

        if item.proposed_action_type not in (
            ProposedActionType.UPDATE_TITLE,
            ProposedActionType.UPDATE_SAFE_SPECIFICS,
        ):
            return ApplyResult(
                listing_id=item.listing_id,
                attempted_action=item.proposed_action_type,
                success=False,
                user_safe_message=(
                    "Live eBay writes support only update_title and update_safe_specifics for now. "
                    f"Requested action {item.proposed_action_type.value!r} is not enabled."
                ),
                idempotency_key=idempotency_key,
                target_sku=sku,
                target_offer_id=offer_id,
                correlation_id=correlation_id,
                retryable=False,
                simulated=False,
                adapter_detail={
                    "executor": "ebay_write",
                    "unsupported_action": True,
                    "legacy_audit": legacy_audit,
                    "transition_queue": transition_queue,
                },
                changed_specific_keys=[],
            )

        if not listing_snapshot:
            return _failure(
                item,
                message="Missing live listing snapshot — cannot perform inventory write.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"missing_snapshot": True},
            )

        if not sku:
            return _failure(
                item,
                message="SKU is required on the live listing for Inventory API writes.",
                idempotency_key=idempotency_key,
                sku=None,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"missing_sku": True},
            )

        if item.proposed_action_type == ProposedActionType.UPDATE_SAFE_SPECIFICS:
            return self._apply_safe_specifics(
                item,
                listing_snapshot,
                sku=sku,
                offer_id=offer_id,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                legacy_audit=legacy_audit,
                transition_queue=transition_queue,
            )

        diff = item.before_after_diff or {}
        title_before = str(diff.get("title_before", ""))
        title_after = str(diff.get("title_after", "")).strip()
        if not title_after:
            return _failure(
                item,
                message="title_after is empty in the approved queue diff — nothing to publish.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"validation": "empty_title_after"},
            )

        if len(title_after) > 80:
            title_after = title_after[:80]

        if not title_flaw_disclosure_preserved(title_before, title_after):
            return _failure(
                item,
                message="Title change would remove condition/disclosure language — blocked for policy safety.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"policy": "would_remove_condition_disclosure"},
            )

        live_title = str(listing_snapshot.get("title") or "").strip()
        if (
            _norm_title(title_after) == _norm_title(title_before)
            and _norm_title(title_after) == _norm_title(live_title)
        ):
            return ApplyResult(
                listing_id=item.listing_id,
                attempted_action=item.proposed_action_type,
                success=True,
                user_safe_message="Title already matches approved target — no eBay API call needed.",
                idempotency_key=idempotency_key,
                target_sku=sku,
                target_offer_id=offer_id,
                correlation_id=correlation_id,
                retryable=False,
                simulated=False,
                adapter_detail={"executor": "ebay_write", "no_op": True},
                changed_specific_keys=[],
            )

        try:
            inv = self._mutation.get_inventory_item(sku)
        except RuntimeError as e:
            ex_s = str(e).lower()
            retryable = not any(
                x in ex_s
                for x in ("http_status=404", "http_status=400", "http_status=403", "401")
            )
            return _failure(
                item,
                message="Could not load inventory item from eBay — try again after sync, or check SKU.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=retryable,
                simulated=False,
                detail={"phase": "get_inventory_item", "error": str(e)[:300]},
            )

        product = inv.get("product")
        if not isinstance(product, dict):
            return _failure(
                item,
                message="Inventory item has no product payload — cannot update title.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"validation": "missing_product"},
            )

        current_title = str(product.get("title") or "").strip()
        if _norm_title(current_title) != _norm_title(live_title):
            return _failure(
                item,
                message=(
                    "Inventory item title does not match the live listing feed (possible concurrent edit). "
                    "Re-sync and re-approve before applying a title change."
                ),
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={
                    "validation": "inventory_title_drift",
                    "inventory_title": current_title[:120],
                    "expected_live_title": live_title[:120],
                },
            )

        new_body = copy.deepcopy(inv)
        new_product = new_body.get("product")
        assert isinstance(new_product, dict)
        new_product["title"] = title_after

        put = self._mutation.put_inventory_item(sku, new_body)
        if not put.ok:
            return _failure(
                item,
                message=put.user_safe_message,
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=put.retryable,
                simulated=False,
                detail={
                    "executor": "ebay_write",
                    "api": "put_inventory_item",
                    "http_status": put.http_status,
                    "legacy_audit": legacy_audit,
                    "transition_queue": transition_queue,
                    "response_preview": (put.response_body_preview or "")[:180],
                },
                external_request_id=put.external_request_id,
            )

        logger.info(
            "eBay title write ok listing=%s sku=%s http=%s",
            item.listing_id,
            sku,
            put.http_status,
        )
        return ApplyResult(
            listing_id=item.listing_id,
            attempted_action=item.proposed_action_type,
            success=True,
            user_safe_message="Title updated on eBay (inventory item).",
            idempotency_key=idempotency_key,
            target_sku=sku,
            target_offer_id=offer_id,
            external_request_id=put.external_request_id,
            correlation_id=correlation_id,
            retryable=False,
            simulated=False,
            adapter_detail={
                "executor": "ebay_write",
                "api": "put_inventory_item",
                "http_status": put.http_status,
                "legacy_audit": legacy_audit,
                "transition_queue": transition_queue,
            },
            changed_specific_keys=[],
        )

    def _apply_safe_specifics(
        self,
        item: ReviewQueueItem,
        listing_snapshot: dict,
        *,
        sku: str,
        offer_id: Optional[str],
        idempotency_key: str,
        correlation_id: str,
        legacy_audit: bool,
        transition_queue: bool,
    ) -> ApplyResult:
        """
        PUT inventory item after merging only ``UPDATE_SAFE_SPECIFICS`` whitelist aspects.

        Endpoints: ``GET/PUT /sell/inventory/v1/inventory_item/{sku}`` (Sell Inventory API).
        """
        from ebay_claw.policies.safe_inventory_specifics import (
            current_aspect_scalar,
            merge_safe_aspects_into_inventory_body,
            validate_safe_inventory_specifics_patch,
        )

        ok, vreasons, norm = validate_safe_inventory_specifics_patch(item.before_after_diff or {})
        if not ok or not norm:
            return _failure(
                item,
                message="Approved queue item is missing a valid safe_inventory_specifics_patch — cannot apply.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={
                    "executor": "ebay_write",
                    "validation": "safe_specifics_patch",
                    "reason_codes": vreasons,
                },
            )

        aspects_patch: Dict[str, str] = dict(norm["aspects"])
        exp_prior = norm.get("expected_prior_values") or {}

        try:
            inv = self._mutation.get_inventory_item(sku)
        except RuntimeError as e:
            ex_s = str(e).lower()
            retryable = not any(
                x in ex_s
                for x in ("http_status=404", "http_status=400", "http_status=403", "401")
            )
            return _failure(
                item,
                message="Could not load inventory item from eBay — try again after sync, or check SKU.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=retryable,
                simulated=False,
                detail={"phase": "get_inventory_item", "error": str(e)[:300]},
            )

        product = inv.get("product")
        if not isinstance(product, dict):
            return _failure(
                item,
                message="Inventory item has no product payload — cannot update specifics.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"validation": "missing_product"},
            )

        live_title = str(listing_snapshot.get("title") or "").strip()
        current_title = str(product.get("title") or "").strip()
        if _norm_title(current_title) != _norm_title(live_title):
            return _failure(
                item,
                message=(
                    "Inventory item title does not match the live listing feed (stale snapshot). "
                    "Re-sync and re-approve before applying specifics."
                ),
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={
                    "validation": "inventory_title_drift",
                    "inventory_title": current_title[:120],
                    "expected_live_title": live_title[:120],
                },
            )

        raw_aspects = product.get("aspects")
        if not isinstance(raw_aspects, dict):
            raw_aspects = {}

        for ek, ev in exp_prior.items():
            cur = current_aspect_scalar(raw_aspects, ek)
            if cur is None:
                return _failure(
                    item,
                    message="Expected prior aspect value not present on live inventory — "
                    "listing may have changed; re-approve with a fresh snapshot.",
                    idempotency_key=idempotency_key,
                    sku=sku,
                    offer_id=offer_id,
                    retryable=False,
                    simulated=False,
                    detail={
                        "validation": "safe_specifics_merge_conflict",
                        "reason": "expected_prior_missing_on_inventory",
                        "aspect_key": ek[:80],
                    },
                )
            if _norm_aspect_val(str(cur)) != _norm_aspect_val(str(ev)):
                return _failure(
                    item,
                    message="Live inventory aspect differs from approved expected prior — "
                    "possible concurrent edit; re-sync and re-approve.",
                    idempotency_key=idempotency_key,
                    sku=sku,
                    offer_id=offer_id,
                    retryable=False,
                    simulated=False,
                    detail={
                        "validation": "safe_specifics_merge_conflict",
                        "reason": "expected_prior_mismatch",
                        "aspect_key": ek[:80],
                    },
                )

        try:
            new_body, changed = merge_safe_aspects_into_inventory_body(
                inv, patch_aspects=aspects_patch
            )
        except ValueError as e:
            return _failure(
                item,
                message="Could not merge specifics into inventory item — invalid payload.",
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=False,
                simulated=False,
                detail={"validation": "safe_specifics_merge", "error": str(e)[:300]},
            )

        if not changed:
            return ApplyResult(
                listing_id=item.listing_id,
                attempted_action=item.proposed_action_type,
                success=True,
                user_safe_message="Inventory specifics already match approved targets — no eBay API call needed.",
                idempotency_key=idempotency_key,
                target_sku=sku,
                target_offer_id=offer_id,
                correlation_id=correlation_id,
                retryable=False,
                simulated=False,
                adapter_detail={"executor": "ebay_write", "no_op": True, "action": "safe_specifics"},
                changed_specific_keys=[],
            )

        put = self._mutation.put_inventory_item(sku, new_body)
        if not put.ok:
            return _failure(
                item,
                message=put.user_safe_message,
                idempotency_key=idempotency_key,
                sku=sku,
                offer_id=offer_id,
                retryable=put.retryable,
                simulated=False,
                detail={
                    "executor": "ebay_write",
                    "api": "put_inventory_item",
                    "http_status": put.http_status,
                    "legacy_audit": legacy_audit,
                    "transition_queue": transition_queue,
                    "response_preview": (put.response_body_preview or "")[:180],
                },
                external_request_id=put.external_request_id,
            )

        logger.info(
            "eBay safe specifics write ok listing=%s sku=%s keys=%s http=%s",
            item.listing_id,
            sku,
            changed,
            put.http_status,
        )
        return ApplyResult(
            listing_id=item.listing_id,
            attempted_action=item.proposed_action_type,
            success=True,
            user_safe_message="Inventory specifics updated on eBay (whitelist-only merge).",
            idempotency_key=idempotency_key,
            target_sku=sku,
            target_offer_id=offer_id,
            external_request_id=put.external_request_id,
            correlation_id=correlation_id,
            retryable=False,
            simulated=False,
            adapter_detail={
                "executor": "ebay_write",
                "api": "put_inventory_item",
                "http_status": put.http_status,
                "legacy_audit": legacy_audit,
                "transition_queue": transition_queue,
            },
            changed_specific_keys=changed,
        )
