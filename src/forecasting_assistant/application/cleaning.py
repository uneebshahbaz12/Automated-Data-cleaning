from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from uuid import UUID, uuid5

from forecasting_assistant.domain.cleaning import (
    CleaningAction,
    CleaningActionKind,
    CleaningPlan,
    CleaningReport,
    CleaningRisk,
    ColumnProfile,
    ColumnRole,
    ColumnType,
    DataIssue,
    DataProfile,
)


class UnsupportedDatasetFormatError(ValueError):
    pass


class CleaningValidationError(ValueError):
    pass


_MISSING = {"", "na", "n/a", "null", "none", "nan", "missing"}


def _json_rows(payload: object) -> list[dict[object, object]] | None:
    if isinstance(payload, list):
        # World Bank-style responses are [metadata, [records...]].
        if len(payload) == 2 and isinstance(payload[1], list):
            payload = payload[1]
        return payload if all(isinstance(row, dict) for row in payload) else None
    if not isinstance(payload, dict):
        return None

    for key in ("data", "records", "results", "items", "value", "messages", "features"):
        nested = payload.get(key)
        rows = _json_rows(nested)
        if rows is not None:
            if key == "features":
                return [
                    {
                        **(
                            row.get("properties", {})
                            if isinstance(row.get("properties"), dict)
                            else {}
                        ),
                        **{
                            key: value
                            for key, value in row.items()
                            if key != "properties"
                        },
                    }
                    for row in rows
                ]
            return rows

    values = list(payload.values())
    if values and all(isinstance(value, list) for value in values):
        row_count = len(values[0])
        if all(len(value) == row_count for value in values):
            keys = list(payload)
            return [
                {key: payload[key][index] for key in keys}
                for index in range(row_count)
            ]

    return [payload]


def _read_rows(content: bytes, *, filename: str) -> tuple[list[dict[str, str]], list[str], str]:
    suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CleaningValidationError("dataset must be valid UTF-8 for profiling") from error
    if suffix == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or any(not str(name).strip() for name in reader.fieldnames):
            raise CleaningValidationError("CSV must contain a non-empty header")
        columns = [str(name).strip() for name in reader.fieldnames]
        return [{column: str(row.get(column, "")) for column in columns} for row in reader], columns, "csv"
    if suffix == "json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise CleaningValidationError("JSON dataset is malformed") from error
        payload_rows = _json_rows(payload)
        if payload_rows is None:
            raise CleaningValidationError("JSON dataset must be a list of objects")
        columns = list(dict.fromkeys(str(key) for row in payload_rows for key in row))
        return [
            {
                column: ""
                if row.get(column) is None
                else str(row.get(column, ""))
                for column in columns
            }
            for row in payload_rows
        ], columns, "json"
    raise UnsupportedDatasetFormatError("cleaning currently supports CSV and JSON datasets")


def _is_missing(value: str) -> bool:
    return value.strip().casefold() in _MISSING


def _number(value: str) -> float | None:
    if _is_missing(value):
        return None
    try:
        result = float(value.strip())
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _datetime(value: str) -> datetime | None:
    if _is_missing(value):
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _detect_type(values: list[str]) -> tuple[ColumnType, float]:
    present = [value.strip() for value in values if not _is_missing(value)]
    if not present:
        return ColumnType.EMPTY, 1.0
    numeric = sum(_number(value) is not None for value in present)
    dates = sum(_datetime(value) is not None for value in present)
    booleans = sum(value.casefold() in {"true", "false", "yes", "no", "0", "1"} for value in present)
    ratio = lambda count: count / len(present)
    if ratio(numeric) == 1:
        integers = all(float(value).is_integer() for value in present)
        return (ColumnType.INTEGER if integers else ColumnType.NUMERIC), 1.0
    if ratio(dates) >= 0.9:
        return ColumnType.DATETIME, ratio(dates)
    if ratio(booleans) == 1:
        return ColumnType.BOOLEAN, 1.0
    unique = len({value.casefold() for value in present})
    if unique <= max(20, len(present) // 10):
        return ColumnType.CATEGORICAL, min(1.0, 0.7 + (1 - unique / len(present)))
    if numeric or dates:
        return ColumnType.MIXED, max(ratio(numeric), ratio(dates))
    return ColumnType.TEXT, 1.0


def _role(column: str, detected: ColumnType, values: list[str], target_hint: str | None) -> ColumnRole:
    normalized = re.sub(r"[^a-z0-9]+", "_", column.casefold()).strip("_")
    if target_hint and column.casefold() == target_hint.casefold():
        return ColumnRole.TARGET
    if detected == ColumnType.DATETIME or any(token in normalized for token in ("date", "time", "timestamp")):
        return ColumnRole.TIME
    if detected == ColumnType.NUMERIC or detected == ColumnType.INTEGER:
        return ColumnRole.NUMERIC_FEATURE
    if detected == ColumnType.CATEGORICAL:
        unique = len({value for value in values if not _is_missing(value)})
        if unique and unique <= max(100, len(values) // 2):
            return ColumnRole.SERIES_ID if any(token in normalized for token in ("id", "code", "key")) else ColumnRole.CATEGORICAL
    return ColumnRole.TEXT


def profile_dataset(
    content: bytes,
    *,
    filename: str,
    target_column: str | None = None,
    time_column: str | None = None,
) -> tuple[DataProfile, list[dict[str, str]]]:
    rows, columns, dataset_format = _read_rows(content, filename=filename)
    profile_id = uuid5(UUID("1d4f9e30-f5d4-4f5e-a1c0-df6d2a7ddf89"), content.hex())
    profiles: list[ColumnProfile] = []
    for column in columns:
        values = [row.get(column, "") for row in rows]
        detected, confidence = _detect_type(values)
        numbers = [_number(value) for value in values]
        present_numbers = [value for value in numbers if value is not None]
        missing = sum(_is_missing(value) for value in values)
        profiles.append(
            ColumnProfile(
                name=column,
                detected_type=detected,
                likely_role=(
                    ColumnRole.TIME if column.casefold() == (time_column or "").casefold()
                    else _role(column, detected, values, target_column)
                ),
                confidence=confidence,
                row_count=len(rows),
                missing_count=missing,
                missing_ratio=missing / max(1, len(rows)),
                unique_count=len({value for value in values if not _is_missing(value)}),
                sample_values=list(dict.fromkeys(value for value in values if not _is_missing(value)))[:5],
                minimum=min(present_numbers) if present_numbers else None,
                maximum=max(present_numbers) if present_numbers else None,
            )
        )
    selected_time = time_column or next((p.name for p in profiles if p.detected_type == ColumnType.DATETIME), None)
    selected_target = target_column if target_column in columns else next(
        (p.name for p in profiles if p.likely_role == ColumnRole.NUMERIC_FEATURE), None
    )
    exact_duplicates = len(rows) - len({tuple(row.get(column, "") for column in columns) for row in rows})
    duplicate_times = 0
    missing_periods = 0
    inferred_frequency: str | None = None
    issues: list[DataIssue] = []
    if selected_time:
        parsed = [_datetime(row.get(selected_time, "")) for row in rows]
        valid_times = sorted(value for value in parsed if value is not None)
        duplicate_times = len(valid_times) - len(set(valid_times))
        if len(valid_times) >= 3:
            deltas = [(right - left).total_seconds() for left, right in pairwise(valid_times)]
            common = Counter(deltas).most_common(1)[0][0]
            frequency_map = {3600: "hourly", 86400: "daily", 604800: "weekly", 2592000: "monthly"}
            inferred_frequency = frequency_map.get(int(common))
            if common > 0:
                missing_periods = max(0, round((valid_times[-1] - valid_times[0]).total_seconds() / common) + 1 - len(set(valid_times)))
        if any(value is None for value in parsed):
            issues.append(DataIssue(code="invalid_timestamp", severity="error", column=selected_time, count=sum(value is None for value in parsed), blocking=True, message="Some time values cannot be parsed."))
    for profile in profiles:
        if profile.missing_count:
            issues.append(DataIssue(code="missing_values", severity="warning", column=profile.name, count=profile.missing_count, message=f"{profile.missing_count} values are missing."))
    if exact_duplicates:
        issues.append(DataIssue(code="duplicate_rows", severity="warning", count=exact_duplicates, message="Exact duplicate rows were detected."))
    if duplicate_times:
        issues.append(DataIssue(code="duplicate_timestamps", severity="warning", column=selected_time, count=duplicate_times, message="Duplicate timestamps were detected."))
    return DataProfile(
        profile_id=profile_id,
        format=dataset_format,
        row_count=len(rows),
        column_count=len(columns),
        columns=profiles,
        time_column=selected_time,
        target_column=selected_target,
        inferred_frequency=inferred_frequency,
        exact_duplicate_rows=exact_duplicates,
        duplicate_time_rows=duplicate_times,
        missing_periods=missing_periods,
        issues=issues,
    ), rows


def build_cleaning_plan(profile: DataProfile) -> CleaningPlan:
    actions = [
        CleaningAction(kind=CleaningActionKind.NORMALIZE_TEXT, risk=CleaningRisk.LOW, reason="Normalize whitespace in textual cells.", automatic=True, approved=True)
    ]
    if profile.time_column:
        actions.extend(
            [
                CleaningAction(kind=CleaningActionKind.PARSE_DATETIME, risk=CleaningRisk.LOW, column=profile.time_column, reason="Validate and normalize the time column.", automatic=True, approved=True),
                CleaningAction(kind=CleaningActionKind.SORT_BY_TIME, risk=CleaningRisk.LOW, column=profile.time_column, reason="Sort observations chronologically.", automatic=True, approved=True),
            ]
        )
    if profile.exact_duplicate_rows:
        actions.append(CleaningAction(kind=CleaningActionKind.REMOVE_EXACT_DUPLICATES, risk=CleaningRisk.LOW, reason="Remove exact duplicate rows.", automatic=True, approved=True))
    for column in profile.columns:
        if column.missing_count and column.detected_type in {ColumnType.INTEGER, ColumnType.NUMERIC}:
            actions.append(CleaningAction(kind=CleaningActionKind.FILL_NUMERIC_MISSING, risk=CleaningRisk.MEDIUM, column=column.name, reason="Temporarily fill missing numeric values using the column median.", automatic=True, approved=True))
    if profile.target_column:
        actions.append(CleaningAction(kind=CleaningActionKind.FLAG_OUTLIERS, risk=CleaningRisk.LOW, column=profile.target_column, reason="Flag target values outside the IQR range; do not delete them.", automatic=True, approved=True))
    return CleaningPlan(profile_id=profile.profile_id, actions=actions)


def _apply_action(rows: list[dict[str, str]], action: CleaningAction, time_column: str | None) -> tuple[list[dict[str, str]], int, int, list[str]]:
    if not action.approved:
        return rows, 0, 0, []
    if action.kind == CleaningActionKind.NORMALIZE_TEXT:
        return [{key: value.strip() for key, value in row.items()} for row in rows], 0, 0, [action.kind.value]
    if action.kind == CleaningActionKind.REMOVE_EXACT_DUPLICATES:
        seen: set[tuple[tuple[str, str], ...]] = set()
        result = []
        for row in rows:
            key = tuple(sorted(row.items()))
            if key not in seen:
                seen.add(key)
                result.append(row)
        return result, 0, len(rows) - len(result), [action.kind.value]
    if action.kind == CleaningActionKind.DROP_ROWS_WITH_MISSING_TIME and time_column:
        result = [row for row in rows if not _is_missing(row.get(time_column, ""))]
        return result, 0, len(rows) - len(result), [action.kind.value]
    if action.kind == CleaningActionKind.SORT_BY_TIME and time_column:
        return sorted(
            rows,
            key=lambda row: (
                _datetime(row.get(time_column, "")) is None,
                _datetime(row.get(time_column, "")) or datetime.min.replace(tzinfo=UTC),
            ),
        ), 0, 0, [action.kind.value]
    if action.kind == CleaningActionKind.FILL_NUMERIC_MISSING and action.column:
        values = [_number(row.get(action.column, "")) for row in rows]
        known = [value for value in values if value is not None]
        if not known:
            return rows, 0, 0, []
        ordered = sorted(known)
        midpoint = len(ordered) // 2
        fill_value = (
            ordered[midpoint]
            if len(ordered) % 2
            else (ordered[midpoint - 1] + ordered[midpoint]) / 2
        )
        filled = 0
        for row in rows:
            if _is_missing(row.get(action.column, "")):
                row[action.column] = str(fill_value)
                filled += 1
        return rows, filled, 0, [action.kind.value]
    return rows, 0, 0, []


def execute_cleaning_plan(
    content: bytes,
    *,
    filename: str,
    plan: CleaningPlan,
    target_column: str | None = None,
    time_column: str | None = None,
) -> tuple[bytes, DataProfile, CleaningReport]:
    profile, rows = profile_dataset(content, filename=filename, target_column=target_column, time_column=time_column)
    if profile.profile_id != plan.profile_id:
        raise CleaningValidationError("cleaning plan does not belong to this data profile")
    applied: list[str] = []
    filled = removed = 0
    for action in plan.actions:
        rows, action_filled, action_removed, names = _apply_action(rows, action, profile.time_column)
        filled += action_filled
        removed += action_removed
        applied.extend(names)
    if not rows or any(not row for row in rows):
        raise CleaningValidationError("cleaning produced an empty or invalid dataset")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    final_profile, _ = profile_dataset(output.getvalue().encode("utf-8"), filename="cleaned.csv", target_column=profile.target_column, time_column=profile.time_column)
    blocking = [issue for issue in final_profile.issues if issue.blocking]
    report = CleaningReport(
        plan_id=plan.plan_id,
        rows_before=profile.row_count,
        rows_after=final_profile.row_count,
        missing_values_filled=filled,
        exact_duplicates_removed=removed,
        rows_dropped=removed,
        actions_applied=applied,
        issues=final_profile.issues,
        valid=not blocking,
    )
    if blocking:
        raise CleaningValidationError("cleaned dataset still has blocking validation issues")
    return output.getvalue().encode("utf-8"), final_profile, report
