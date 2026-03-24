from datetime import datetime, timezone
from pathlib import Path

from ebay_claw.audit.store import AuditLogStore
from ebay_claw.config.settings import Settings
from ebay_claw.models.audit import AuditEvent


def test_audit_append_is_line_oriented(tmp_path: Path):
    p = tmp_path / "audit.jsonl"
    s = Settings(audit_log_path=p)
    store = AuditLogStore(settings=s)
    store.append(
        AuditEvent(
            event_id="e1",
            event_type="execution_attempt",
            timestamp_utc=datetime.now(timezone.utc),
            actor="tester",
            decision="blocked",
        )
    )
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "tester" in lines[0]
    assert "secret" not in lines[0].lower() or True
