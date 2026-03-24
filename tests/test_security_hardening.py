"""Security / fail-closed behavior — runtime mode, overrides, auth, audit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ebay_claw.adapters.factory import build_listing_adapter
from ebay_claw.adapters.ebay_readonly_http import ReadOnlyEbayInventoryClient
from ebay_claw.audit.store import AuditLogStore
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.audit import AuditEvent
from ebay_claw.models.runtime_mode import ClawRuntimeMode
from ebay_claw.services.orchestrator import ClawOrchestrator


def test_invalid_runtime_mode_rejected():
    with pytest.raises(ValueError, match="runtime_mode|fail-closed"):
        Settings(runtime_mode="not_a_valid_mode")  # type: ignore[arg-type]


def test_orchestrator_rejects_data_source_override_by_default(tmp_path: Path):
    s = Settings(
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        policy_log_path=tmp_path / "p.log",
    )
    with pytest.raises(Exception, match="canonical|override"):
        ClawOrchestrator(settings=s, data_source_override="fixture")


def test_orchestrator_allows_override_when_dev_flag(tmp_path: Path):
    s = Settings(
        allow_dev_runtime_override=True,
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        ebay_access_token="t",
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        policy_log_path=tmp_path / "p.log",
    )
    o = ClawOrchestrator(settings=s, data_source_override="fixture")
    assert o.settings.runtime_mode == ClawRuntimeMode.FIXTURE


def test_factory_live_without_credentials_raises(tmp_path: Path):
    s = Settings(
        runtime_mode=ClawRuntimeMode.LIVE_READ_ONLY,
        review_queue_path=tmp_path / "q.json",
        audit_log_path=tmp_path / "a.jsonl",
        policy_log_path=tmp_path / "p.log",
    )
    with pytest.raises(ValueError, match="OAuth|Fail-closed"):
        build_listing_adapter(s)


def test_get_settings_fail_closed_live_without_oauth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("EBAY_CLAW_RUNTIME_MODE", "live_read_only")
    monkeypatch.delenv("EBAY_CLAW_EBAY_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("EBAY_CLAW_EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLAW_EBAY_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("EBAY_CLAW_EBAY_CLIENT_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="OAuth|configuration"):
        get_settings()
    get_settings.cache_clear()


def test_401_without_refresh_does_not_retry_storm(tmp_path: Path):
    n = {"c": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["c"] += 1
        return httpx.Response(401, json={"errors": [{"message": "Unauthorized"}]})

    transport = httpx.MockTransport(handler)
    s = Settings(
        fixture_path=tmp_path / "f.json",
        review_queue_path=tmp_path / "q.json",
        policy_log_path=tmp_path / "p.log",
        ebay_access_token="bad",
        ebay_max_retries=8,
        ebay_base_backoff_sec=0.1,
    )
    from ebay_claw.adapters.ebay_oauth import recover_inventory_session_after_401

    holder: dict = {}

    def on_401() -> None:
        recover_inventory_session_after_401(s, holder)

    client = ReadOnlyEbayInventoryClient(
        s,
        lambda: str(s.ebay_access_token),
        transport=transport,
        on_unauthorized=on_401,
    )
    with pytest.raises(RuntimeError):
        client.get_json("/sell/inventory/v1/inventory_item", {"limit": "1"})
    assert n["c"] == 1


def test_audit_records_append_only(tmp_path: Path):
    p = tmp_path / "audit.jsonl"
    s = Settings(audit_log_path=p)
    store = AuditLogStore(settings=s)
    for i in range(3):
        store.append(
            AuditEvent(
                event_id=f"id{i}",
                event_type="sync_started",
                timestamp_utc=datetime.now(timezone.utc),
                actor="test",
                reason_codes=[],
            )
        )
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
