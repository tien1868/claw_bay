"""Structured compliance findings for review items and dashboards."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ComplianceSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ComplianceIssueRecord(BaseModel):
    code: str
    severity: ComplianceSeverity
    message: str
    blocks_guarded_write: bool = Field(
        description="If true, guarded-write apply must be rejected until resolved.",
    )
