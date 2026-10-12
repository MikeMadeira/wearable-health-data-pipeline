# INC-002: Walking Skeleton

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-001;
uses its approved G1 decisions in `reports/INC-001/profiling-findings.md` (Section 7).

## Goal

A single CLI command that rebuilds, from the immutable raw exports, a manifest of every
raw input file, silver tables for the wellness survey and participants, persisted
quality results and a human-readable quality report, and a first
`gold_clinical_daily` keyed by (`participant_id`, `local_date`) loaded by MERGE into
DuckDB tables.

It proves the layer layout, quality mechanism, idempotent rerun and local storage
choice that INC-003 to INC-005 extend.

## Requirements

### CLI, storage and failure behavior

- One command, for example
  `python -m wearable_pipeline run --data-dir data --output-dir output`, rebuilds all
  derived layers deterministically from raw.
- Output goes into one DuckDB database file under the output directory. Each layer is a
  set of native DuckDB tables (manifest, silver, quality, gold). The quality report is
  written to `output/quality/report.md`.
- The command exits non-zero only on I/O or contract failures (for example a missing or
  unreadable input file). Field-level data defects never fail the run.

### Bronze: raw files and manifest

- The unchanged raw files under `data/` act as the local bronze layer, analogous to the
  S3 raw prefix in the cloud proposal. Per-minute JSON is not copied or normalized into
  DuckDB in this increment.
- A manifest table records every file under `data/` (source, relative path, size in
  bytes, SHA-256, and a record count where it can be measured cheaply, otherwise null).
  `data_dictionary.md` is included as a documentation file. No hard file count is
  asserted; counts are verified against the tree at implementation.
- The manifest is only recorded in this increment. File-change detection belongs to
  INC-006.

### Silver

- `silver_wellness_survey` keeps the raw values and adds the normalized date, an orphan
  flag and per-row quality flags. No value is changed or dropped. It is unique on
  (`participant_id`, `date`).
- `silver_participants` keeps the `participants.csv` columns plus validity flags. It is
  unique on `participant_id`.
- P999 stays in silver with an orphan flag. It is excluded only from outputs that
  require an enrolled-participant join (G1 decision 5).
- No silver is built for device metadata or for the wearable sources.

### Quality

- `quality_results` holds one row per (run id, rule, source) with checked and failed
  counts, the handling (flag, quarantine, and so on) and the severity.
- `quality_rejects` holds the affected row identifier, rule, reason and raw values.
- Skeleton rules: survey orphan participant (T-01, 14 rows for P999 in the profiled
  data); duplicate survey participant-day; survey scores outside the data-dictionary
  ranges; survey date outside the study window; participants key uniqueness.
- Rules that find nothing are still run and reported with zero counts.
- The handling follows the G1 quality policy: preserve raw rows, flag or withhold only
  the affected field, never fail or drop a whole day for a field-level defect.

### Gold

- `gold_clinical_daily` is keyed by (`participant_id`, `local_date`). It carries the four
  survey scores (fatigue, stress, readiness, sleep quality), a survey-present flag, and
  quality and valid-count fields.
- It has one row per enrolled participant per study day (50 x 28 = 1,400 on the profiled
  data), built from the participants x study-window calendar. Later slices add sleep,
  steps and heart-rate columns to the existing keys.
- P999 is excluded from gold.
- It is loaded by MERGE on (`participant_id`, `local_date`): matched rows update the
  survey and quality columns only, unmatched rows are inserted.
- A rerun on unchanged inputs yields identical table contents with no duplicate keys.
  Rows that the source no longer produces are not silently deleted. This is flagged for
  INC-006 to decide.

### Determinism

- No wall-clock time or random identifiers appear in any table or in the report.
- The run identifier is derived deterministically (for example a hash of the manifest
  checksums), so a rerun on unchanged inputs gives identical table contents and an
  identical report.

## Constraints

- Add DuckDB as the only new runtime dependency, justified as an embedded SQL engine
  with MERGE and transactions that is adequate for the data size. Update `pyproject.toml`
  and `uv.lock`. No pandas and no other packages. The pinned Python 3.12 environment is
  unchanged.
- Deliberate deviation from the vertical-slice plan: the plan's `bronze_*` tables are not
  created in this increment. The manifest replaces them, and INC-003 to INC-005 read raw
  files directly into their silver tables. The human accepted this at planning review and
  the plan and initiative wording were updated to match.
- Do not change the harness (`scripts/`, `.github/`), the uv/pyenv setup beyond adding
  DuckDB, the README structure, `data/`, or `tests/test_orchestrator.py`. No public API
  exists yet in `src/` to preserve.
- Raw files are read-only. Tests use small synthetic fixtures, never participant data.
- Out of scope:
  - sleep, steps and heart-rate silver and gold columns;
  - device-metadata silver or gold, and `gold_participant_devices` (left to the
    INIT-001 scope decision);
  - weekly and cohort outputs;
  - incremental mode and file-change detection;
  - Docker or container changes;
  - README, diagram and query output;
  - cloud deployment;
  - notebooks.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- grains and keys of `silver_wellness_survey`, `silver_participants`, the manifest, the
  quality tables and `gold_clinical_daily`;
- the enrolled-participant join, with the P999 orphan retained in silver and absent from
  gold;
- one gold row per participant per study day;
- each skeleton rule (score range, duplicate, out-of-window, orphan) with a failing and
  a passing case, and rules with zero hits still reported;
- raw values preserved in silver and in the rejects;
- MERGE update versus insert;
- rerun idempotency (identical contents, no duplicate keys), and a changed survey value
  updating exactly one gold row;
- deterministic run id and report;
- raw input files unchanged byte for byte;
- non-zero exit only on I/O or contract failure.

Harness gates:

- `python -m pytest -q` passes.
- The container gate (Docker build and in-container pytest) passes with DuckDB installed
  from `pyproject.toml` and `uv.lock`, and the lock is consistent.
- Changes are confined to `src/`, `tests/`, `specs/`, `pyproject.toml`, `uv.lock` and
  `reports/INC-002/`.

Real-data evidence, run by the human and recorded in the implementation report:

- one command run;
- 1,400 gold rows (50 x 28);
- 1,414 survey rows in silver, with the 14 P999 orphans flagged;
- `quality_results` counts matching the INC-001 findings (T-01 = 14);
- a second run produces identical tables and report;
- `data/` SHA-256 unchanged.

Human gate (G4): the human ratifies local storage (native DuckDB tables with MERGE) at
the INC-002 review, once the skeleton shows MERGE and rerun working. The daily-freshness
mechanism is ratified before INC-006 and recorded in the findings and plan.
