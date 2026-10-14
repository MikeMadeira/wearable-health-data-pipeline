# Planning Review Report: INC-008 (round 1)

Status: APPROVED
Verdict: PASS
Inputs:
- intents/INC-008-chronotype-cohort.md (unedited)
- specs/INC-008.md (Status: Draft, D1-D12 await G3)
- src/wearable_pipeline/cohort.py (stub)
- tests/test_cohort.py (81 tests), `git diff tests/test_weekly.py` (one line)
- reports/INC-008/planner.md; reference format: reports/INC-007/planning-reviewer.md
- src/wearable_pipeline/incremental.py, tests/test_incremental.py helpers (design validity)
- `data/` not read (synthetic fixtures only)

Outputs:
- reports/INC-008/planning-reviewer.md (this file)

Checks:
- `python -m pytest -q tests/test_cohort.py tests/test_weekly.py`: PASS as red baseline. **81 failed, 95 passed** (113 s). The 81 failures are 80 in `tests/test_cohort.py` plus the amended `tests/test_weekly.py::test_constants_and_registration`. The one passing cohort test is the constants-only `test_cohort_columns_are_the_specified_schema`. No import, syntax or fixture error. Failure kinds match spec section 11: stub `NotImplementedError` (pure functions and direct `refresh`), "table gold_chronotype_cohort does not exist" (pipeline and incremental tests, which fail in setup), and `AssertionError` for the two registration tests.
- Stub: PASS. `classify_cohort`, `mean_if_covered` and `ChronotypeCohort.refresh` bodies are only `raise NotImplementedError`. Constants, `COHORT_COLUMNS` and `COHORT` are declared. No `pytest.raises(NotImplementedError)` anywhere in `tests/`.
- Existing-test change: PASS. `git diff` shows exactly one changed assertion in `tests/test_weekly.py`, from `== [TABLE_NAME]` to `== [TABLE_NAME, "gold_chronotype_cohort"]`. The intent permits this (tests that assert the table list). `DERIVED_TABLES[0] is WEEKLY` is kept. Nothing else is weakened. No `src/` file other than the new stub is modified, and the wiring (`DERIVED_TABLES`, `_EXPECTED_TABLES`, `_expected_schema`) is deliberately left to the implementer.
- Hand-computed values: PASS. I recomputed P001 by hand. HR: 8 even days at 60 plus 7 odd days at 62 gives 914. Day 15 has mean 110, 300 bpm is withheld and day 28 is outside the window. So `hr_valid_days` = 16, equal-weight mean = 1024/16 = 64.0, and the minute-weighted 1244/18 is correctly rejected. Steps: the sum of 100+i over i=0..13 is 1491, plus one zero-step day, over 15 days = 99.4 (the exact duplicate minute counts once, conflicting and invalid days are missing). Sleep: even days give 400 min, eff 80, deep 25; odd days give 480 min, eff 96, deep 25. Over 7+7 days: 440 / 88 / 25, 14 valid days, and the conflicting D14 is withheld. P002 has 13 valid days, so all means are NULL. P003 has 14 steps days at 50.0 (the boundary) and the flag UNEXPECTED. I then ran the real pipeline on fixture C and compared `gold_clinical_daily` aggregates: counts and means equal `EXPECTED` for P001-P003 (P001 14/14/15/16, 440/88/25/99.4/64.0; P002 13/13/13/0, steps 100.0 suppressed by the threshold; P003 0/0/14/0, steps 50.0). P004-P006 have no data. The scratch run used only a temp directory.
- Other numbers: PASS. Corrected record 60->61 gives 1025/16. The partial window cases (15 days: 1491/15 and 914/15; 10 days: 10 valid days so the mean is NULL) are correct. Day offsets up to `D[39]` exist (`len(D)` is 40).
- DuckDB feasibility of the old-schema degradations: PASS. On a table with a PRIMARY KEY, `ALTER COLUMN TYPE` and `DROP COLUMN` on non-key columns both succeed (probed). The helper `swap_first_two_columns` recreates the table without a PK, which is the INC-007 precedent.

Spec vs intent (coverage matrix, all PASS):
- Grain and key (one row per enrolled participant, MERGE on `participant_id`, 50 rows, orphans and duplicate listings): spec sections 2 and 3; tests `one_row_per_enrolled...`, `pipeline_writes_the_table...` (PRIMARY KEY check), `fifty_participants...` (28/22).
- NULL cohort and flag, never invented: spec section 4, `classify_cohort` (18 parametrized cases covering missing, unexpected, conflict, no case folding and no trimming), and the pipeline-level `COHORTS` test.
- Valid-day counts never NULL, no imputation, zero-step is valid, missing is not zero: spec sections 3 and 6; tests `nothing_is_imputed...`, `partial_duplicate_withheld...`.
- Threshold boundary 13 NULL / 14 available: `mean_if_covered` parametrization plus P002 and P003.
- Reconciliation with daily, including after an incremental run, and silver-only derivation (gold tampering test): present.
- Idempotent MERGE, stale key removal, affected-only refresh, empty, out-of-window and unenrolled `affected` ignored, missing or wrong layout recomputed in full: present.
- Corrected record changes one row, new day changes one row, removed file changes one participant: present.
- Incremental equals full for a new day, corrected record, removed file, participants change (changed chronotype, added and removed participants), survey change (shared input) and window change (4 windows): present, using `assert_equals_full` (all tables, columns, report, `run_id` via `state`).
- Old-schema fallback: 5 degradations (missing table, retyped, reordered, missing column, measure retyped) with `fallback_reason == "old_schema"`.
- Failed run unchanged (malformed file; failing refresh rolls back, via tree hashes), determinism, compare command (identical and one-row difference, counts only), quality outputs, `run_id` and report unchanged: present.
- Constraints: no new dependency, silver and daily untouched, no new quality rule, no timing metric. The no-`NotImplementedError` and stub-only conditions are met.

Findings:

By severity (no blocker, no medium):

1. LOW (TESTS, weak scenario): `test_removed_record_equals_full_rebuild` removes the last two HR records of P001, the withheld 300 bpm record and the out-of-window record. Neither changes any daily or cohort value, so the test passes for an implementation that ignores the removal. It cannot distinguish a stale implementation from a correct one. It is harmless, and the removed-file test covers the real scenario with a value change. Optional: remove a record that affects a valid day (for example the last in-window HR record of day 15) at the next touch.
2. LOW (stub docstring): the `classify_cohort` docstring cites `specs/INC-008.md 3`, but the classification rules are in section 4. No behaviour impact. Optional fix at implementation.
3. LOW (red quality, expected): most incremental and pipeline-level tests fail in `prepared`/`built` (table missing or stub), so their own assertions are not yet exercised. This is the same setup-failure precedent accepted for INC-006 and INC-007. Gate 2 must confirm they pass for the right reason.
4. INFO (new rule beyond the intent, flagged by the planner): `CHRONOTYPE_CONFLICT` (D2) is a third flag value. The intent names only missing and unexpected. It is a reasonable reading of "a cohort is never invented" and is presented as a decision, so the human may ratify or change it.
5. INFO: the planner's "not covered by automated tests" list (real-data 50 rows with 28/22, timings, `data/` SHA-256) matches the intent's human real-data evidence. A synthetic 50-row 28/22 test exists.
6. INFO (carried): the wiring touches `incremental._EXPECTED_TABLES` and `_expected_schema`. Tests that replace `DERIVED_TABLES` with fakes must keep working (the spy and `Boom` tests do). The implementer must report, not weaken, any existing `tests/test_incremental.py` test that breaks.
7. INFO (carried): no reference implementation has run against these tests, so gate 2 must run the real implementation, the full suite and the real-data evidence. The unrelated notebook and any untracked files must stay out of the baseline commit.

Decisions D1-D12 for ratification (clear and complete in spec section 12 and planner.md; the spec table is the authority):
- D1 `chronotype_cohort` + `cohort_flag` columns, exact match.
- D2 duplicate listings: missing ignored, disagreement gives a NULL cohort with CONFLICT (the one flag beyond the intent).
- D3 13 columns, 4 valid-day counts and 5 means.
- D4 `MIN_VALID_DAYS = 14`, absolute and per measure (alternatives named: 50% of the window, 26 days).
- D5 heart rate is the equal-weight mean of daily means, with a worked example (64.0 vs 69.11 minute-weighted). It differs from the weekly table.
- D6 whole window including a trailing partial block.
- D7 silver-only derivation via the INC-007 per-day logic.
- D8 affected participants rule.
- D9 layout rebuild is a full recompute.
- D10 `window_days` column.
- D11 quality outputs unchanged.
- D12 the single `tests/test_weekly.py` amendment and the implementer wiring.
- Items for the human to look at especially: D4, D5, D2 and the D12 amendment.

Next action:
Orchestrator: planning gate 1 is PASS. Route to human review and ratification of D1-D12 at G3, then the baseline commit, then the implementer. The human may optionally ask the planner to address findings 1 and 2, but neither blocks. Do not commit on behalf of the human.
