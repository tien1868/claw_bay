"""Structured policy decision records (JSON lines)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from ebay_claw.security.redaction import redact_mapping, redact_string


class PolicyDecisionRecord(BaseModel):
    schema_version: int = 1
    ts_utc: datetime
    listing_id: Optional[str] = None
    review_item_id: Optional[str] = None
    action: Optional[str] = None
    allowed: bool
    blocks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    rationale_short: str = ""


def append_policy_jsonl(path: Path, record: PolicyDecisionRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump(mode="json")
    line = json.dumps(redact_mapping(payload), default=str, ensure_ascii=True) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def safe_rationale(text: str, max_len: int = 400) -> str:
    return redact_string((text or "")[:max_len])
