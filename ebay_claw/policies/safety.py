"""Safety guardrails — reject/warn; structured + text audit; no secrets in logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from ebay_claw.audit.store import AuditLogStore, new_event_id
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.audit import AuditEvent
from ebay_claw.models.domain import (
    ListingRecord,
    PricingAction,
    ProposedActionType,
    ReviewQueueItem,
)
from ebay_claw.policies.safe_inventory_specifics import validate_safe_inventory_specifics_patch
from ebay_claw.policies.title_flaw_guard import title_flaw_disclosure_preserved
from ebay_claw.security.policy_structured import (
    PolicyDecisionRecord,
    append_policy_jsonl,
    safe_rationale,
)
from ebay_claw.security.redaction import redact_string

logger = get_logger(__name__)


@dataclass
class PolicyOutcome:
    allowed: bool
    warnings: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)


class PolicyEngine:
    def __init__(self, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._audit = AuditLogStore(settings=self._s)

    def _log_text(self, msg: str) -> None:
        safe = redact_string(msg)
        line = f"{datetime.now(timezone.utc).isoformat()} {safe}\n"
        try:
            self._s.policy_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._s.policy_log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            logger.warning("Could not write policy log: %s", line.strip())
        logger.info(safe)

    def _structured(
        self,
        item: ReviewQueueItem,
        allowed: bool,
        blocks: List[str],
        warnings: List[str],
    ) -> None:
        rec = PolicyDecisionRecord(
            ts_utc=datetime.now(timezone.utc),
            listing_id=item.listing_id,
            review_item_id=item.id,
            action=item.proposed_action_type.value,
            allowed=allowed,
            blocks=list(blocks),
            warnings=list(warnings),
            confidence=item.confidence,
            rationale_short=safe_rationale(item.rationale),
        )
        try:
            append_policy_jsonl(self._s.policy_structured_log_path, rec)
        except OSError as e:
            logger.warning("Structured policy log failed: %s", e)

    def _audit_policy(
        self,
        item: ReviewQueueItem,
        allowed: bool,
        blocks: List[str],
        warnings: List[str],
    ) -> None:
        try:
            self._audit.append(
                AuditEvent(
                    event_id=new_event_id(),
                    event_type="policy_decision",
                    timestamp_utc=datetime.now(timezone.utc),
                    actor=self._s.default_actor,
                    listing_id=item.listing_id,
                    review_item_id=item.id,
                    decision="allow" if allowed else "block",
                    reason_codes=list(blocks),
                    confidence=item.confidence,
                    snapshot_before=item.current_state_snapshot,
                    snapshot_after=item.before_after_diff,
                    policy_allowed=allowed,
                    policy_blocks=list(blocks),
                    policy_warnings=list(warnings),
                )
            )
        except OSError as e:
            logger.warning("Audit append failed: %s", e)

    def _compute_outcome(
        self,
        item: ReviewQueueItem,
        listing: Optional[ListingRecord],
    ) -> PolicyOutcome:
        warnings: List[str] = []
        blocked: List[str] = []

        if item.proposed_action_type == ProposedActionType.UPDATE_SAFE_SPECIFICS:
            ok, reasons, _norm = validate_safe_inventory_specifics_patch(item.before_after_diff or {})
            if not ok:
                blocked.extend(reasons)

        if item.proposed_action_type == ProposedActionType.UPDATE_ITEM_SPECIFICS:
            diff = item.before_after_diff or {}
            for c in diff.get("corrections") or []:
                if not isinstance(c, dict):
                    continue
                if str(c.get("name") or "").lower() == "brand":
                    if float(c.get("confidence") or 0) < 0.85:
                        blocked.append("brand_change_below_confidence_threshold")
                if str(c.get("name") or "").lower() == "category":
                    if float(c.get("confidence") or 0) < 0.8:
                        blocked.append("category_change_low_confidence")
            for a in diff.get("additions") or []:
                if not isinstance(a, dict):
                    continue
                if str(a.get("name") or "").lower() in ("material", "fabric", "materials"):
                    if float(a.get("confidence") or 0) < 0.88:
                        blocked.append("material_claim_low_confidence_possible_invention")

        if listing:
            if listing.price_amount >= self._s.high_value_price_usd:
                if item.status.value == "pending":
                    warnings.append("high_value_requires_manual_review")

            w = listing.watchers or 0
            if (
                item.proposed_action_type == ProposedActionType.END_AND_SELL_SIMILAR
                and w >= 1
            ):
                blocked.append("end_listing_with_watchers_requires_explicit_approval")
                self._log_text(
                    f"BLOCK listing={item.listing_id} action=end_and_sell_similar watchers={w}"
                )

        if item.proposed_action_type == ProposedActionType.MARKDOWN_LISTING:
            diff = item.before_after_diff or {}
            pct = diff.get("markdown_pct")
            if isinstance(pct, (int, float)) and pct > self._s.max_auto_markdown_pct:
                blocked.append("markdown_exceeds_configured_cap")
                self._log_text(
                    f"BLOCK listing={item.listing_id} markdown_pct={pct} cap={self._s.max_auto_markdown_pct}"
                )

        if item.proposed_action_type == ProposedActionType.UPDATE_TITLE:
            new_t = str((item.before_after_diff or {}).get("title_after", ""))
            old_t = str((item.before_after_diff or {}).get("title_before", ""))
            if not title_flaw_disclosure_preserved(old_t, new_t):
                blocked.append("would_remove_condition_disclosure")
                self._log_text(
                    f"BLOCK listing={item.listing_id} title_strips_flaw_disclosure"
                )

        allowed = len(blocked) == 0
        return PolicyOutcome(allowed=allowed, warnings=warnings, blocked_reasons=blocked)

    def evaluate_review_item(
        self,
        item: ReviewQueueItem,
        listing: Optional[ListingRecord],
    ) -> PolicyOutcome:
        out = self._compute_outcome(item, listing)
        self._structured(item, out.allowed, out.blocked_reasons, out.warnings)
        self._audit_policy(item, out.allowed, out.blocked_reasons, out.warnings)
        self._log_text(
            f"EVAL listing={item.listing_id} allowed={out.allowed} warnings={len(out.warnings)} blocks={len(out.blocked_reasons)}"
        )
        return out

    def must_pass_before_write(
        self,
        item: ReviewQueueItem,
        listing: Optional[ListingRecord],
    ) -> PolicyOutcome:
        """Pre-write re-check — no duplicate structured/audit emit (apply layer logs attempt)."""
        return self._compute_outcome(item, listing)

    def should_warn_premium_early_discount(
        self,
        listing: ListingRecord,
        action: PricingAction,
    ) -> Optional[str]:
        from ebay_claw.analytics.strategy_scoring import _is_premium_brand

        brand = listing.brand or listing.item_specifics.get("Brand")
        if not _is_premium_brand(brand, self._s):
            return None
        if action in (
            PricingAction.MARKDOWN_20,
            PricingAction.MARKDOWN_30,
        ):
            days = listing.extra.get("days_active") if listing.extra else None
            if days is not None and int(days) < 45:
                msg = "premium_early_aggressive_discount"
                self._log_text(f"WARN listing={listing.listing_id} {msg}")
                return msg
        return None
