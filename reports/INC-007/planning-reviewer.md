# Planning Review Report: INC-007 (round 2)

Status: APPROVED
Verdict: PASS
Inputs:
- intents/INC-007-weekly-biostatistics.md (unedited)
- specs/INC-007.md (Status: Draft), specs/INC-006.md (amendment targets)
- reports/INC-007/planner.md (incl. Revision 2, D1-D11), round-1 review (history below)
- tests/test_weekly.py (94 cases), src/wearable_pipeline/weekly.py (stub)
- src/wearable_pipeline/pipeline.py, incremental.py, sleep.py, steps.py, heart_rate.py (design validity)
- `data/` not read

Outputs:
- reports/INC-007/planning-reviewer.md (this file, overwrites round 1 except the history section)

Checks:
- `python -m pytest -q` (repo root): PASS as baseline. **93 failed, 745 passed**, as reported; 0 failures outside `tests/test_weekly.py`. Of the 93: 54 `NotImplementedError` from the stub (47 round-1 plus the 7 new tests), 37 `CatalogException: gold_biostat_weekly does not exist` (pipeline-level tests, same setup-failure precedent as INC-006), 1 `KeyError` (determinism) and 1 `assert [] == ['gold_biostat_weekly']` (`DERIVED_TABLES`). No import, syntax or fixture error. The one extra pass is the schema-shape test.
- Stub: PASS. Constants and `WEEKLY_COLUMNS` only; every function and `refresh` body is just `raise NotImplementedError`. `pytest.raises(NotImplementedError)`: none in `tests/`.
- No existing file changed: PASS. `git diff` is empty; `git status` shows only the four untracked INC-007 paths (`reports/INC-007/`, `specs/INC-007.md`, `weekly.py`, `tests/test_weekly.py`).
- Round-1 finding 1 (identical duplicate sleep sessions): RESOLVED. I ran the real pipeline on the fixture of `test_identical_duplicate_sleep_sessions_give_one_value_set_not_a_sum`: daily 400 / 25.0 / 80.0 with `sleep_session_count = 2` on all five days, including the day whose second session has `sleep_onset="bad"` (its stages are still in silver: 8 stage rows, 1000 minutes per date). A pooled-stage implementation would give 800 on every day, so the `== approx(400.0)` assertion and `reconcile` both fail it. `test_identical_duplicate_sessions_do_not_hide_a_conflicting_session`: real daily output withholds the day with the conflicting third session (400 on four days, NULL on the fifth; stage indexes restart 1..4 three times in row order, matching D10), so 4 valid days is correct and the test fails an implementation that deduplicates blindly or pools.
- Round-1 finding 2 (folder mismatch): RESOLVED. Real pipeline: both HR and both steps mismatch rows are flagged in silver and attributed to P002 (silver participant is the file's content id); daily shows P001 4 valid days, mean 70.0, steps 10 per day, and P002 nothing. A leaking implementation gives P002 non-zero `hr_valid_days`/`steps_valid_days`, which the test asserts to be 0, and fails `reconcile`. The leak detection works for this rule.
- Round-1 finding 2 (out-of-window and orphan rows): PARTLY EFFECTIVE, see finding 1 below. Orphan leakage (rows for P999) is caught by `set(rows) == {P001,P002} x {1,2}`. Out-of-window leakage is only caught by a weekly-assignment off-by-one (see finding 1). Expected values are correct (real daily: 3 valid days in each week, 2 rows flagged out-of-window, silver sleep dates Jan 7 and Jan 22 are the neighbours).
- Round-1 finding 3 (D10 attribution): RESOLVED in spec section 4 and D10; consistent with the builder (probed).
- Round-1 finding 4 (D11): RESOLVED. Spec section 7 states a rebuild forces a full recomputation, `affected` ignored, no raise, and unreachable via `run_incremental` (old-schema fallback is first), so it does not contradict INC-006 sections 5 and 9. Two tests (missing table, dropped column) assert the full row set is restored after `refresh(con, affected)`; they fail an implementation that recomputes only the affected week. Both reach the stub via `refresh(out)` in `built`, hence the genuine `NotImplementedError`.
- Round-1 finding 5 (second study start): RESOLVED (`test_second_study_start_gives_matching_week_bounds`, values correct: 2026-03-02..08, 03-16..22, `week_of` boundaries).
- Spec vs intent: PASS. All goals, requirements and constraints carried; no business rule invented beyond the ratification list (D1-D11; D10 and D11 are new and flagged for the human).
- New-defect search (unsatisfiable or contradictory tests, tests that cannot fail): none blocking. No contradiction between spec sections 4, 7, 8, 11 and 12 and the tests; spec section 11 and the planner's red-baseline description match the observed failure kinds.

Findings:

By severity (no blocker):

1. LOW (TESTS, over-claimed guard): `test_rows_outside_the_window_and_orphans_never_reach_a_week`. The planner says the neighbouring out-of-window rows would make a leaking implementation reach 4 valid days. Because `week_of` is `None` outside whole weeks, out-of-window rows can never lie inside an emitted week; the day after the window (D[14]) belongs to non-emitted week 3 and the day before (D[-1]) only lands in week 1 with truncating division. So the test catches orphan rows and a truncation/off-by-one defect, not a missing "not out of window" filter, which is unobservable in the output. The test is satisfiable and harmless; the claim in `planner.md` and the "out-of-window" clause of spec section 4 are simply not independently testable. No action required; optionally reword at the next touch.
2. LOW (planner.md, stale text): the first section still reads "87 tests: 86 red", "86 failed, 745 passed", "47 fail with NotImplementedError; 37 ... CatalogException" and the Outputs/Checks lines have not been updated; Revision 2 gives the correct 94 tests and 93 failed / 745 passed. Also Revision 2 says all 7 new tests fail with `NotImplementedError` (correct: 47 + 7 = 54). Action: planner to mark the first section as superseded at the next touch. Does not block.
3. INFO: the "Human ratification" list in `planner.md` numbers 1-9 and the revision adds 10-11; map them to D1-D11 in the spec section 12 (the spec table is the authority).
4. INFO: the round-1 process slip (script edit of the tests) is on record; Revision 2 reports no repeat.
5. INFO: `notebooks/INC-001-findings-guide.ipynb` is unrelated and must stay out of the baseline commit. The scratch probe `/tmp/probe_inc007.py` was deleted.
6. INFO (carried): no reference implementation has run against these tests; gate 2 must run the real implementation and the real-data evidence.

Human-decision items (not defects; confirm or change at gate G2; spec stays Draft until then):
- D1 `week_id` INTEGER 1..4 with explicit period dates.
- D2 minimum coverage `MIN_VALID_DAYS = 4` of 7, per measure, coverage counts never gated.
- D3 measure list and statistics (section 5); `steps_total` sums valid days only.
- D4 survey included as four weekly means with valid-day counts; not counted in `valid_days`.
- D5 quality outputs, `run_id`, report unchanged.
- D6 column names and types (section 3).
- D7 minute-weighted weekly heart-rate mean.
- D8 trailing partial week dropped; windows under 7 days give an empty table.
- D9 `valid_days` = at least one valid wearable value.
- D10 stage-to-session attribution by `stage_index` restart in silver row order; identical sessions give one value set, conflicting ones withhold.
- D11 direct `refresh(con, affected)` on a missing or wrong-layout table recreates and recomputes all weeks (alternative: raise an error).
- Planner choices also to confirm: one coverage column for deep share and efficiency; `refresh(None)` prunes keys outside the computed set (as ratified for INC-006).

Next action:
Orchestrator: planning gate 1 is PASS. Route to human review/ratification of D1-D11, then the baseline commit (exclude `notebooks/INC-001-findings-guide.ipynb`), then the implementer. Optionally ask the planner to fix the stale first section of `reports/INC-007/planner.md` (finding 2); not blocking.

## Round-1 history (short)

Round 1 was REJECTED, Blocker-Type TESTS (86 failed, 745 passed). Findings: (1) MEDIUM, no test for identical duplicate sleep sessions (a pooled-stage implementation gives 800 and passed everything); (2) MEDIUM, no test for folder-mismatch steps/heart-rate rows or for out-of-window rows; (3) LOW, spec did not say how stages are attributed to sessions; (4) LOW, a direct `refresh(con, affected)` on a missing or wrong-layout table was undefined; (5) INFO, only the default study start was tested. The round-1 hand verification of fixture A's expected values, the incremental-design check and the plausible-wrong-implementation search all passed and stand. All five findings are resolved above (finding 2 partly effective for the out-of-window clause, see round-2 finding 1).
