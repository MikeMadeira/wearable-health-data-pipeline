# INC-007: Weekly Biostatistics Table

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-006
(`intents/INC-006-incremental-mode-and-compare.md`); uses the G2 direction in
`reports/INC-001/profiling-findings.md` (Section 7, items 6 and 7): study-relative
seven-day blocks anchored on 2026-01-08 and a distinct wide participant-week table. The
exact measures and the coverage threshold are decided at the planning gate (see Planning
Decisions).

## Goal

The pipeline produces `gold_biostat_weekly`, a participant-week table with a schema
distinct from `gold_clinical_daily`, for the biostatistics team's weekly workflow. It is
derived from the silver layers, never from raw files, is written by every run, and stays
correct under reruns, corrections and incremental runs.

## Boundary

One code slice: the weekly table and its incremental support through the INC-006
`DerivedTable` extension point. The chronotype cohort table, the container and the
documentation are separate increments.

## Requirements

### Table, grain and week definition

- Table `gold_biostat_weekly`, written on every run and MERGEd on
  (`participant_id`, `week_id`).
- One row per enrolled participant and week: on the default 28-day window that is all
  enrolled participants x 4 weeks = 200 rows.
- A week is a study-relative seven-day block anchored on the study start (2026-01-08 by
  default): Jan 8-14, Jan 15-21, Jan 22-28 and Jan 29-Feb 4.
- Identifying columns: `participant_id`, `week_id`, `period_start_date`,
  `period_end_date`.
- The window is configurable. When `study_days` is not a multiple of 7, only whole
  seven-day weeks inside the window are produced and a trailing partial block is not
  emitted. The planner documents this in the spec.
- The column set and naming differ from `gold_clinical_daily`: this is a weekly
  biostatistics schema, not a daily table with a week column.

### Source and measures

- The table is derived from silver (not from `gold_clinical_daily`). The weekly
  measures use the same silver values, quality flags and withheld-value rules as the daily
  table.
- Where a weekly total or mean has a daily counterpart, it is consistent with the
  `gold_clinical_daily` values for the same days. The planner documents any intended
  difference in the spec.
- The measures are proposed by the planner from the sleep, steps, heart-rate and survey
  data available, and ratified by the human at the planning gate.

### Coverage and partial weeks

- A weekly value is computed from the valid data available; missing minutes and days are
  never imputed.
- The row exposes coverage: `valid_days` and valid counts per source, so a partial week is
  visible and a missing source is not a measured value.
- The minimum coverage under which a weekly aggregate is unavailable is proposed by the
  planner and ratified by the human at the planning gate.

### Quality outputs

- The weekly table adds no quality rules; its coverage columns expose the effects of the
  daily rules. `quality_results`, `quality_rejects`, the report and `run_id` are
  unchanged unless the planner shows a need and flags it for the human's decision.

### Reruns and incremental mode

- A rerun on the same inputs is idempotent: identical rows, no duplicates.
- The weekly table plugs into the INC-006 `DerivedTable` extension point. Incremental mode
  recomputes only the participant-weeks that contain an affected day. Its output, including
  `run_id` and the report, equals a full rebuild's on the same inputs.
- A corrected single record changes exactly one weekly row. A no-op incremental run
  changes nothing.
- A change to a shared input or to the study window follows the INC-006 shared-input path
  and gives the same result as a full rebuild.
- A failed run leaves the previous output unchanged, as in INC-006.

## Constraints

- Do not change: `gold_clinical_daily` columns and values, the silver tables, `run_id`
  derivation, the existing `run` and `compare` CLI surface, existing public signatures, the
  harness (`scripts/`, `.github/`), `data/`.
- No new runtime dependency: standard library plus DuckDB.
- Existing tests that assert the table list or a behavior this increment extends may be
  updated by this increment. No other test is weakened.
- Out of scope:
  - the chronotype cohort table (INC-008);
  - the container and the documentation (INC-009, INC-010);
  - statistical modeling and machine learning;
  - new silver tables or new quality rules for existing sources;
  - detecting late arrivals from the data;
  - scheduling and streaming;
  - performance targets beyond recording timings.

## Planning Decisions

Left to the planner, ratified by the human before implementation (gate G2):

- the weekly measure list and the statistic for each;
- the minimum-coverage threshold below which an aggregate is unavailable;
- the exact column names and types, and the value of `week_id`;
- the treatment of survey data, if the planner proposes it.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- week boundaries and the four week keys, and `period_start_date` and `period_end_date`;
- grain and key uniqueness, and 200 rows on the default window;
- partial, missing, duplicate and flagged days feeding `valid_days` and the valid counts,
  with no imputation;
- the weekly schema differing from the daily one in names and columns;
- weekly values reconciling with the daily totals and means;
- an idempotent MERGE rerun;
- a corrected record changing exactly one weekly row;
- incremental output equal to a full rebuild after a new day, a corrected record, a
  removed file, a shared-input change and a window change;
- a window that is not a multiple of seven days;
- the old-schema fallback;
- a failed run leaving the output unchanged;
- deterministic outputs;
- the compare command covering the new table without code changes.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-007/`.

Real-data evidence, run by the human on a copy of `data/` (never on `data/` itself) and
recorded in the implementation report:

- a full run gives 200 weekly rows;
- a rerun is identical (compare command, `run_id`, report);
- an incremental run with no change is a no-op;
- one corrected record changes exactly one weekly row, and the incremental output compared
  with a full rebuild is identical;
- the weekly values reconcile with the daily table on real data;
- timings are recorded;
- `data/` SHA-256 unchanged.

Human steps after implementation: a new review notebook (`notebooks/INC-007-review.ipynb`)
is created and committed with its outputs cleared, as for `notebooks/INC-003-review.ipynb`
to `notebooks/INC-006-review.ipynb`, and the profiling findings are updated with the G2
decisions. The orchestrator's implementation phase may not edit `notebooks/`.
