import json
from datetime import datetime, timezone
from pathlib import Path

from ebay_claw.audit.store import (
    AUDIT_CHAIN_GENESIS_PREV,
    AuditLogStore,
    canonical_record_hash_payload,
    compute_audit_record_hash,
    new_event_id,
)
from ebay_claw.config.settings import Settings
from ebay_claw.models.audit import AuditEvent


def test_compute_chain_link_matches_append_logic():
    prev = AUDIT_CHAIN_GENESIS_PREV
    ev = {"event_id": "a", "event_type": "sync_started"}
    inner = {"event": ev, "chain_prev_hash": prev}
    canon = canonical_record_hash_payload(inner)
    h1 = compute_audit_record_hash(prev, canon)
    inner2 = {"event": {"event_id": "b", "event_type": "sync_completed"}, "chain_prev_hash": h1}
    canon2 = canonical_record_hash_payload(inner2)
    h2 = compute_audit_record_hash(h1, canon2)
    assert h1 != h2
    assert len(h1) == 64


def test_audit_file_chain_is_continuous(tmp_path: Path):
    p = tmp_path / "audit.jsonl"
    s = Settings(audit_log_path=p, audit_chain_enabled=True)
    store = AuditLogStore(settings=s)
    for i in range(3):
        store.append(
            AuditEvent(
                event_id=new_event_id(),
                event_type="sync_started",
                timestamp_utc=datetime.now(timezone.utc),
                actor="test",
                reason_codes=[str(i)],
            )
        )
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    prev = AUDIT_CHAIN_GENESIS_PREV
    for ln in lines:
        obj = json.loads(ln)
        assert obj["chain_prev_hash"] == prev
        inner = {"event": obj["event"], "chain_prev_hash": prev}
        canon = canonical_record_hash_payload(inner)
        assert obj["record_hash"] == compute_audit_record_hash(prev, canon)
        prev = obj["record_hash"]
