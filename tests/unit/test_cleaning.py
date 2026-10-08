from forecasting_assistant.application.cleaning import (
    build_cleaning_plan,
    execute_cleaning_plan,
    profile_dataset,
)
from forecasting_assistant.domain.cleaning import CleaningActionKind


def test_profile_detects_types_missing_values_and_duplicates() -> None:
    content = (
        b"date,sales,store_id\n"
        b"2026-01-01,10,A\n"
        b"2026-01-02,,A\n"
        b"2026-01-02,,A\n"
    )
    profile, _ = profile_dataset(content, filename="sales.csv", target_column="sales")

    assert profile.time_column == "date"
    assert profile.target_column == "sales"
    assert profile.row_count == 3
    assert profile.exact_duplicate_rows == 1
    assert profile.columns[1].missing_count == 2
    assert profile.columns[1].detected_type.value == "integer"


def test_profile_supports_json_records() -> None:
    content = (
        b'[{"date":"2026-01-02","sales":20},'
        b'{"date":"2026-01-01","sales":10},'
        b'{"date":"2026-01-01","sales":10}]'
    )

    profile, _ = profile_dataset(content, filename="sales.json", target_column="sales")

    assert profile.format == "json"
    assert profile.row_count == 3
    assert profile.time_column == "date"
    assert profile.target_column == "sales"
    assert profile.exact_duplicate_rows == 1


def test_profile_supports_common_json_wrappers_and_columnar_records() -> None:
    wrapped = b'{"results":[{"date":"2026-01-01","sales":10}]}'
    columnar = b'{"date":["2026-01-01","2026-01-02"],"sales":[10,20]}'

    wrapped_profile, _ = profile_dataset(
        wrapped, filename="sales.json", target_column="sales"
    )
    columnar_profile, _ = profile_dataset(
        columnar, filename="sales.json", target_column="sales"
    )

    assert wrapped_profile.row_count == 1
    assert columnar_profile.row_count == 2
    assert columnar_profile.target_column == "sales"


def test_cleaning_plan_uses_temporary_median_imputation() -> None:
    profile, _ = profile_dataset(
        b"date,sales\n2026-01-01,10\n2026-01-02,\n",
        filename="sales.csv",
        target_column="sales",
    )
    plan = build_cleaning_plan(profile)
    action = next(item for item in plan.actions if item.kind == CleaningActionKind.FILL_NUMERIC_MISSING)
    assert action.approved is True
    assert "median" in action.reason


def test_execute_cleaning_plan_removes_exact_duplicates_and_preserves_raw_input() -> None:
    raw = b"date,sales\n2026-01-02,20\n2026-01-01,10\n2026-01-01,10\n"
    profile, _ = profile_dataset(raw, filename="sales.csv", target_column="sales")
    plan = build_cleaning_plan(profile)
    cleaned, final_profile, report = execute_cleaning_plan(
        raw, filename="sales.csv", plan=plan, target_column="sales"
    )

    assert raw == b"date,sales\n2026-01-02,20\n2026-01-01,10\n2026-01-01,10\n"
    assert cleaned.startswith(b"date,sales\n2026-01-01,10\n")
    assert final_profile.row_count == 2
    assert report.exact_duplicates_removed == 1
    assert report.valid is True
