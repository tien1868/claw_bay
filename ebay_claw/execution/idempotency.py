"""Guarded-apply idempotency keys + durable duplicate-success detection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ebay_claw.models.domain import ReviewQueueItem


def build_apply_idempotency_key(item: ReviewQueueItem) -> str:
    """Stable key for one approved queue revision (item id, action, version, enqueue fingerprint)."""
    fp = (item.listing_snapshot_fingerprint or "").strip()
    raw = (
        f"{item.id}\x00{item.proposed_action_type.value}\x00{item.version}\x00{fp}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApplyIdempotencyStore:
    """Append-only JSONL of successful applies — process-local; replace with DB for multi-host."""

    def __init__(self, path: Path):
        self._path = path

    @classmethod
    def from_settings(cls, settings: Any) -> "ApplyIdempotencyStore":
        from ebay_claw.config.settings import Settings

        s = settings if isinstance(settings, Settings) else settings
        return cls(s.apply_idempotency_store_path)

    def has_successful_apply(self, idempotency_key: str) -> bool:
        if not idempotency_key or not self._path.exists():
            return False
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("idempotency_key") == idempotency_key:
                    return True
        return False

    def record_success(
        self,
        *,
        idempotency_key: str,
        review_item_id: str,
        listing_id: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "idempotency_key": idempotency_key,
            "review_item_id": review_item_id,
            "listing_id": listing_id,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
