# Implementation Report: INC-008

Status: READY

Inputs:
- specs/INC-008.md (Approved, D1-D12)
- tests/test_cohort.py, tests/test_weekly.py (amended `test_constants_and_registration`)
- src/wearable_pipeline/weekly.py, incremental.py

Outputs:
- src/wearable_pipeline/cohort.py
- src/wearable_pipeline/incremental.py
- reports/INC-008/implementer.md

Checks:
- `python -m pytest -q` (repo root): PASS, 920 passed, 0 failed (308.8 s).
- `data/` not read; tests, specs, notebooks, README not edited; nothing committed.

Findings:
- No spec deviation and no test defect.
- `cohort.py`: `classify_cohort` and `mean_if_covered` implemented as pure functions per spec section 4/6. `ChronotypeCohort.refresh` mirrors `WeeklyBiostat`: layout check by ordered (name, type), drop/create with `PRIMARY KEY (participant_id)` and full recompute when the layout differs, MERGE on `participant_id`, prune only on the full path. Affected participants = enrolled ids with at least one date inside the window.
- Per-day values reuse weekly's private `_daily_sleep`, `_daily_steps` and `_daily_heart_rate` (no change to weekly). They are restricted through the temp `weekly_keys` table, filled with one row per participant spanning the whole window. Weekly's `_daily_survey` is not used. Hence the daily rules cannot drift. `weekly` does not import `cohort`; `cohort` does not import `incremental`.
- Heart rate: mean of the daily means (sum / valid minutes per day), each valid day weighted equally; all sums in ascending date order.
- `incremental.py`: imports `COHORT`, `COHORT_COLUMNS`; `DERIVED_TABLES = [WEEKLY, COHORT]`; `_EXPECTED_TABLES` and `_expected_schema` include `gold_chronotype_cohort` (old-schema fallback).
- Minor: cohort imports underscore-prefixed helpers from `weekly` (allowed by spec section 5; a shared module would be a later cleanup).

Next action:
Route to the implementation reviewer (PASS, see implementation-reviewer.md).

## Real-data evidence (2026-10-14, run by the orchestrator on copies of `data/` under /tmp)

- Full run: 78.0 s, `run_id=a826c8397cf5ad8e`. `gold_chronotype_cohort`: 50 rows, 50 distinct participants, 13 columns. Cohorts A 28 and B 22; `cohort_flag` is NULL for all 50. `window_days` is 28 for all rows. Cohort attribute equals `silver_participants.chronotype` on all 50 rows.
- Coverage: sleep valid days 28 for all; steps and heart-rate valid days are 27 for 4 participants and 28 for 46. No participant is below the 14-day threshold, so no measure is NULL and the threshold is exercised only by tests.
- Reconciliation with `gold_clinical_daily` (valid-day counts and the five means, independent SQL): 0 mismatches over 50 participants.
- Cohort means (A / B): total sleep 414.6 / 407.67 min, daily steps 10117.4 / 10635.2, heart rate 71.33 / 68.12 bpm.
- Rerun on the same input: 78.2 s, identical (compare command, same `run_id` and report).
- No-op incremental: 0.4 s, identical to the full run.
- One corrected record (P001 steps, 2026-01-20T12:00, +37): incremental 2.7 s against 78.1 s for a full rebuild; `run_id=41c339cef2ec4025` for both; compare identical. Exactly one cohort row changed (P001), one weekly row and one daily row.
- `data/` tree hash unchanged (`abd71e95...`).
