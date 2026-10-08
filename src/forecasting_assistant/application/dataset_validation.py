from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from typing import Any

from forecasting_assistant.domain.datasets import (
    DatasetCandidate,
    DatasetSearchQuery,
    QualityReport,
)


class DatasetPayloadValidationError(ValueError):
    pass


def _json_point_count(payload: Any) -> int | None:
    if isinstance(payload, list):
        # World Bank-style responses are [metadata, [records...]].
        if len(payload) == 2 and isinstance(payload[1], list):
            records = payload[1]
            return len(records) if all(isinstance(row, Mapping) for row in records) else None
        return len(payload) if all(isinstance(row, Mapping) for row in payload) else None
    if not isinstance(payload, Mapping):
        return None
    features = payload.get("features")
    if isinstance(features, list):
        return len(features) if all(isinstance(row, Mapping) for row in features) else None
    for key in ("data", "results", "value", "items", "messages"):
        values = payload.get(key)
        if isinstance(values, list):
            return len(values) if all(isinstance(row, Mapping) for row in values) else None
        if isinstance(values, Mapping):
            nested_count = _json_point_count(values)
            if nested_count is not None:
                return nested_count
    properties = payload.get("properties")
    if isinstance(properties, Mapping):
        parameter = properties.get("parameter")
        if isinstance(parameter, Mapping):
            series_lengths = [
                len(value) for value in parameter.values() if isinstance(value, Mapping)
            ]
            if series_lengths:
                return max(series_lengths)
    values = list(payload.values())
    if values and all(isinstance(value, list) for value in values):
        lengths = {len(value) for value in values}
        if len(lengths) == 1:
            return lengths.pop()
    # A single JSON object is a valid one-row dataset. The minimum-point
    # requirement will still reject it when the forecasting request needs more.
    if payload:
        return 1
    return None


def validate_dataset_payload(
    content: bytes,
    *,
    content_type: str | None,
    candidate: DatasetCandidate,
    query: DatasetSearchQuery,
) -> QualityReport:
    normalized_type = (content_type or "").split(";", 1)[0].casefold()
    if normalized_type == "text/html" or content.lstrip().lower().startswith(b"<!doctype html"):
        raise DatasetPayloadValidationError("dataset endpoint returned an HTML page, not data")

    if normalized_type in {"application/json", "application/geo+json"} or content.lstrip().startswith(
        (b"{", b"[")
    ):
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DatasetPayloadValidationError("dataset JSON is malformed") from error
        if not isinstance(payload, (dict, list)) or not payload:
            raise DatasetPayloadValidationError("dataset JSON has no records")
        points = _json_point_count(payload)
        if points is not None and points < query.minimum_training_points:
            raise DatasetPayloadValidationError(
                "dataset has fewer than the minimum required training points"
            )
        return QualityReport(
            schema_valid=True,
            integrity_valid=True,
            training_points=points,
            warnings=[] if points is not None else ["record count requires adapter-specific validation"],
        )

    looks_csv = normalized_type in {"text/csv", "application/csv"} or b"," in content[:4096]
    if looks_csv:
        try:
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or len(reader.fieldnames) < 2:
                raise DatasetPayloadValidationError("dataset CSV does not contain a usable schema")
            rows = list(reader)
        except UnicodeDecodeError as error:
            raise DatasetPayloadValidationError("dataset CSV is not valid UTF-8") from error
        if len(rows) < query.minimum_training_points:
            raise DatasetPayloadValidationError(
                "dataset has fewer than the minimum required training points"
            )
        missing = sum(
            1
            for row in rows
            for value in row.values()
            if value is None or not str(value).strip()
        )
        cells = max(1, len(rows) * len(reader.fieldnames))
        return QualityReport(
            missing_ratio=missing / cells,
            schema_valid=True,
            integrity_valid=True,
            training_points=len(rows),
        )

    if content.startswith(b"PK\x03\x04"):
        return QualityReport(
            integrity_valid=True,
            warnings=["archive contents require format-specific schema validation"],
        )
    if normalized_type in {
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        if candidate.acquisition_mode.value == "publication":
            raise DatasetPayloadValidationError(
                "publication tables require an approved extraction template and human QA"
            )
        return QualityReport(
            integrity_valid=True,
            warnings=["binary table requires adapter-specific schema validation"],
        )
    raise DatasetPayloadValidationError("dataset payload format is unsupported")
