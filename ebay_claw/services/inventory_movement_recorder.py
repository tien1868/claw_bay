"""Detect inventory deltas and staleness transitions; emit operational events (read-only)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import json

from ebay_claw.analytics.inventory_analyst import InventoryAnalyst
from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.logging_config import get_logger
from ebay_claw.models.domain import ListingRecord
from ebay_claw.services.operational_history_store import OperationalHistoryStore

logger = get_logger(__name__)


class InventoryMovementRecorder:
    """
    Persists last-seen per-listing signals to emit:
    listing_created, listing_sold, stale_crossed_90d, stale_cleared.
    First run seeds state without emitting creates (cold start).
    """

    def __init__(self, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = self._s.inventory_movement_snapshot_path
        self._history = OperationalHistoryStore(settings=self._s)

    def _read_raw(self) -> dict:
        if not self._path.exists():
            return {"listings": {}, "initialized": False}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("inventory snapshot load failed: %s", e)
            return {"listings": {}, "initialized": False}

    def _write_raw(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

    def record_after_ingest(
        self,
        listings: List[ListingRecord],
        *,
        as_of: date,
        analyst: InventoryAnalyst,
        data_source: str,
    ) -> None:
        """Compare to last snapshot; append events; write new snapshot."""
        raw = self._read_raw()
        prev: Dict[str, Any] = dict(raw.get("listings") or {})
        initialized = bool(raw.get("initialized"))
        cur: Dict[str, Dict[str, Any]] = {}

        for lst in listings:
            analysis = analyst.analyze(lst, as_of=as_of)
            cur[lst.listing_id] = {
                "days_active": analysis.days_active,
                "is_stale": analysis.is_stale,
                "sold_90d": int(lst.sold_quantity_last_90_days or 0),
            }

        if not initialized:
            self._write_raw(
                {
                    "listings": cur,
                    "initialized": True,
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._history.append_event(
                "listing_synced",
                source="inventory_tracker",
                payload={
                    "listing_count": len(listings),
                    "data_source": data_source,
                    "cold_start": True,
                },
            )
            return

        prev_ids = set(prev.keys())
        cur_ids = set(cur.keys())

        for lid in cur_ids - prev_ids:
            self._history.append_event(
                "listing_created",
                source="inventory_tracker",
                listing_id=lid,
                payload={"data_source": data_source},
            )

        for lid in prev_ids - cur_ids:
            self._history.append_event(
                "listing_sold",
                source="inventory_tracker",
                listing_id=lid,
                payload={
                    "reason": "listing_removed_from_active_inventory",
                    "units": 1.0,
                    "data_source": data_source,
                },
            )

        for lid in cur_ids & prev_ids:
            p = prev[lid]
            c = cur[lid]
            p_stale = bool(p.get("is_stale"))
            c_stale = bool(c.get("is_stale"))
            p_days = int(p.get("days_active") or 0)
            c_days = int(c.get("days_active") or 0)

            if p_days < 90 <= c_days:
                self._history.append_event(
                    "stale_crossed_90d",
                    source="inventory_tracker",
                    listing_id=lid,
                    payload={"days_active": c_days, "data_source": data_source},
                )

            if p_stale and not c_stale:
                self._history.append_event(
                    "stale_cleared",
                    source="inventory_tracker",
                    listing_id=lid,
                    payload={"reason": "no_longer_flagged_stale", "data_source": data_source},
                )

            prev_sold = int(p.get("sold_90d") or 0)
            cur_sold = int(c.get("sold_90d") or 0)
            delta = cur_sold - prev_sold
            if delta > 0:
                self._history.append_event(
                    "listing_sold",
                    source="inventory_tracker",
                    listing_id=lid,
                    payload={
                        "reason": "sold_quantity_increased",
                        "units": float(delta),
                        "data_source": data_source,
                    },
                )

        self._history.append_event(
            "listing_synced",
            source="inventory_tracker",
            payload={
                "listing_count": len(listings),
                "data_source": data_source,
                "cold_start": False,
            },
        )

        self._write_raw(
            {
                "listings": cur,
                "initialized": True,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
