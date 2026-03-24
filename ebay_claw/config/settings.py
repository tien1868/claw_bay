"""Central configuration — EBAY_CLAW_* env. Runtime mode is canonical (UI cannot override)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ebay_claw.models.runtime_mode import ClawRuntimeMode

_log = logging.getLogger("ebay_claw.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EBAY_CLAW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: Canonical server-side mode. Streamlit and other UIs must not change this at runtime.
    runtime_mode: ClawRuntimeMode = Field(default=ClawRuntimeMode.FIXTURE)

    strict_config: bool = False
    guarded_write_enabled: bool = False
    require_manual_approval_for_write: bool = True
    require_policy_pass_for_write: bool = True
    require_dry_run_acknowledgement: bool = True
    require_audit_on_apply: bool = True

    #: Dev-only: tests may inject overrides when True (never enable in production).
    allow_dev_runtime_override: bool = False

    age_threshold_days_30: int = 30
    age_threshold_days_60: int = 60
    age_threshold_days_75: int = 75
    age_threshold_days_90: int = 90
    age_threshold_days_120: int = 120
    age_threshold_days_180: int = 180

    stale_risk_base: float = Field(default=0.12, ge=0.0, le=1.0)
    max_auto_markdown_pct: int = Field(default=30, ge=0, le=100)
    high_value_price_usd: float = Field(default=200.0, ge=0)

    execution_enabled: bool = False

    #: **Dangerous** — allows EbayWriteExecutor to load. Does not implement HTTP calls yet; fail-closed inside executor.
    ebay_real_writes_enabled: bool = False
    #: Persist successful apply idempotency keys (guarded apply path only).
    apply_idempotency_store_path: Path = Field(
        default=Path(".ebay_claw_apply_idempotency.jsonl")
    )
    #: Require non-empty listing_snapshot_fingerprint on queue item before apply.
    apply_require_enqueue_fingerprint: bool = True
    #: If live has sku / offer_id, queue snapshot must record the same (missing = blocker).
    apply_strict_live_identity: bool = True
    #: Internal / future HTTP apply seam — off by default; requires shared secret when enabled.
    apply_api_enabled: bool = False
    apply_api_shared_secret: str = ""
    #: When True with apply_api_enabled, orchestrator may inject EbayWriteExecutor (still scaffold-only).
    apply_api_allow_live_executor: bool = False

    fixture_path: Path = Field(default=Path("fixtures/sample_listings.json"))
    review_queue_path: Path = Field(default=Path(".ebay_claw_review_queue.json"))
    policy_log_path: Path = Field(default=Path(".ebay_claw_policy.log"))
    policy_structured_log_path: Path = Field(default=Path(".ebay_claw_policy.jsonl"))
    audit_log_path: Path = Field(default=Path(".ebay_claw_audit.jsonl"))
    sync_state_path: Path = Field(default=Path(".ebay_claw_sync_state.json"))
    sync_history_path: Path = Field(default=Path(".ebay_claw_sync_history.jsonl"))
    sync_history_enabled: bool = True
    #: Append-only operational analytics (inventory movement, queue outcomes) — read-only, local JSONL.
    operational_history_path: Path = Field(default=Path(".ebay_claw_operational_history.jsonl"))
    #: Live write expansion advisory (dashboard only — not an apply gate).
    live_write_expansion_min_attempts_readiness: int = Field(
        default=3,
        ge=0,
        le=1_000_000,
        description="7d live attempts below this → expansion advisory insufficient_data.",
    )
    live_write_expansion_min_attempts_rate_eval: int = Field(
        default=5,
        ge=0,
        le=1_000_000,
        description="7d attempts needed before success-rate and ready thresholds apply.",
    )
    live_write_expansion_success_rate_critical_below: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Success rate strictly below this (with rate-eval volume) → strong not_ready signal.",
    )
    live_write_expansion_success_rate_advisory_floor: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Success rate strictly below this (with rate-eval volume) → advisory not_ready signal.",
    )
    live_write_expansion_success_rate_ready_floor: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description="Success rate at or above this (with rate-eval volume) → allows ready if no blockers.",
    )
    live_write_expansion_bucket_jump_min_current_7d: int = Field(
        default=2,
        ge=0,
        le=1_000_000,
        description="Tracked failure bucket: min current-7d count for jump detection.",
    )
    live_write_expansion_bucket_jump_min_delta: int = Field(
        default=2,
        ge=0,
        le=1_000_000,
        description="Tracked failure bucket: min rise vs prior 7d for jump detection.",
    )
    live_write_expansion_prior_window_min_attempts: int = Field(
        default=1,
        ge=0,
        le=1_000_000,
        description="Prior 7d must have at least this many attempts to compare failure totals.",
    )
    #: Last-seen listing snapshot for movement detection (separate from marketplace writes).
    inventory_movement_snapshot_path: Path = Field(
        default=Path(".ebay_claw_inventory_movement_snapshot.json")
    )

    #: When audit JSONL exceeds this size (bytes), rotate file before append. 0 = disabled.
    audit_log_max_bytes: int = Field(default=0, ge=0)
    audit_log_rotation_keep: int = Field(default=5, ge=1, le=100)
    #: Tamper-evident chained hash over audit lines (hash chain resets when log is rotated).
    audit_chain_enabled: bool = False

    ebay_client_id: Optional[str] = Field(default=None, description="eBay application client ID")
    ebay_client_secret: Optional[str] = Field(default=None)
    ebay_refresh_token: Optional[str] = Field(default=None)
    ebay_access_token: Optional[str] = Field(
        default=None,
        description="Short-lived OAuth token; if set, used until refresh is needed",
    )
    ebay_use_sandbox: bool = False
    ebay_marketplace_id: str = "EBAY_US"
    ebay_inventory_page_size: int = Field(default=100, ge=1, le=200)
    ebay_http_timeout_sec: float = Field(default=60.0, ge=5.0)
    ebay_max_retries: int = Field(default=5, ge=1, le=15)
    ebay_base_backoff_sec: float = Field(default=1.0, ge=0.1)
    #: Max attempts that may interpret 401 as recoverable (refresh path); no retry storms.
    ebay_oauth_401_max_attempts: int = Field(default=1, ge=0, le=3)

    api_budget_max_calls_per_run: int = Field(default=500, ge=1, le=50_000)
    api_cache_ttl_sec: float = Field(default=0.0, ge=0.0)
    api_cache_max_entries: int = Field(default=256, ge=0)

    #: Read-only default. For ``EbayWriteExecutor`` (title PUT) the token must include
    #: ``https://api.ebay.com/oauth/api_scope/sell.inventory`` — set ``EBAY_CLAW_EBAY_OAUTH_SCOPE`` accordingly.
    ebay_oauth_scope: str = (
        "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
    )

    compliance_checks_enabled: bool = True
    default_actor: str = "system"

    #: Read-only sold comps (fixture first; swap adapter for official sold APIs later).
    comps_enabled: bool = True
    comps_fixture_path: Path = Field(default=Path("fixtures/sold_comps.json"))
    comps_recency_default_days: int = Field(default=90, ge=7, le=365)

    premium_brands: str = Field(
        default="patagonia,arcteryx,stone island,acronym,visvim,engineered garments,"
        "needles,rick owens,comme des garcons,issey miyake,uniqlo u"
    )

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def _coerce_runtime_mode(cls, v):
        if v is None:
            return ClawRuntimeMode.FIXTURE
        if isinstance(v, ClawRuntimeMode):
            return v
        if isinstance(v, str):
            lv = v.strip().lower()
            for m in ClawRuntimeMode:
                if m.value == lv:
                    return m
            raise ValueError(f"invalid runtime_mode={v!r} (fail-closed)")
        return v

    @computed_field
    @property
    def data_source(self) -> Literal["fixture", "live"]:
        if self.runtime_mode == ClawRuntimeMode.FIXTURE:
            return "fixture"
        return "live"

    @computed_field
    @property
    def read_only_mode(self) -> bool:
        """True when mutations are never allowed (fixture + live_read_only, or guarded off)."""
        if self.runtime_mode == ClawRuntimeMode.FIXTURE:
            return True
        if self.runtime_mode == ClawRuntimeMode.LIVE_READ_ONLY:
            return True
        return not self.guarded_write_enabled

    @property
    def premium_brand_set(self) -> set[str]:
        return {b.strip().lower() for b in self.premium_brands.split(",") if b.strip()}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    from ebay_claw.security.config_validation import validate_settings

    report = validate_settings(s)
    for w in report.warnings:
        _log.warning("Config: %s", w)
    for e in report.errors:
        _log.error("Config: %s", e)
    if not report.ok:
        raise ValueError("Invalid configuration (fail-closed):\n" + "\n".join(report.errors))
    return s


def get_settings_uncached() -> Settings:
    get_settings.cache_clear()
    return get_settings()
