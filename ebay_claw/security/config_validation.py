"""Validate configuration — fail-closed rules for live modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ebay_claw.adapters.ebay_oauth import live_credentials_configured
from ebay_claw.config.settings import Settings
from ebay_claw.models.runtime_mode import ClawRuntimeMode


@dataclass
class ValidationReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def validate_settings(s: Settings) -> ValidationReport:
    r = ValidationReport()

    if s.runtime_mode in (
        ClawRuntimeMode.LIVE_READ_ONLY,
        ClawRuntimeMode.LIVE_GUARDED_WRITE,
    ):
        if not live_credentials_configured(s):
            r.errors.append(
                "runtime_mode is live_read_only or live_guarded_write but eBay OAuth is incomplete "
                "(fail-closed: configure tokens or use runtime_mode=fixture)."
            )

    if s.runtime_mode == ClawRuntimeMode.LIVE_GUARDED_WRITE and not s.guarded_write_enabled:
        r.warnings.append(
            "live_guarded_write with GUARDED_WRITE_ENABLED=false — all mutations remain blocked until enabled."
        )

    if s.guarded_write_enabled and s.runtime_mode != ClawRuntimeMode.LIVE_GUARDED_WRITE:
        r.warnings.append(
            "GUARDED_WRITE_ENABLED without runtime_mode=live_guarded_write — write guard will block mutations."
        )

    if s.live_write_expansion_success_rate_advisory_floor <= s.live_write_expansion_success_rate_critical_below:
        r.warnings.append(
            "live_write_expansion_success_rate_advisory_floor should be above "
            "live_write_expansion_success_rate_critical_below so tiers stay ordered."
        )
    if s.live_write_expansion_success_rate_ready_floor <= s.live_write_expansion_success_rate_advisory_floor:
        r.warnings.append(
            "live_write_expansion_success_rate_ready_floor should be above "
            "live_write_expansion_success_rate_advisory_floor for sensible ready vs advisory behavior."
        )

    return r


def validate_settings_or_raise(s: Settings) -> None:
    report = validate_settings(s)
    if not report.ok:
        raise ValueError("Configuration invalid:\n" + "\n".join(report.errors))
