# Implementation Review Report: INC-008

Status: APPROVED
Verdict: PASS (no blocker, major or medium findings; three Low observations)
Inputs:
- intents/INC-008-chronotype-cohort.md, specs/INC-008.md (Approved, D1-D12 ratified)
- reports/INC-008/planner.md, planning-reviewer.md, implementer.md (Status READY)
- src/wearable_pipeline/cohort.py, incremental.py (git diff vs HEAD); weekly.py for the reused helpers
- tests/test_cohort.py (read; unchanged vs HEAD)
- reports/INC-007/implementation-reviewer.md (format reference)

Outputs:
- reports/INC-008/implementation-reviewer.md

Checks:
- `python -m pytest -q` (repo root): PASS (920 passed, 0 failed, 0 skipped, 307.8 s; matches the implementer's count).
- Test/spec protection: PASS. `git status` shows only `M src/wearable_pipeline/cohort.py`, `M src/wearable_pipeline/incremental.py` and `?? reports/INC-008/implementer.md` (plus this report). `git diff HEAD -- tests specs intents data` is empty. The D12 amendment of `test_weekly.py::test_constants_and_registration` is already in the baseline commit. Nothing weakened; no `NotImplementedError` left in `src/`.
- Spec adherence, section by section: PASS.
  - 1 Contract: all names, `COHORT_COLUMNS`, `classify_cohort`, `mean_if_covered(values, minimum=14)` (float or None), `COHORT`, `DERIVED_TABLES == [WEEKLY, COHORT]`. Import direction is correct (`weekly` does not import `cohort`; `cohort` does not import `incremental`). No new dependency; `pyproject.toml`, `uv.lock`, `Dockerfile` untouched.
  - 2 Grain/population: enrolled ids are `DISTINCT participant_id` from `silver_participants`; orphans get no row; a participant with no data keeps a row with zero counts and NULL means; window read from `run_window`; whole window including a trailing partial block.
  - 3 Schema: 13 columns, types and order from `COHORT_COLUMNS`; `PRIMARY KEY (participant_id)`; counts are never NULL; `window_days = study_days`.
  - 4 Cohort rule: missing (NULL or blank) ignored, exact comparison, no trim or case folding, conflict, unexpected and missing flags exactly as specified.
  - 5/D7 Silver only: reads silver tables and `run_window`, never gold or survey; per-day values come from weekly's `_daily_sleep`, `_daily_steps`, `_daily_heart_rate`, so daily rules cannot drift. Verified by test and by the fuzz reconciliation below.
  - 6/D4/D5 Statistics: gate per measure on its own count (`>= 14`), zero-step days valid, heart rate is the mean of the daily means (`sum / minutes` per day, equal weight), all sums in ascending date order via one code path (`_compute`).
  - 7/D8/D9 Lifecycle: `_layout_matches` (existence plus ordered (name, type)); a missing or different layout is dropped, re-created and fully recomputed, ignoring `affected`; no exception for a wrong layout; full path MERGE + prune; incremental path recomputes only enrolled ids with a date inside the window, without prune; empty mapping changes nothing; no `BEGIN`/`COMMIT`; exceptions propagate.
  - 8/9 Integration: `_EXPECTED_TABLES` and `_expected_schema` include the cohort table (old-schema fallback). No quality rule, result row, report text or `run_id` change.
- Incremental equals full rebuild (fuzz, scratch under /tmp, deleted): PASS. 40 seeds x 3 successive incremental runs (120 runs: 90 incremental, 27 full fallbacks, 3 no-op) on the six-participant fixture C (cohorts A/B/unexpected/missing/conflict, an orphan). Mutations: random rewrite of hr, steps and sleep files with out-of-range and non-numeric values, conflicting duplicate slots and sessions, file deletion, and window changes among 10, 13, 14, 21 and 28 days. After every run every table, column type, report bytes were identical to a fresh full rebuild, DOUBLE columns compared bit-for-bit (`struct.pack`), and the suite's own `reconcile` against `gold_clinical_daily` held. 0 mismatches.
- Determinism: PASS. Bit-identical doubles incremental vs full (fuzz above); `test_output_is_deterministic_across_independent_full_runs` passes.
- Reconciliation with `gold_clinical_daily`: PASS (fuzz, 120 runs, plus the suite's reconcile tests).
- `data/` access: PASS (not read; the pipeline was never run on it).
- Code quality: PASS with Low notes. One compute path serves both refresh modes; ids reach SQL only as bound parameters; temp tables (`weekly_keys`, `cohort_stage`) are created inside the caller's transaction, and `weekly_keys` is dropped in `finally`; no dead code or unused imports or parameters found in `cohort.py`.

Findings:
- Blocker: none.
- Major: none.
- Medium: none.
- Low 1: `cohort.py` imports underscore-prefixed helpers (`_daily_sleep`, `_daily_steps`, `_daily_heart_rate`) from `weekly.py`. Permitted by spec section 1/5 and disclosed by the implementer. A shared private module would be a later cleanup.
- Low 2: the temp table is named `weekly_keys` and must keep weekly's four-column shape (with a dummy `week_id` of 1). This is a hidden coupling to `weekly._KEY_JOIN`; a change there would break the cohort silently apart from the tests. Acceptable, and a comment in `_compute` explains it.
- Low 3: the new import in `incremental.py` (`wearable_pipeline.cohort`) sits before `wearable_pipeline.weekly` rather than in alphabetical module order after `sleep`/`steps`. Cosmetic only. `_compute` is about 40 lines; readable.
- Process note: real-data evidence (full run, no-op, one corrected record, 50 rows with A 28 / B 22, `data/` SHA-256 before and after) and `notebooks/INC-008-review.ipynb` are human steps and were not produced.
- Cleanup: scratch fuzz script and fixtures under `/tmp` deleted; no stray files in the repo (only ignored caches such as `.pytest_cache`); no commits.

Next action:
Orchestrator: record INC-008 as passing Gate 2 (`APPROVED`). Human: run the real-data evidence on a copy of `data/`, create the review notebook if required by the intent, then commit. Nothing was committed by this review.
