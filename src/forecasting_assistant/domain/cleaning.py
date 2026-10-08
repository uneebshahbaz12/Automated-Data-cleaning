from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ColumnType(StrEnum):
    EMPTY = "empty"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMERIC = "numeric"
    DATETIME = "datetime"
    CATEGORICAL = "categorical"
    TEXT = "text"
    MIXED = "mixed"


class ColumnRole(StrEnum):
    TIME = "time"
    TARGET = "target"
    SERIES_ID = "series_id"
    CATEGORICAL = "categorical"
    NUMERIC_FEATURE = "numeric_feature"
    TEXT = "text"
    UNKNOWN = "unknown"


class CleaningActionKind(StrEnum):
    NORMALIZE_TEXT = "normalize_text"
    PARSE_DATETIME = "parse_datetime"
    SORT_BY_TIME = "sort_by_time"
    REMOVE_EXACT_DUPLICATES = "remove_exact_duplicates"
    DROP_ROWS_WITH_MISSING_TIME = "drop_rows_with_missing_time"
    FILL_NUMERIC_MISSING = "fill_numeric_missing"
    FLAG_OUTLIERS = "flag_outliers"


class CleaningRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ColumnProfile(BaseModel):
    name: str
    detected_type: ColumnType
    likely_role: ColumnRole = ColumnRole.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    row_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    missing_ratio: float = Field(ge=0, le=1)
    unique_count: int = Field(ge=0)
    sample_values: list[str] = Field(default_factory=list, max_length=5)
    minimum: float | None = None
    maximum: float | None = None


class DataIssue(BaseModel):
    code: str
    severity: str
    message: str
    column: str | None = None
    count: int = Field(default=0, ge=0)
    blocking: bool = False


class DataProfile(BaseModel):
    profile_id: UUID = Field(default_factory=uuid4)
    format: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[ColumnProfile]
    time_column: str | None = None
    target_column: str | None = None
    inferred_frequency: str | None = None
    exact_duplicate_rows: int = Field(default=0, ge=0)
    duplicate_time_rows: int = Field(default=0, ge=0)
    missing_periods: int = Field(default=0, ge=0)
    issues: list[DataIssue] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CleaningAction(BaseModel):
    kind: CleaningActionKind
    risk: CleaningRisk
    column: str | None = None
    reason: str
    automatic: bool = False
    approved: bool = False


class CleaningPlan(BaseModel):
    plan_id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    actions: list[CleaningAction] = Field(default_factory=list)


class CleaningReport(BaseModel):
    report_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    rows_before: int = Field(ge=0)
    rows_after: int = Field(ge=0)
    missing_values_filled: int = Field(default=0, ge=0)
    exact_duplicates_removed: int = Field(default=0, ge=0)
    rows_dropped: int = Field(default=0, ge=0)
    outliers_flagged: int = Field(default=0, ge=0)
    actions_applied: list[str] = Field(default_factory=list)
    issues: list[DataIssue] = Field(default_factory=list)
    valid: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
