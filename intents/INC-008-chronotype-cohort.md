# INC-008: Chronotype Cohort Table

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-007
(`intents/INC-007-weekly-biostatistics.md`); uses the G3 direction in
`reports/INC-001/profiling-findings.md` (Section 7, item 8): all enrolled participants
are retained in chronotype cohorts A and B, with metric-specific validity counts rather
than dropping participants. The exact statistics and the coverage threshold are decided at
the planning gate (see Planning Decisions).

## Goal

The pipeline produces `gold_chronotype_cohort`, a participant-level table that links each
enrolled participant's chronotype to sleep and activity measures over the study window, so
participant cohorts can be compared by chronotype. It is derived from the silver layers,
never from raw files, is written by every run, and stays correct under reruns, corrections
and incremental runs.

## Boundary

One code slice: the cohort table and its incremental support through the INC-006
`DerivedTable` extension point. The A versus B comparison query, the container and the
documentation are separate increments (INC-010 and INC-009).

## Requirements

### Table, grain and population

- Table `gold_chronotype_cohort`, written on every run and MERGEd on `participant_id`.
- One row per enrolled participant: 50 rows on the real data. No participant is dropped
  for incomplete data.
- Each row carries the participant's chronotype as a cohort attribute (A or B on the real
  data). How the attribute is stored is decided by the planner.
- A participant with a missing or unexpected chronotype keeps a row with a NULL cohort and
  a flag. A cohort is never invented and no quality rule is added.
- The summary covers the whole study window (default 28 days).

### Source and measures

- The table is derived from silver, with the same rules, flags and withheld-value rules
  as the daily table. Where a measure has a daily counterpart, it is consistent with
  `gold_clinical_daily` for the same days. The planner documents any intended difference.
- Measures: mean total sleep, mean sleep efficiency, mean deep-sleep percentage, mean daily
  steps and mean heart rate, each with its valid-day count.
- Each participant mean is the mean of the daily values over the valid days, so each valid
  day weighs equally. The planner documents the heart-rate definition.
- No new chronotype timing metric (sleep midpoint, bedtime or wake time) is added.

### Coverage

- Missing days and minutes are never imputed. Valid-day counts are always exposed and are
  never NULL.
- Below a minimum number of valid days, a metric is NULL. The planner proposes the
  threshold for the human to ratify at G3.

### Quality outputs

- The table adds no quality rules. `quality_results`, `quality_rejects`, the report and
  `run_id` are unchanged unless the planner shows a need and flags it for the human's
  decision.

### Reruns and incremental mode

- A rerun on the same inputs is idempotent: identical rows, no duplicates.
- The table plugs into the INC-006 `DerivedTable` extension point. Incremental mode
  recomputes only the participants affected by a change. A change to the participants
  file recomputes all rows. Its output, including `run_id` and the report, equals a full
  rebuild's on the same inputs.
- A corrected single record changes exactly one participant row. A no-op incremental run
  changes nothing.
- A change to a shared input or to the study window follows the INC-006 shared-input path
  and gives the same result as a full rebuild.
- A failed run leaves the previous output unchanged, as in INC-006.

## Constraints

- Do not change: `gold_clinical_daily`, `gold_biostat_weekly`, the silver tables, `run_id`
  derivation, the existing `run` and `compare` CLI surface, existing public signatures, the
  harness (`scripts/`, `.github/`), `data/`.
- No new runtime dependency: standard library plus DuckDB.
- Existing tests that assert the table list or a behavior this increment extends may be
  updated by this increment. No other test is weakened.
- Out of scope:
  - the A versus B comparison query or any cohort-level summary table (INC-010);
  - `gold_participant_devices` (not implemented: out of scope for INIT-001);
  - the container and the documentation (INC-009, INC-010);
  - statistical testing, modeling and machine learning;
  - new timing metrics, new silver tables and new quality rules;
  - detecting late arrivals from the data;
  - scheduling and streaming;
  - performance targets beyond recording timings.

## Planning Decisions

Left to the planner, ratified by the human before implementation (gate G3):

- the statistic detail for each metric and the minimum valid-day threshold;
- the exact column names and types;
- the heart-rate weighting;
- how the cohort attribute is stored.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- grain and key uniqueness: one row per enrolled participant, and 50 rows on the real data;
- the cohort attribute taken from `silver_participants`, and the NULL-cohort case;
- metric correctness and valid-day counts, with partial, missing, duplicate and flagged
  days and no imputation;
- the threshold boundary;
- values reconciling with the daily table;
- an idempotent MERGE rerun;
- a corrected record changing exactly one participant row;
- incremental output equal to a full rebuild after a new day, a corrected record, a
  removed file, a participants-file change, a shared-input change and a window change;
- the old-schema fallback;
- a failed run leaving the output unchanged;
- deterministic outputs;
- the compare command covering the new table without code changes.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-008/`.

Real-data evidence, run by the human on a copy of `data/` (never on `data/` itself) and
recorded in the implementation report:

- a full run gives 50 participant rows, with cohorts A and B of 28 and 22 participants;
- a rerun is identical (compare command, `run_id`, report);
- an incremental run with no change is a no-op;
- one corrected record changes exactly one participant row, and the incremental output
  compared with a full rebuild is identical;
- the values reconcile with the daily table on real data;
- timings are recorded;
- `data/` SHA-256 unchanged.

Human steps after implementation: a review notebook (`notebooks/INC-008-review.ipynb`) is
created and committed with its outputs cleared, as for `notebooks/INC-007-review.ipynb`, and
the profiling findings are updated with the G3 decisions. The orchestrator's implementation
phase may not edit `notebooks/`.
