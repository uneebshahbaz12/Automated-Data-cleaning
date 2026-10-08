# Current Forecasting Pipeline Workflow

## Overview

The project currently follows this workflow:

```text
User gives a short forecasting request
        |
        v
LLMREI requirements interview
        |
        v
Confirmed forecasting specification
        |
        +-------------------------------+
        |                               |
        v                               v
Dataset discovery/catalog search       User-provided dataset
        |                               |
        +---------------+---------------+
                        |
                        v
Dataset selection or upload
                        |
                        v
Raw dataset validation
                        |
                        v
Immutable raw dataset storage
                        |
                        v
Automatic profiling and cleaning
                        |
                        v
Immutable cleaned derived dataset
                        |
                        v
Forecasting/modeling stage
```

The project is designed so that the raw dataset is never overwritten. Cleaning
creates a separate derived version linked to the raw version.

## 1. Requirements elicitation

The user starts with a short description, for example:

> Forecast monthly product sales for the next 12 months using our sales data.

The LLMREI interview asks follow-up questions and extracts structured
forecasting requirements, such as:

- Forecasting intent.
- Problem statement.
- Business goal.
- Target variable.
- Target description and unit.
- Time column.
- Data frequency.
- Forecast horizon.
- Single-series or multi-series dataset type.
- Geography or entity.
- Dataset source.
- Forecast type.
- Evaluation metric.

The LLM returns structured objects. Python validates the returned values,
updates the dialogue state, decides which information is still missing, and
determines the next question.

The interview ends with a confirmation step. The user must confirm the
requirements before dataset discovery or fetching proceeds.

Relevant implementation:

- `src/forecasting_assistant/application/orchestrator.py`
- `src/forecasting_assistant/infrastructure/llm/openai_responses.py`
- `src/forecasting_assistant/domain/models.py`

## 2. Dataset discovery or user-provided data

After requirements are confirmed, the system obtains a dataset in one of two
ways.

### Catalog or external source

The confirmed forecasting specification is converted into a dataset search
query. Configured source adapters search available catalogs and return dataset
candidates.

Candidates are ranked using factors such as:

- Match with the forecasting target.
- Authority and provenance.
- Geography and time coverage.
- Frequency and history length.
- Quality information.
- License compatibility.
- Access reliability.

The system displays recommendations. The user explicitly confirms one dataset.

### User-provided dataset

For an uploaded dataset, the system records information such as:

- File name.
- File format.
- Source reference.
- Columns.
- File size.
- Content checksum.

The uploaded file follows the same validation and cleaning path after it is
accepted.

Relevant implementation:

- `src/forecasting_assistant/application/dataset_discovery.py`
- `src/forecasting_assistant/application/dataset_query.py`
- `src/forecasting_assistant/application/dataset_ranking.py`
- `src/forecasting_assistant/infrastructure/datasets/registry.py`

## 3. Dataset fetching and safety validation

When the confirmed dataset is fetched, the source adapter downloads the
payload. Before storage, Python performs safety checks:

- The payload must not be empty.
- Executable payloads are rejected.
- Unsafe or malformed archives are rejected.
- Archive path traversal is rejected.
- Archive size and member-count limits are enforced.
- Dataset payload validation checks the expected data shape and quality.

The original bytes are stored in the content-addressed object store. The
stored `DatasetVersion` includes:

- SHA-256 checksum.
- Storage path.
- Byte size.
- Content type.
- Source version.
- Candidate identity.
- License snapshot.
- Quality information.
- Retrieval timestamp.

This raw version is immutable and is the source of truth for auditability.

Relevant implementation:

- `src/forecasting_assistant/application/dataset_discovery.py`
- `src/forecasting_assistant/application/dataset_validation.py`
- `src/forecasting_assistant/infrastructure/datasets/object_store.py`
- `src/forecasting_assistant/domain/datasets.py`

## 4. Automatic profiling

After a supported dataset is stored, the fetch workflow automatically profiles
it. No separate manual cleaning command is required.

The profiler currently supports:

- CSV.
- JSON lists of objects.
- JSON objects containing common `data` or `records` wrappers.

The profiler measures:

- Row and column counts.
- Column names.
- Empty values.
- Missing-value counts and ratios.
- Unique-value counts.
- Sample values.
- Numeric minimum and maximum.
- Candidate data types.
- Candidate column roles.
- Candidate time column.
- Candidate target column.
- Exact duplicate rows.
- Duplicate timestamps.
- Invalid timestamps.
- Missing time periods.
- Basic hourly, daily, weekly, or monthly frequency.

Current type detection includes:

- Empty.
- Boolean.
- Integer.
- Numeric.
- Date/time.
- Categorical.
- Text.
- Mixed.

The profiler is deterministic Python code. It does not need the OpenAI API key
to calculate these facts.

Relevant implementation:

- `src/forecasting_assistant/application/cleaning.py`
- `src/forecasting_assistant/domain/cleaning.py`

## 5. Cleaning-plan generation

The profile is converted into a cleaning plan. Each action has:

- Action type.
- Column, when applicable.
- Risk level.
- Reason.
- Whether it is automatic.
- Whether it is approved.

Low-risk actions currently approved automatically include:

- Normalize surrounding text whitespace.
- Validate and normalize the time column.
- Sort rows chronologically.
- Remove exact duplicate rows.
- Flag target outliers without deleting them.

As a temporary policy, numeric missing values are filled with the column median.
This policy is provisional and should be replaced or made user-configurable after
supervisor approval.

The raw dataset is not modified while the plan is generated.

## 6. Automatic cleaning execution

The fetch workflow executes the approved actions automatically:

1. Read the raw bytes.
2. Recreate the profile.
3. Verify that the plan belongs to the same raw content.
4. Apply only approved actions.
5. Write the cleaned result.
6. Profile the cleaned result again.
7. Reject the result if blocking validation issues remain.
8. Store the cleaned result as a derived dataset version.

The cleaning report records:

- Rows before cleaning.
- Rows after cleaning.
- Number of missing values filled.
- Number of exact duplicates removed.
- Rows dropped.
- Actions applied.
- Remaining issues.
- Overall validity.

The cleaned version contains:

- `parent_version_id` pointing to the raw dataset.
- The original profile.
- The final profile.
- The cleaning plan.
- The cleaning report.
- Cleaning status.

The raw and cleaned files have separate content-addressed storage paths and
checksums.

## 7. What happens when cleaning cannot run

For formats that are not currently supported by the cleaning service, the raw
dataset is preserved and the version metadata records:

```text
cleaning_status = unsupported_format
```

The current cleaning implementation does not yet read Excel or Parquet files.
Those formats require additional reader dependencies and format-specific
validation.

If a blocking issue remains after cleaning, the operation fails closed instead
of returning a success-shaped result.

## 8. Role of the LLM in data cleaning

The current automatic profiling and low-risk transformations are deterministic
Python operations. The LLM is not required to decide whether a value is
numeric, missing, duplicated, or parseable as a date.

In the planned semantic cleaning layer, the LLM may help interpret ambiguity,
for example:

- Whether `revenue`, `amount`, or `sales_value` is the forecasting target.
- Whether `recorded_at` is the observation-time column.
- Whether `store_code` is a series identifier.
- How to explain a cleaning warning in simple language.

The LLM should suggest or explain. Python should validate the suggestion, and
the user should approve high-impact changes.

The API key is used by the backend only. It should never be placed in browser
code, source files, logs, or this documentation.

## 9. Current end-to-end commands

The virtual environment does not need to be activated. On PowerShell, run the
project executables directly:

```powershell
cd C:\Users\hp\fpy
& ".\.venv\Scripts\forecast-elicitation.exe" interview
```

After the interview and dataset confirmation, fetch the selected dataset:

```powershell
& ".\.venv\Scripts\forecast-elicitation.exe" fetch-dataset YOUR_SELECTION_ID
```

The `fetch-dataset` command now triggers automatic profiling and cleaning for
supported CSV and JSON datasets.

The complete test suite can be run without activating the environment:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

## 10. What is implemented now

Implemented:

- LLM-based requirements interview.
- Structured forecasting specification.
- Dataset discovery and ranking.
- Explicit dataset confirmation.
- Dataset fetching.
- Raw payload safety validation.
- Immutable raw dataset storage.
- CSV and JSON profiling.
- Type and role detection.
- Missing-value and duplicate detection.
- Timestamp and frequency analysis.
- Automatic low-risk cleaning.
- Post-cleaning validation.
- Immutable cleaned derived versions.
- Raw-to-cleaned lineage.
- Cleaning metadata and reports.
- Automated tests.

## 11. Remaining work

The next improvements are:

1. Add a user-facing profile and cleaning-report command.
2. Add web UI/API screens for reviewing the profile and cleaning plan.
3. Add explicit approval flow for medium- and high-risk actions.
4. Add Excel and Parquet readers.
5. Confirm and make configurable the missing-value policy. The current temporary
   policy uses the column median.
6. Replace the temporary numeric fill behavior with explicit policies such as
   interpolation, forward fill, backward fill, or no fill.
7. Add duplicate-timestamp resolution policies.
8. Add stronger outlier, leakage, and range checks.
9. Add LLM-assisted semantic interpretation with redacted profile input.
10. Connect the cleaned `DatasetVersion` to the forecasting/model-training
    stage.

## Summary

The current system is a two-stage pipeline:

```text
LLMREI extracts and confirms forecasting requirements
        +
Python fetches, profiles, validates, and cleans the dataset
```

The dataset is not cleaned during the interview. The interview must finish and
the dataset must be selected or uploaded first. Once the dataset is fetched,
supported CSV and JSON data are automatically profiled and cleaned. The
original raw data remains unchanged, and the cleaned data is stored as a
separate, auditable derived version.
