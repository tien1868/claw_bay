"""Last N sync runs — JSONL append-only for operator dashboards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from ebay_claw.config.settings import Settings, get_settings
from ebay_claw.models.sync_history import SyncHistoryEntry


class SyncHistoryStore:
    def __init__(self, path: Optional[Path] = None, settings: Optional[Settings] = None):
        self._s = settings or get_settings()
        self._path = path or self._s.sync_history_path

    def append(self, entry: SyncHistoryEntry) -> None:
        if not self._s.sync_history_enabled:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.model_dump(mode="json"), default=str, ensure_ascii=True) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)

    def last_n(self, n: int = 10) -> List[SyncHistoryEntry]:
        if n <= 0 or not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        tail = lines[-n:]
        out: List[SyncHistoryEntry] = []
        for ln in tail:
            try:
                out.append(SyncHistoryEntry.model_validate(json.loads(ln)))
            except (json.JSONDecodeError, ValueError):
                continue
        return out
