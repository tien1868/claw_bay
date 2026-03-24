"""Persist last listing sync metadata (no tokens)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.sync_history import SyncHistoryEntry
from ebay_claw.models.sync_state import SyncState
from ebay_claw.security.redaction import redact_string
from ebay_claw.services.sync_history import SyncHistoryStore

logger = get_logger(__name__)


def _safe_msg(msg: str) -> str:
    return redact_string((msg or "")[:2000])[:500]


def _duration_sec(started: Optional[datetime], ended: Optional[datetime]) -> Optional[float]:
    if started is None or ended is None:
        return None
    try:
        return max(0.0, (ended - started).total_seconds())
    except Exception:
        return None


class SyncStateStore:
    def __init__(self, path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = path or self._s.sync_state_path

    def read(self) -> SyncState:
        if not self._path.exists():
            return SyncState(runtime_mode=self._s.runtime_mode.value)
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return SyncState.model_validate(raw)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Sync state load failed: %s", e)
            return SyncState(runtime_mode=self._s.runtime_mode.value)

    def write(self, state: SyncState) -> None:
        safe = state.model_copy(
            update={"message_safe": _safe_msg(state.message_safe), "warnings": [_safe_msg(w) for w in state.warnings]}
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(safe.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )

    def _append_history(
        self,
        *,
        source: Literal["fixture", "live"],
        status: Literal["ok", "error", "partial"],
        listing_count: int,
        started_at: Optional[datetime],
        completed_at: datetime,
        message: str,
        partial: bool,
        api_used: int,
        budget_max: int,
        cache_hits: int,
        cache_misses: int,
        auth_code: Optional[str],
    ) -> None:
        hist = SyncHistoryStore(settings=self._s)
        dur = _duration_sec(started_at, completed_at) or 0.0
        hist.append(
            SyncHistoryEntry(
                completed_at_utc=completed_at,
                source=source,
                runtime_mode=self._s.runtime_mode.value,
                status=status,
                listing_count=listing_count,
                duration_sec=dur,
                api_calls_used=api_used,
                api_budget_max=budget_max,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
                partial_sync=partial,
                auth_failure_code=auth_code,
                message_safe=_safe_msg(message),
            )
        )

    def mark_running(self, source: Literal["fixture", "live"]) -> None:
        self.write(
            SyncState(
                source=source,
                runtime_mode=self._s.runtime_mode.value,
                status="running",
                started_at=datetime.now(timezone.utc),
                completed_at=None,
                duration_seconds=None,
                listing_count=0,
                message_safe="",
                pages_fetched=0,
                partial_sync=False,
                warnings=[],
                api_calls_used=0,
                api_budget_max=self._s.api_budget_max_calls_per_run,
                cache_hits=0,
                cache_misses=0,
                last_auth_failure_code=None,
            )
        )

    def mark_ok(
        self,
        source: Literal["fixture", "live"],
        listing_count: int,
        pages: int,
        started_at: Optional[datetime],
        message: str = "",
        *,
        partial_sync: bool = False,
        warnings: Optional[List[str]] = None,
        api_calls_used: int = 0,
        api_budget_max: Optional[int] = None,
        cache_hits: int = 0,
        cache_misses: int = 0,
    ) -> None:
        ws = warnings or []
        done = datetime.now(timezone.utc)
        bmax = api_budget_max or self._s.api_budget_max_calls_per_run
        st_status: Literal["ok", "partial"] = "partial" if partial_sync else "ok"
        self.write(
            SyncState(
                source=source,
                runtime_mode=self._s.runtime_mode.value,
                status=st_status,
                started_at=started_at,
                completed_at=done,
                duration_seconds=_duration_sec(started_at, done),
                listing_count=listing_count,
                pages_fetched=pages,
                message_safe=_safe_msg(message or "sync_complete"),
                partial_sync=partial_sync,
                warnings=[_safe_msg(w) for w in ws],
                api_calls_used=api_calls_used,
                api_budget_max=bmax,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
                last_auth_failure_code=None,
            )
        )
        self._append_history(
            source=source,
            status=st_status,
            listing_count=listing_count,
            started_at=started_at,
            completed_at=done,
            message=message,
            partial=partial_sync,
            api_used=api_calls_used,
            budget_max=bmax,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            auth_code=None,
        )

    def mark_error(
        self,
        source: Literal["fixture", "live"],
        message_safe: str,
        started_at: Optional[datetime],
        *,
        listing_count: int = 0,
        pages_fetched: int = 0,
        api_calls_used: int = 0,
        auth_failure_code: Optional[str] = None,
        cache_hits: int = 0,
        cache_misses: int = 0,
    ) -> None:
        done = datetime.now(timezone.utc)
        self.write(
            SyncState(
                source=source,
                runtime_mode=self._s.runtime_mode.value,
                status="error",
                started_at=started_at,
                completed_at=done,
                duration_seconds=_duration_sec(started_at, done),
                listing_count=listing_count,
                pages_fetched=pages_fetched,
                message_safe=_safe_msg(message_safe),
                partial_sync=False,
                warnings=[],
                api_calls_used=api_calls_used,
                api_budget_max=self._s.api_budget_max_calls_per_run,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
                last_auth_failure_code=auth_failure_code,
            )
        )
        self._append_history(
            source=source,
            status="error",
            listing_count=listing_count,
            started_at=started_at,
            completed_at=done,
            message=message_safe,
            partial=False,
            api_used=api_calls_used,
            budget_max=self._s.api_budget_max_calls_per_run,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            auth_code=auth_failure_code,
        )
