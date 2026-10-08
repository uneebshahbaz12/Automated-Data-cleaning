# Automated Data Cleaning Workflow

## Purpose

The next stage of the forecasting project is to inspect a confirmed dataset, understand
its structure, identify data-quality problems, propose cleaning actions, apply safe
transformations, and save a cleaned version without changing the original file.

The important design decision is:

> The LLM helps us understand and explain ambiguous data. Deterministic Python code
> performs the actual measurements and data transformations.

This keeps the workflow reliable and auditable. The LLM must not be allowed to
silently rewrite values or delete rows.

## Where the current project ends

The current project already implements:

1. Requirement elicitation.
2. A validated and explicitly confirmed `ForecastingSpecification`.
3. Dataset discovery and ranking.
4. Explicit dataset selection.
5. Secure dataset fetching.
6. Raw payload validation.
7. Immutable, content-addressed storage of the raw dataset.

The cleaning stage starts after step 7.

```text
Confirmed forecasting specification
        |
        v
Dataset selected and fetched
        |
        v
Immutable raw dataset version
        |
        v
Data profiling and schema detection       <-- next stage
        |
        v
Cleaning plan
        |
        v
Cleaning execution
        |
        v
Post-cleaning validation
        |
        v
Immutable cleaned dataset version
        |
        v
Forecast model training and evaluation
```

## Will the same OpenAI API key be used?

Yes. The cleaning stage can use the same OpenAI account and the same
`OPENAI_API_KEY` configuration already used by the requirement-elicitation system.

The key is used by the backend, never by browser code:

```dotenv
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4.1-mini
```

The API key itself is not used to inspect the file directly. The application first
profiles the dataset locally using deterministic Python code. It then sends a
carefully limited, redacted profile to the LLM when interpretation or explanation is
useful.

The LLM should normally receive metadata such as:

- column names
- inferred data types
- sample values with sensitive values redacted
- row and column counts
- missing-value percentages
- candidate timestamp columns
- candidate target columns
- detected frequency
- duplicate counts
- outlier summaries

It should not receive raw credentials. Sensitive business data should also be
minimized or redacted before it is sent to an external provider. The application
should make this configurable for privacy-sensitive datasets.

## Simple explanation of the responsibilities

### What Python does

Python reads the dataset and measures facts:

- how many rows and columns exist
- which values are empty
- whether values can be parsed as dates
- whether values are numeric
- whether timestamps are duplicated
- whether dates are missing
- whether values are outside configured bounds
- whether a series has an irregular frequency
- whether suspicious outliers exist

Python also performs approved transformations:

- trimming whitespace
- converting types
- sorting timestamps
- filling or removing values according to a policy
- removing exact duplicate rows
- inserting missing time periods
- resampling and aggregation
- saving the cleaned result

### What the LLM does

The LLM helps interpret meaning that cannot always be established from values alone:

- Which column is probably the forecasting target?
- Which date-like column represents observation time?
- Does `temp_avg`, `temperature`, or `value` describe the target?
- Is a column an identifier, a category, or a numeric measurement?
- Is a suspicious value likely to be a legitimate event or a data error?
- What does a technical cleaning report mean in simple language?

The LLM proposes or explains. It does not become the final authority over data
changes.

### What the user does

The user confirms decisions that could materially change the data:

- which column is the target
- which column is time
- what the expected frequency is
- how missing target values should be handled
- whether outliers should be retained, flagged, capped, or replaced
- how duplicate timestamps should be resolved
- whether resampling is allowed

## Complete workflow

## Step 1: Start with the immutable raw dataset

After the user confirms a dataset, the existing dataset service fetches it and saves
an immutable raw `DatasetVersion`.

The raw version includes:

- candidate ID
- source and query identity
- file size
- content type
- SHA-256 checksum
- retrieval time
- license snapshot
- source version such as an ETag

The raw file is never overwritten. Every cleaned result is stored as a new derived
version with a link to its raw parent.

## Step 2: Load the dataset safely

The cleaning service loads supported formats such as:

- CSV
- JSON
- Excel
- Parquet

Before loading, it applies safety limits:

- maximum file size
- allowed file extensions
- archive safety checks
- executable-content checks
- maximum number of rows or cells where appropriate

If the format cannot be read safely, the workflow stops with a clear error. It does
not create a successful-looking empty dataset.

## Step 3: Detect the schema and data types

Schema detection means understanding the shape of the table.

For every column, the profiler calculates facts such as:

```text
Column: date
- detected type: datetime
- example values: 2025-01-01, 2025-01-02
- missing values: 0%
- unique values: 365
- likely role: time column

Column: sales
- detected type: numeric
- example values: 120.5, 135.0, 98.2
- missing values: 2.1%
- minimum: 0
- maximum: 950
- likely role: target or measure

Column: store_id
- detected type: categorical identifier
- example values: A01, A02, A03
- missing values: 0%
- unique values: 12
- likely role: series identifier
```

### How type detection works

The deterministic profiler checks:

1. Whether the column is empty or constant.
2. Whether values can be parsed as integers or decimals.
3. Whether values can be parsed as dates or timestamps.
4. Whether values are mostly true/false values.
5. Whether values are short repeated labels.
6. Whether values are unique identifiers.
7. Whether a mixed column contains incompatible values.

For example:

```text
"12", "15", "18"        -> numeric
"2026-01-01", "2026-01-02" -> datetime
"yes", "no", "yes"      -> boolean-like
"Karachi", "Lahore"     -> categorical
"store-001", "store-002" -> identifier-like
```

The profiler records confidence and evidence. It should not force every column into
a type when the data is ambiguous.

## Step 4: Use the LLM for semantic interpretation

After deterministic profiling, the application can send a structured profile to the
same OpenAI client pattern already used by the project.

Example request to the LLM:

```json
{
  "forecasting_specification": {
    "target_description": "daily electricity demand",
    "target_column": null,
    "time_column": null,
    "frequency": "daily"
  },
  "columns": [
    {
      "name": "reading_date",
      "detected_type": "datetime",
      "missing_ratio": 0.0,
      "sample_values": ["2026-01-01", "2026-01-02"]
    },
    {
      "name": "load_mw",
      "detected_type": "numeric",
      "missing_ratio": 0.01,
      "sample_values": ["120.4", "131.2"]
    }
  ],
  "question": "Which column is most likely the target and which is the time column?"
}
```

Example structured response:

```json
{
  "target_column": "load_mw",
  "time_column": "reading_date",
  "confidence": 0.98,
  "reason": "load_mw describes electricity demand and reading_date is datetime-like",
  "needs_user_confirmation": false
}
```

The application then checks the response:

- Does the named column actually exist?
- Is the selected target numeric or otherwise usable?
- Is the selected time column actually datetime-like?
- Does the answer agree with the confirmed specification?
- Is the confidence sufficient?
- Did the LLM invent a column?

If the response fails validation, the application ignores it and asks the user or
uses the deterministic result where safe.

## Step 5: Generate a data profile

The profile is a structured report, not just an LLM response.

It should contain:

- file metadata
- row and column counts
- column type results
- candidate roles
- missing-value statistics
- duplicate statistics
- timestamp statistics
- frequency estimate
- range and domain checks
- outlier summary
- possible leakage indicators
- warnings and confidence levels

Example:

```json
{
  "rows": 10000,
  "columns": 6,
  "time_column": "date",
  "target_column": "sales",
  "frequency": "daily",
  "issues": {
    "missing_target_ratio": 0.021,
    "duplicate_time_rows": 13,
    "missing_periods": 4,
    "outliers": 18,
    "invalid_values": 2
  }
}
```

## Step 6: Detect missing values

The profiler checks missingness separately for:

- the time column
- the target column
- each series identifier
- past covariates
- known future covariates
- other columns

It also checks whether missing values occur:

- randomly
- in a short isolated gap
- in a long consecutive block
- at the start of the history
- at the end of the history
- only for one series

The cleaning policy depends on the column role:

| Situation | Possible action |
|---|---|
| Missing time value | reject or remove the row |
| One short missing target gap | interpolate or flag |
| Long missing target block | usually flag for review |
| Missing categorical ID | reject or mark unknown |
| Missing past covariate | interpolate, fill, or leave missing |
| Missing future covariate | do not invent without an approved policy |

The LLM may explain the options, but the selected policy is applied by Python.

## Step 7: Detect duplicates

The service checks:

- exact duplicate rows
- duplicate timestamps in a single series
- duplicate `(series_id, timestamp)` combinations in panel data
- duplicates with identical values
- duplicates with conflicting values

Examples:

```text
Same timestamp, same sales value
-> likely safe duplicate; may be removed

Same timestamp, sales = 100 and sales = 150
-> conflicting duplicate; requires a rule or user decision
```

Possible policies include:

- keep first
- keep latest
- sum
- mean
- last value
- reject conflicting duplicates
- keep and flag

The policy comes from the specification or an explicit user decision.

## Step 8: Normalize and validate timestamps

The service:

1. Parses timestamp values.
2. Applies the configured timezone.
3. Sorts rows chronologically.
4. Detects the observed frequency.
5. Compares it with the required frequency.
6. Finds missing periods.
7. Handles duplicate periods.
8. Optionally inserts missing periods as explicit missing rows.

For daily data:

```text
2026-01-01
2026-01-02
2026-01-04
```

The system identifies `2026-01-03` as a missing period. It does not silently assume
that the missing day did not exist.

## Step 9: Validate ranges and domain rules

The cleaning service uses the forecasting specification and profile to check rules
such as:

- target must not be negative
- percentages must be between 0 and 100
- probabilities must be between 0 and 1
- latitude must be between -90 and 90
- longitude must be between -180 and 180
- measurements must be within configured bounds

An invalid value is first flagged. It is only changed when a cleaning policy allows
that change.

## Step 10: Detect outliers

Outlier detection can use:

- interquartile range
- z-score
- robust median/MAD score
- rolling-window deviation
- seasonal deviation
- domain bounds
- unusually large rate of change

The first implementation should default to:

```text
detect and flag, but do not delete
```

This is important because a spike can be:

- an error
- a holiday
- a promotion
- a weather event
- a market crash
- a real operational change

The LLM can help explain the likely context, but it cannot decide to delete the
value without an approved rule.

## Step 11: Decide whether resampling is needed

If the source frequency differs from the requested forecasting frequency, the service
creates a proposed resampling action.

Examples:

- hourly to daily
- daily to weekly
- daily to monthly

The aggregation method must match the meaning of the target:

- `sum` for sales volume
- `mean` for temperature
- `last` for account balance or closing price
- `min` or `max` for extremes

If the aggregation method is unknown, the system asks the user instead of guessing.

## Step 12: Check for forecasting leakage

Leakage occurs when information from the future is accidentally used to predict the
past or present.

The service checks for:

- columns that contain future target values
- rolling calculations that include future rows
- unshifted target-derived features
- covariates unavailable at prediction time
- timestamps that are not ordered correctly

Leakage is a blocking issue. The dataset should not proceed to model training until
it is resolved.

## Step 13: Create a cleaning plan

The profile becomes a proposed `CleaningPlan`.

Example:

```json
{
  "actions": [
    {
      "column": "date",
      "action": "parse_datetime",
      "risk": "low",
      "automatic": true
    },
    {
      "column": "sales",
      "action": "linear_interpolation",
      "reason": "3 isolated missing values",
      "risk": "medium",
      "automatic": false
    },
    {
      "column": "date",
      "action": "insert_missing_daily_periods",
      "risk": "medium",
      "automatic": false
    },
    {
      "column": "sales",
      "action": "flag_outliers",
      "risk": "low",
      "automatic": true
    }
  ]
}
```

Each action should state:

- affected column or series
- reason
- method
- number of affected rows
- risk level
- whether approval is required
- expected result

## Step 14: Ask for approval when required

Low-risk actions can run automatically. High-impact actions require confirmation.

### Safe automatic actions

- trim whitespace
- normalize column names
- parse clearly valid timestamps
- convert clearly numeric values
- sort by time
- remove exact duplicate rows
- flag outliers
- create a profile

### Approval-required actions

- fill missing target values
- delete rows
- remove outliers
- resample data
- aggregate conflicting duplicates
- change target values
- fill future covariates
- drop columns

The user should see a plain-language summary such as:

```text
I found 3 missing sales values, 13 duplicate rows, and 18 unusual sales values.
I can sort the timestamps and flag the unusual values automatically.
Filling the 3 missing sales values may affect the forecast. Should I interpolate
them, leave them missing, or review them manually?
```

## Step 15: Apply the cleaning plan

Python applies only approved actions in a deterministic order:

1. Preserve the raw dataset.
2. Normalize column names and basic representations.
3. Parse and validate timestamps.
4. Sort by series and timestamp.
5. Resolve exact duplicates.
6. Insert or align missing periods if approved.
7. Apply missing-value policies.
8. Apply approved range corrections.
9. Apply approved outlier actions.
10. Apply approved resampling.
11. Run final validation.

Every action is recorded in an audit report.

## Step 16: Validate the cleaned dataset

After cleaning, the service runs the profile again and compares before and after:

```text
Rows before:                    10,000
Rows after:                      9,987
Missing target values before:       42
Missing target values after:         0
Exact duplicates removed:           13
Outliers flagged:                   18
Timestamp frequency:            daily
Leakage detected:                false
Unresolved blocking issues:          0
```

The cleaned dataset is accepted only if:

- the target column still exists
- the time column still exists
- timestamps are valid
- the required frequency is satisfied
- no blocking leakage exists
- the result is not empty
- the transformations match the approved plan

## Step 17: Save a versioned cleaned dataset

The cleaned file is saved as a derived `DatasetVersion`.

The derived version stores:

- raw parent version ID
- cleaned file checksum
- storage path
- content type
- cleaning-plan ID
- cleaning-report ID
- transformations applied
- user approvals
- unresolved warnings
- creation time

The raw version remains available for comparison and reproducibility.

## Step 18: Pass the cleaned version to forecasting

Only the validated cleaned version is sent to the future modeling stage:

```text
Cleaned dataset
  -> feature preparation
  -> train/validation split
  -> backtesting
  -> model selection
  -> forecasting
```

The model-training stage should never silently reload the original raw file when a
cleaned version has been approved.

## Recommended architecture

The future implementation can be organized as:

```text
src/forecasting_assistant/
  cleaning/
    models.py
    profiler.py
    schema_detection.py
    missing_values.py
    duplicates.py
    timestamps.py
    resampling.py
    outliers.py
    leakage.py
    policies.py
    pipeline.py
    reports.py
```

Suggested objects:

- `ColumnProfile`
- `DataProfile`
- `CleaningIssue`
- `CleaningAction`
- `CleaningPlan`
- `CleaningApproval`
- `CleaningReport`

## Recommended first implementation

Implement the first version in this order:

1. Load CSV and JSON safely.
2. Create deterministic column profiles.
3. Detect candidate time, target, and identifier columns.
4. Report missing values.
5. Detect exact duplicates and time duplicates.
6. Parse and regularize timestamps.
7. Validate target ranges.
8. Generate a cleaning plan.
9. Automatically apply only low-risk actions.
10. Require approval for imputing, deleting, resampling, and outlier replacement.
11. Save the cleaned data as a derived dataset version.
12. Add Excel and Parquet support.
13. Add leakage detection and advanced outlier detection.

## Final design rule

The safest division of responsibility is:

```text
Python measures facts.
LLM interprets ambiguous meaning and explains results.
User approves high-impact decisions.
Python applies approved transformations.
SQLite and object storage preserve the audit trail and versions.
```

This approach lets the project benefit from the same OpenAI API key and LLM
infrastructure already used for LLMREI while keeping data cleaning reproducible,
testable, and safe.
