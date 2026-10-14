# Planner Report: INC-008

Status: READY
Inputs:
- intents/INC-008-chronotype-cohort.md
- specs/INC-007.md, src/wearable_pipeline/weekly.py, tests/test_weekly.py
- src/wearable_pipeline/incremental.py (`DerivedTable`, `DERIVED_TABLES`, `_EXPECTED_TABLES`)
- reports/INC-001/profiling-findings.md (Section 7 item 8), reports/INC-007/planner.md
- `data/` was not read; only synthetic fixtures.

Outputs:
- specs/INC-008.md (Draft; decisions D1-D12 await G3)
- src/wearable_pipeline/cohort.py (stub: `classify_cohort`, `mean_if_covered`, `ChronotypeCohort.refresh` each only `raise NotImplementedError`; constants and `COHORT_COLUMNS` declared; `COHORT` instance)
- tests/test_cohort.py (81 tests; no `pytest.raises(NotImplementedError)`)
- tests/test_weekly.py (one-line amendment, see below)
- reports/INC-008/planner.md

Checks:
- `python -m pytest -q` (full suite, repo root): 81 failed, 839 passed (baseline, expected red). 80 failures are in `tests/test_cohort.py` (81 tests, 1 passes: the constants-only `test_cohort_columns_are_the_specified_schema`); 1 is the amended `tests/test_weekly.py::test_constants_and_registration`. All other existing tests pass.
- Failure causes in `tests/test_cohort.py`: stub `NotImplementedError` for the pure-function tests and every test that calls `COHORT.refresh` directly (`built`); "Table gold_chronotype_cohort does not exist" for pipeline/incremental-level tests; assertion failure for the registration test. Fixture C was validated against `gold_clinical_daily` (counts and means of the silver-derived days equal the hand-computed `EXPECTED` values).
- Wiring is deliberately not done (so no existing test is red because of the stub): `DERIVED_TABLES`, `_EXPECTED_TABLES`, `_expected_schema` are untouched.

Findings:
- Decisions at G3, RATIFIED 2026-10-14 by the human, D1-D12 unchanged (full text in specs/INC-008.md section 12):
  1. D1 Cohort attribute: `chronotype_cohort` VARCHAR ('A'/'B'/NULL) plus `cohort_flag` VARCHAR (NULL, `CHRONOTYPE_MISSING`, `CHRONOTYPE_UNEXPECTED`, `CHRONOTYPE_CONFLICT`). Exact match; no case folding or trimming of a present value; NULL/empty/whitespace is missing.
  2. D2 Participant listed twice: missing values ignored; one distinct value is used; disagreeing values give a NULL cohort with `CHRONOTYPE_CONFLICT`.
  3. D3 Measures/columns (13): `participant_id`, `chronotype_cohort`, `cohort_flag`, `window_days`, `sleep_valid_days`, `sleep_stage_valid_days`, `steps_valid_days`, `hr_valid_days`, `sleep_total_min_mean`, `sleep_efficiency_pct_mean`, `deep_sleep_pct_mean`, `steps_daily_mean`, `hr_daily_mean_bpm`. No survey, minutes, min/max or overall `valid_days`.
  4. D4 Threshold: `MIN_VALID_DAYS = 14` absolute, per measure (13 NULL, 14 available); alternatives: 50% of window, or 26 days.
  5. D5 Heart rate: mean of daily mean bpm over valid days (equal day weight); differs from the minute-weighted weekly mean.
  6. D6 Whole window including a trailing partial block (weekly drops it).
  7. D7 Source: silver only, via the INC-007 per-day derivation (never gold); consistent with `gold_clinical_daily` by construction and by reconciliation test.
  8. D8 Incremental: affected = enrolled ids with at least one affected date inside the window; participants/survey/device/window changes take the full-rebuild path.
  9. D9 A missing or different layout forces a full recompute inside `refresh`.
  10. D10 `window_days` column included.
  11. D11 No new quality rule, `quality_results`/`quality_rejects`/report/`run_id` change.
  12. D12 Existing-test amendment and wiring (below).
- Existing-test amendments: ONE. `tests/test_weekly.py::test_constants_and_registration` now expects `[t.name for t in DERIVED_TABLES] == ["gold_biostat_weekly", "gold_chronotype_cohort"]` and `DERIVED_TABLES[0] is WEEKLY` (was `== [TABLE_NAME]`); permitted by the intent (tests asserting the table list). It is red until wiring. No other test touched or weakened. Possible legitimate follow-ups the implementer must record rather than weaken: tests in `tests/test_incremental.py` that replace `DERIVED_TABLES` with a fake and then compare to a full rebuild (INC-007 already handled the known one by restoring the original list).
- Implementer wiring (spec section 8 / D12): `incremental.DERIVED_TABLES = [WEEKLY, COHORT]`; add `gold_chronotype_cohort` with `COHORT_COLUMNS` to `_EXPECTED_TABLES` and `_expected_schema()`; `incremental` imports `cohort`, `cohort` must not import `incremental`, `weekly` must not import `cohort`. Reuse the weekly per-day derivation (helpers may be shared or moved without changing weekly output).
- Not covered by automated tests (human real-data evidence per intent): 50 real rows with A 28 / B 22 (a synthetic 50-participant 28/22 test exists), timings, `data/` SHA-256.

Next action:
Route to the planner reviewer; the human should ratify D1-D12 at G3 (notably D4 threshold and D5 heart-rate weighting) and check the single `tests/test_weekly.py` amendment.
