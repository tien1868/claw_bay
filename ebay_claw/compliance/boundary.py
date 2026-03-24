"""
Placeholder boundary for eBay Compliance API–style checks before any future write.

Heuristics tuned for less noise: INFO for optional polish, WARNING for policy risk,
BLOCKING only for clear listing integrity problems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ebay_claw.config.settings import Settings

from ebay_claw.models.compliance_issue import ComplianceIssueRecord, ComplianceSeverity
from ebay_claw.models.domain import ListingRecord
from ebay_claw.security.redaction import redact_string


@dataclass
class ComplianceResult:
    listing_id: str
    checked_at_utc: datetime
    issues: List[ComplianceIssueRecord] = field(default_factory=list)
    api_called: bool = False
    note: str = ""

    @property
    def blocking_issues(self) -> List[str]:
        return [i.code for i in self.issues if i.severity == ComplianceSeverity.BLOCKING]

    @property
    def warnings(self) -> List[str]:
        """Non-blocking actionable strings (warning + info) for legacy callers."""
        return [i.message for i in self.issues if i.severity != ComplianceSeverity.BLOCKING]

    @property
    def ok_for_write_preview(self) -> bool:
        return not any(i.blocks_guarded_write for i in self.issues)

    def guarded_write_block_reason(self) -> str:
        blockers = [i for i in self.issues if i.blocks_guarded_write]
        if not blockers:
            return ""
        return "; ".join(f"{i.code}: {i.message}" for i in blockers)


class EbayComplianceBoundary:
    """Integration seam — official APIs only; no scraping or HTML parsing."""

    def __init__(self, settings: Optional["Settings"] = None):
        from ebay_claw.config.settings import get_settings

        self._s = settings or get_settings()

    def check_listing(self, listing: ListingRecord) -> ComplianceResult:
        now = datetime.now(timezone.utc)
        if not self._s.compliance_checks_enabled:
            return ComplianceResult(
                listing_id=listing.listing_id,
                checked_at_utc=now,
                note="compliance_checks_disabled",
            )

        issues: List[ComplianceIssueRecord] = []
        title = (listing.title or "").strip()
        desc = (listing.description or "").strip()
        tlen = len(title)
        dlen = len(desc)

        if tlen == 0:
            issues.append(
                ComplianceIssueRecord(
                    code="listing_title_missing",
                    severity=ComplianceSeverity.BLOCKING,
                    message="Title is empty — listing integrity violation; blocks guarded write.",
                    blocks_guarded_write=True,
                )
            )
        elif tlen < 6:
            issues.append(
                ComplianceIssueRecord(
                    code="listing_title_very_short",
                    severity=ComplianceSeverity.WARNING,
                    message="Title is very short; improve for discovery and policy clarity.",
                    blocks_guarded_write=False,
                )
            )
        elif tlen < 20:
            issues.append(
                ComplianceIssueRecord(
                    code="listing_title_could_be_richer",
                    severity=ComplianceSeverity.INFO,
                    message="Title is short; consider adding garment details for buyer trust.",
                    blocks_guarded_write=False,
                )
            )

        if dlen == 0:
            issues.append(
                ComplianceIssueRecord(
                    code="description_missing",
                    severity=ComplianceSeverity.WARNING,
                    message="No item description — adds buyer-risk and policy scrutiny.",
                    blocks_guarded_write=False,
                )
            )
        elif dlen < 30:
            issues.append(
                ComplianceIssueRecord(
                    code="description_thin",
                    severity=ComplianceSeverity.INFO,
                    message="Description is minimal; optional expansion may improve conversion.",
                    blocks_guarded_write=False,
                )
            )

        cond = (listing.condition or "").lower()
        if cond and "new" in cond and "open box" not in cond and "like new" not in cond:
            desc_l = desc.lower()
            if any(x in desc_l for x in ("wear", "flaw", "hole", "stain", "pilling", "fade")):
                issues.append(
                    ComplianceIssueRecord(
                        code="condition_new_vs_description_wear",
                        severity=ComplianceSeverity.BLOCKING,
                        message='Condition suggests "new" but description mentions wear/damage — '
                        "correct condition or description before any write.",
                        blocks_guarded_write=True,
                    )
                )

        return ComplianceResult(
            listing_id=listing.listing_id,
            checked_at_utc=now,
            issues=issues,
            api_called=False,
            note="placeholder_compliance_no_live_api",
        )

    def summarize_for_dashboard(self, results: List[ComplianceResult]) -> dict:
        n_block = sum(1 for r in results if r.blocking_issues)
        n_warn = sum(
            1 for r in results for i in r.issues if i.severity == ComplianceSeverity.WARNING
        )
        n_info = sum(1 for r in results for i in r.issues if i.severity == ComplianceSeverity.INFO)
        by_code: dict[str, int] = {}
        for r in results:
            for i in r.issues:
                by_code[i.code] = by_code.get(i.code, 0) + 1
        sample_block_reasons = [
            r.guarded_write_block_reason() for r in results if r.guarded_write_block_reason()
        ][:8]
        return {
            "listings_checked": len(results),
            "blocking_listing_count": n_block,
            "warning_signal_count": n_warn,
            "info_signal_count": n_info,
            "issues_by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])[:40]),
            "sample_guarded_write_blockers": sample_block_reasons,
            "note": redact_string("compliance_api_not_connected"),
        }
