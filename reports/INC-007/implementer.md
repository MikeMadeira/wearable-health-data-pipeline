# Implementation Report: INC-007

Status: READY
Inputs:
- intents/INC-007-weekly-biostatistics.md
- specs/INC-007.md (D1-D11, INC-006 amendment)
- reports/INC-007/planner.md (incl. Revision 2), reports/INC-007/planning-reviewer.md
- tests/test_weekly.py (94 tests), tests/test_incremental.py
- src/wearable_pipeline/weekly.py (stub), incremental.py, pipeline.py, sleep.py, steps.py, heart_rate.py, compare.py

Outputs:
- src/wearable_pipeline/weekly.py
- src/wearable_pipeline/incremental.py
- reports/INC-007/implementer.md

Checks:
- `python -m pytest -q` (repo root): PASS, 839 passed, 0 failed (about 239 s) after the planner corrected the three test defects (planner.md Revision 3; one extra parametrised case, `weekly_key_type_changed`). The first implementation run ended at 3 failed, 835 passed (baseline 93 failed, 745 passed); all three failures were test defects, not code defects, and the implementation was not changed afterwards.
- First run, `python -m pytest -q tests/test_weekly.py`: 2 failed, 92 passed (about 73 s); all weekly tests pass after Revision 3.
- `data/` was never read or run on. Tests and specs were not edited. No new dependency, no commit. All file changes were made with the edit/create tools; no scratch files written (one read-only probe via `python -` stdin on synthetic fixtures, nothing written).

Findings (all three RESOLVED by the planner in Revision 3: the weekly_type_changed case now alters a non-key column and a new weekly_key_type_changed case recreates the table; the survey-only test deletes its own steps file; the INC-006 test restores the original DERIVED_TABLES. The text below is the original blocker evidence):
1. `tests/test_weekly.py::test_old_weekly_schema_falls_back_to_a_full_rebuild[weekly_type_changed]` (TESTS). The degrade step runs `ALTER TABLE gold_biostat_weekly ALTER COLUMN week_id TYPE BIGINT`. `week_id` is part of the PRIMARY KEY that spec section 7 requires and that `test_refresh_creates_a_missing_table_with_the_key` asserts (`duckdb.ConstraintException` on a duplicate key). DuckDB refuses to change the type of a key column (`BinderException: Cannot change the type of a column that has a UNIQUE or PRIMARY KEY constraint`), so the failure happens inside the test setup, before any pipeline code runs. A unique index has the same restriction. No implementation can satisfy both tests. Fix (planner): degrade a non-key column (the sibling case `weekly_measure_type_changed` already does) or recreate the table with a BIGINT `week_id` without a key in that case.
2. `tests/test_weekly.py::test_survey_only_week_reports_survey_coverage_but_zero_valid_days` (TESTS). `make_data` always writes a P001 steps file with one record (`steps: 0` at 2026-01-08T00:00). The daily table therefore has `total_steps = 0`, `steps_valid_minute_count = 1` for (P001, 2026-01-08) (verified on the fixture), which by spec sections 3/4 (steps valid = non-NULL daily total) is a valid day, so `valid_days = 1`, not the 0 the test asserts. Treating a recorded zero as invalid would contradict the spec and the reconciliation tests. Fix (planner): build the survey-only fixture without the default steps file, or assert `valid_days == 1`.
3. `tests/test_incremental.py::test_failure_inside_the_transaction_rolls_back_everything` (TESTS; existing test legitimately affected by the INC-006 amendment, as the planner anticipated). It sets `DERIVED_TABLES` to `[]` for its last step and then compares the incremental output with a fresh full rebuild; the output built earlier (with `[WEEKLY]`) contains `gold_biostat_weekly`, the fresh rebuild with `[]` does not, so the `columns`/`tables` comparison differs. The implementation cannot make both equal without dropping an unregistered table. Fix (planner): restore the original `DERIVED_TABLES` (`monkeypatch.setattr(incremental, "DERIVED_TABLES", original)` or `monkeypatch.undo()`) instead of `[]` before the final `inc` and `assert_equals_full`. I did not edit it.

Next action:
Done: the planner corrected the three tests and the suite passes (839). Next: the implementation reviewer, which should also confirm the INC-006 test amendment does not weaken that test.

## Summary of the implementation

- `weekly.py`: pure `week_count`, `week_bounds`, `week_of`; `WeeklyBiostat.refresh` (registered as `WEEKLY`). Reads only `run_window` and the silver tables, never gold.
  - Layout check by ordered (name, type) against `WEEKLY_COLUMNS`; missing or different layout is dropped, re-created with `PRIMARY KEY (participant_id, week_id)` and fully recomputed, ignoring `affected` (D11).
  - One code path `_compute(con, start, keys)` for full and incremental: a temp `weekly_keys` table (participant, week, bounds) restricts the silver reads. Full path keys = every enrolled participant x whole week; incremental keys = `{(pid, week_of(date))}` of enrolled participants (dates outside whole weeks ignored, empty mapping does nothing).
  - Per-day values re-derived from silver with the builder rules: steps and heart rate take one value per minute slot (`min` over the slot, exact duplicates equal, conflicts already NULL in silver), rows excluded when orphan, undated, out of window or flagged with the folder-mismatch rule; counts over non-NULL slots; rest-to-moderate threshold 30 as in `steps.py`. Heart rate uses integer sums/counts/min/max per day. Survey: distinct non-NULL score per day, else NULL. Sleep: stages split into sessions at `stage_index = 1` in `rowid` order (D10), `compute_sleep_metrics` per session, sessions without stage rows add the all-NULL metric set, one distinct set gives the day value, otherwise withheld.
  - Weekly aggregation in Python over days in ascending order (integer sums exact; DOUBLE sums of the sleep percentages in date order; one division per mean), gated by `MIN_VALID_DAYS` per measure; coverage columns never gated. The weekly HR mean is total valid-minute value / valid-minute count.
  - MERGE through a temp stage table on (participant_id, week_id); the full path also deletes keys not in the computed set.
- `incremental.py`: imports `WEEKLY`, `DERIVED_TABLES = [WEEKLY]`, `_EXPECTED_TABLES` gains `gold_biostat_weekly`, `_expected_schema` creates it from `WEEKLY_COLUMNS` (old-schema check). `pipeline.py`, `compare.py`, `cli.py`, quality outputs, `run_id` unchanged (the existing `_persist` already calls `DERIVED_TABLES` refresh).

## Decisions and deviations

- No spec deviation. Unregistered state: when `DERIVED_TABLES` is monkeypatched to `[]`, no weekly table is written (that is what makes finding 3 unfixable in code).
- `_in_scope` carries two unused parameters (`con`, `table`) kept for call-site readability; harmless, can be trimmed.

## Timing

- Weekly refresh is part of every pipeline test run; slowest weekly tests: default 28-day window with 200 rows 4.59 s (includes the full pipeline run), window change to 28 days 1.64 s. 60,000-record heart-rate and steps scale tests (3.62 s and 1.84 s, whole suite under load) are within their limits. Whole suite about 216 s. No dedicated weekly scale test exists. Real-data timing is a human step.

## Open issues

- None blocking. Real-data evidence and `notebooks/INC-007-review.ipynb` are human steps after review.

## Real-data evidence (run on copies of `data/`)

Run on 2026-10-14 from a throwaway driver (`/tmp/inc007_evidence.py`, outside the repo) on copies of `data/` under `/tmp/inc007`. Raw values were not printed. `data/` was never used as a run input.

| Check | Result |
|---|---|
| Full run | 200 `gold_biostat_weekly` rows, 200 distinct (`participant_id`, `week_id`) keys, 50 participants, 29 columns. Weeks 1-4 are Jan 8-14, 15-21, 22-28 and Jan 29-Feb 4. Daily table: 1,400 rows. `run_id=a826c8397cf5ad8e` (same as INC-005 and INC-006). 95.4 s, 4.9 GB peak. |
| Full rerun on the same output | `compare` vs the pre-rerun snapshot: identical, same `run_id`, `quality/` tree hash unchanged (104.3 s). |
| Incremental, nothing changed | `mode=noop`, 0.6 s, `compare` vs snapshot: identical, `quality/` hash unchanged. |
| One corrected record (P001 steps, 2026-01-20T12:00) | Incremental 3.3 s (0.2 GB peak) vs full rebuild of the same copy 108.8 s: `compare` identical, equal `run_id` `41c339cef2ec4025`. Exactly one weekly row differs from the baseline: (P001, week 2). |
| New file (P050 `sleep.json` added to a baseline that lacked it) | Incremental 3.7 s: `compare` vs the full run of the complete copy identical, `run_id` back to `a826c8397cf5ad8e`. Only P050's four weekly rows differ from that baseline. |
| Reconciliation with `gold_clinical_daily`, all 200 rows | 0 mismatches for: `steps_total` = sum of daily totals; availability iff at least 4 valid daily totals; `steps_daily_mean`; the rest-to-moderate and burst minute sums; `hr_valid_minutes`; `hr_weekly_mean_bpm * hr_valid_minutes` = sum of daily mean x count (tolerance 1e-6); HR min and max; sleep means; survey means; and every daily row falls in exactly one week (1,400 of 1,400). |
| `data/` tree hash (path plus SHA-256 of every file) | `abd71e95…012b` before and after: unchanged. |

Coverage on the real data: `valid_days` is 7 for all 200 rows (a day is valid when any of sleep, steps or heart rate has a valid value). `sleep_valid_days` is 7 for all rows. `steps_valid_days` and `hr_valid_days` are 6 for 4 participant-weeks and 7 for 196, so no weekly measure is NULL under the 4-day threshold on this dataset (0 NULL sleep, steps or heart-rate measures). The threshold is therefore exercised only by the tests, not by the real data.

Observations: the weekly refresh adds no visible time to a run (full runs 95-109 s against 74-85 s in INC-006, which is within the variation of a loaded machine; not measured separately). Incremental runs take 3-4 s.
