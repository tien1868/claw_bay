"""Record and query operational history — JSONL append-only."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Union

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.operational_history import OperationalEventRecord, OperationalEventType

logger = get_logger(__name__)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class OperationalHistoryStore:
    def __init__(self, path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = path or self._s.operational_history_path

    def append(self, record: OperationalEventRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        rec = record.model_copy(
            update={"occurred_at_utc": _to_utc(record.occurred_at_utc)}
        )
        with self._path.open("a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")

    def append_event(
        self,
        event_type: OperationalEventType,
        *,
        source: str,
        listing_id: Optional[str] = None,
        review_item_id: Optional[str] = None,
        actor: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        occurred_at_utc: Optional[datetime] = None,
    ) -> OperationalEventRecord:
        rec = OperationalEventRecord(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            occurred_at_utc=occurred_at_utc or datetime.now(timezone.utc),
            source=source,
            listing_id=listing_id,
            review_item_id=review_item_id,
            actor=actor,
            payload=payload or {},
        )
        self.append(rec)
        return rec

    def iter_events(
        self,
        *,
        since_utc: Optional[datetime] = None,
        until_utc: Optional[datetime] = None,
        event_types: Optional[Set[OperationalEventType]] = None,
    ) -> Iterator[OperationalEventRecord]:
        if not self._path.exists():
            return
        since_utc = _to_utc(since_utc) if since_utc else None
        until_utc = _to_utc(until_utc) if until_utc else None
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = OperationalEventRecord.model_validate_json(line)
                except Exception as e:
                    logger.warning("Skip bad operational history line: %s", e)
                    continue
                if event_types is not None and rec.event_type not in event_types:
                    continue
                ts = _to_utc(rec.occurred_at_utc)
                if since_utc and ts < since_utc:
                    continue
                if until_utc and ts > until_utc:
                    continue
                yield rec

    def load_all(self) -> List[OperationalEventRecord]:
        return list(self.iter_events())

    def count_events(
        self,
        event_types: Union[OperationalEventType, Sequence[OperationalEventType]],
        *,
        since_utc: datetime,
        until_utc: datetime,
    ) -> int:
        if isinstance(event_types, str):
            typeset = {event_types}
        else:
            typeset = set(event_types)
        return sum(
            1
            for _ in self.iter_events(
                since_utc=since_utc, until_utc=until_utc, event_types=typeset
            )
        )

    def sum_payload_float(
        self,
        event_type: OperationalEventType,
        key: str,
        *,
        since_utc: datetime,
        until_utc: datetime,
    ) -> float:
        total = 0.0
        for rec in self.iter_events(
            since_utc=since_utc,
            until_utc=until_utc,
            event_types={event_type},
        ):
            v = rec.payload.get(key)
            if v is None:
                continue
            try:
                total += float(v)
            except (TypeError, ValueError):
                continue
        return total

    def has_recent_sync_signal(self, as_of: date, *, lookback_days: int = 14) -> bool:
        """True if we have a sync batch in the window — event-based metrics are meaningful."""
        end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)
        start = end - timedelta(days=lookback_days)
        return (
            self.count_events(
                "listing_synced",
                since_utc=start,
                until_utc=end,
            )
            >= 1
        )

    def counter_by_type(
        self,
        *,
        since_utc: datetime,
        until_utc: datetime,
        types: Optional[Set[OperationalEventType]] = None,
    ) -> Counter[str]:
        c: Counter[str] = Counter()
        for rec in self.iter_events(
            since_utc=since_utc, until_utc=until_utc, event_types=types
        ):
            c[rec.event_type] += 1
        return c

    def rollup_windows_days(
        self,
        windows: Sequence[int],
        *,
        as_of: date,
    ) -> Dict[int, Counter[str]]:
        """Return event counts per window (days back from end of as_of day)."""
        end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)
        out: Dict[int, Counter[str]] = {}
        for d in windows:
            start = end - timedelta(days=d)
            out[d] = self.counter_by_type(since_utc=start, until_utc=end)
        return out

    def weekly_slices(
        self,
        *,
        as_of: date,
        num_weeks: int = 4,
    ) -> List[tuple[date, date, Counter[str]]]:
        """Non-overlapping week buckets ending at as_of (inclusive)."""
        end_day = as_of
        slices: List[tuple[date, date, Counter[str]]] = []
        for _ in range(num_weeks):
            week_end = datetime(
                end_day.year, end_day.month, end_day.day, 23, 59, 59, tzinfo=timezone.utc
            )
            week_start_dt = week_end - timedelta(days=6)
            week_start_day = week_start_dt.date()
            cnt = self.counter_by_type(since_utc=week_start_dt, until_utc=week_end)
            slices.append((week_start_day, end_day, cnt))
            end_day = week_start_day - timedelta(days=1)
        return slices

    def proposals_and_approvals_for_action(
        self,
        proposed_event: OperationalEventType,
        proposed_payload_key: str,
        payload_value: str,
        *,
        since_utc: datetime,
        until_utc: datetime,
    ) -> tuple[int, int]:
        """Count proposal events and queue_approved with matching proposed_action_type."""
        proposed_n = 0
        for rec in self.iter_events(
            since_utc=since_utc,
            until_utc=until_utc,
            event_types={proposed_event},
        ):
            if rec.payload.get(proposed_payload_key) == payload_value:
                proposed_n += 1
        approved_n = 0
        for rec in self.iter_events(
            since_utc=since_utc,
            until_utc=until_utc,
            event_types={"queue_approved"},
        ):
            if rec.payload.get("proposed_action_type") == payload_value:
                approved_n += 1
        return proposed_n, approved_n

    def listing_event_counts(
        self,
        listing_id: str,
        event_types: Set[OperationalEventType],
        *,
        since_utc: datetime,
        until_utc: datetime,
    ) -> int:
        n = 0
        for rec in self.iter_events(
            since_utc=since_utc,
            until_utc=until_utc,
            event_types=event_types,
        ):
            if rec.listing_id == listing_id:
                n += 1
        return n
