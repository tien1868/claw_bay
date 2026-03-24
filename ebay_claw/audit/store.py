"""Append-only JSONL audit log — rotation hooks and optional tamper-evident hash chain."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.audit import AuditEvent
from ebay_claw.security.redaction import redact_mapping

AUDIT_CHAIN_GENESIS_PREV = "ebay_claw_audit_chain_genesis_v1"


def canonical_record_hash_payload(inner: dict[str, Any]) -> str:
    """Deterministic JSON for hashing."""
    return json.dumps(inner, sort_keys=True, separators=(",", ":"), default=str)


def compute_audit_record_hash(chain_prev_hash: str, inner_canonical: str) -> str:
    """record_hash = SHA256(prev || '|' || canonical_json(inner))."""
    blob = f"{chain_prev_hash}|{inner_canonical}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AuditLogStore:
    def __init__(self, path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = path or self._s.audit_log_path

    def _rotated_path(self, index: int) -> Path:
        """e.g. audit.jsonl.1, audit.jsonl.2"""
        return self._path.with_name(f"{self._path.stem}.{index}{self._path.suffix}")

    def _maybe_rotate(self) -> None:
        max_b = self._s.audit_log_max_bytes
        if max_b <= 0 or not self._path.exists():
            return
        try:
            if self._path.stat().st_size < max_b:
                return
        except OSError:
            return
        keep = self._s.audit_log_rotation_keep
        for i in range(keep - 1, 0, -1):
            src = self._rotated_path(i)
            dst = self._rotated_path(i + 1)
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            if src.exists():
                try:
                    src.rename(dst)
                except OSError:
                    pass
        first = self._rotated_path(1)
        if first.exists():
            try:
                first.unlink()
            except OSError:
                pass
        try:
            self._path.rename(first)
        except OSError:
            pass
        # Hash chain intentionally restarts on a new active file after rotation.

    def _read_last_chain_tip(self) -> Tuple[str, Optional[str]]:
        """
        Return (prev_hash_for_next_record, last_line_record_hash or None).
        When file empty / no chain: prev = AUDIT_CHAIN_GENESIS_PREV.
        """
        if not self._path.exists():
            return AUDIT_CHAIN_GENESIS_PREV, None
        try:
            text = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return AUDIT_CHAIN_GENESIS_PREV, None
        if not text:
            return AUDIT_CHAIN_GENESIS_PREV, None
        last_line = text.splitlines()[-1]
        try:
            obj = json.loads(last_line)
        except (json.JSONDecodeError, TypeError):
            return AUDIT_CHAIN_GENESIS_PREV, None
        if isinstance(obj, dict) and "record_hash" in obj and "chain_prev_hash" in obj:
            rh = str(obj.get("record_hash") or "")
            return rh or AUDIT_CHAIN_GENESIS_PREV, rh or None
        return AUDIT_CHAIN_GENESIS_PREV, None

    def append(self, event: AuditEvent) -> AuditEvent:
        self._maybe_rotate()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        safe = redact_mapping(event.model_dump(mode="json"))
        if self._s.audit_chain_enabled:
            chain_prev, _ = self._read_last_chain_tip()
            inner = {"event": safe, "chain_prev_hash": chain_prev}
            canon = canonical_record_hash_payload(inner)
            record_hash = compute_audit_record_hash(chain_prev, canon)
            line_obj: dict[str, Any] = {
                "event": safe,
                "chain_prev_hash": chain_prev,
                "record_hash": record_hash,
            }
            line = json.dumps(line_obj, default=str, ensure_ascii=True) + "\n"
        else:
            line = json.dumps(safe, default=str, ensure_ascii=True) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)
        return event

    def append_execution_result(
        self,
        *,
        actor: str,
        listing_id: str,
        review_item_id: str,
        success: bool,
        message: str,
        snapshot_before: dict,
        snapshot_after: dict,
        meta: Optional[dict] = None,
    ) -> AuditEvent:
        ev = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type="execution_result",
            timestamp_utc=datetime.now(timezone.utc),
            actor=actor,
            listing_id=listing_id,
            review_item_id=review_item_id,
            decision="success" if success else "blocked_or_failed",
            reason_codes=[message[:200]],
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            redacted_meta=meta or {},
        )
        return self.append(ev)


def new_event_id() -> str:
    return str(uuid.uuid4())
