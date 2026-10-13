# Planning Review Report: INC-006 (round 2)

Status: APPROVED
Verdict: PASS
Inputs:
- intents/INC-006-incremental-mode-and-compare.md (unedited)
- specs/INC-006.md (Status: Draft), specs/INC-002.md (git diff: one CLI line)
- reports/INC-006/planner.md (incl. Revision 2 and Human ratification), previous round-1 review
- src/wearable_pipeline/incremental.py, compare.py (stubs); pipeline.py, cli.py and the builders for design validity
- tests/test_incremental.py, tests/test_compare.py
- `data/` not read

Outputs:
- reports/INC-006/planning-reviewer.md (this file, overwrites round 1)

Checks:
- `python -m pytest -q` (repo root): PASS as baseline. **158 failed, 586 passed** (126 failures in `test_incremental.py`, 32 in `test_compare.py`, none elsewhere). The two new files hold 162 cases: 158 red, 4 green (signature, default-mode and unknown-mode checks and compare's wrong-argument count, which are invariants).
- `pytest.raises(NotImplementedError)`: PASS. No match in `tests/` (the only grep hits are `.pyc`).
- Stubs: PASS. `incremental.py` and `compare.py` keep dataclasses, constants, the `DerivedTable` protocol and `DERIVED_TABLES = []`; every function body is only `raise NotImplementedError`. `cli.py`, `pipeline.py`, `__init__.py` untouched.
- No weakened tests: PASS. `git diff` over `src tests scripts .github intents` is empty; the only tracked change is `specs/INC-002.md` (CLI line). The new test files are untracked.
- Round-1 finding 1 (cross-file ownership): RESOLVED. `test_participant_owned_by_two_files_reparses_the_whole_source` is 3 sources x {change P002, change P001, remove P002, remove P001} = 12 cases. It asserts equality with a fresh full rebuild in every table and the report, the exact set of parsed paths (every remaining file of that source, no other source, no removed file), changed gold keys and `affected_gold_rows`. A changed-file-only re-parse fails the parsed-paths assertion and, for sleep, the duplicate flag and count. I re-ran two fresh full builds per case and the 12 expected changed-key sets all match. Further tests cover the sleep duplicate count over both files, the both-files `is_duplicate_key` flag, and a control (an unrelated participant file is parsed alone). These are satisfiable and agree with spec section 4 step 3 and section 11.
- Round-1 finding 2 (old-schema type and order): RESOLVED. Nine new `DEGRADE` cases: type changes on gold, `quality_results`, `raw_manifest`, `quality_file_counts`; column order on gold, `raw_manifest`, `silver_steps_minute`, `silver_file_owners`; missing `run_window`. I checked that each altered column really is BIGINT today, so the ALTER to INTEGER, DOUBLE or VARCHAR is a real change and not a no-op. A name-only check would fail them. Agrees with spec section 5.
- Round-1 finding 3 (explicit changed-gold sets): RESOLVED. `EXPECTED_DEFECT_CASES` fixes the changed keys and staged rows for `defects` ({P001 DAY1, DAY2}, 2), `orphan_folder` (set(), 0), `hr_folder_mismatch` (set(), 1), `sleep_pid_differs` ({P002 DAY2}, 1), plus equality with a full rebuild.
- Round-1 finding 4 (run_window): RESOLVED in spec (sections 2, 3, 5, 11) and tests: the window is stored by every run, shorter, longer and shifted windows give `window_changed` and then a stable no-op, stale gold with unchanged inputs and window is a no-op, empty gold is a no-op, stale gold with an event change stays incremental, a window equal to the stale gold dates is still a change (so the gold dates are never used), and a window change prunes stale gold. No test contradicts the spec.
- Findings 5 and 6: RESOLVED. Human ratification of gold pruning on the shared and window path and of the side tables is recorded in planner.md ("Human ratification"); `run_window` is recorded as a third side table under the same rule. The pruning test is `test_participant_removed_prunes_its_gold_rows_unlike_full_mode_merge`.
- Spec vs intent: PASS. No goal, requirement or constraint of the intent is dropped; the ratified additions (pruning, three side tables, one full rebuild for pre-INC-006 databases) are stated in the spec.
- New-defect search (tests that cannot be satisfied, contradict the spec, or stay red for the wrong reason): none blocking. See finding 1 for a precedent-class note.

Findings:
1. INFO: `DEGRADE` cases that drop a table the current pipeline does not yet create (`missing_window_table`, `missing_file_state_table`, `missing_owner_table`) and the two that alter or reorder such a table (`side_table_type_changed`, `owners_columns_reordered`) fail today in their setup (`CatalogException` or `IndexError`) instead of at the stub. They become real checks once the implementer writes the side tables, same precedent as round 1. The other 13 degradation cases fail at `NotImplementedError`. Accepted.
2. LOW (planner.md, stale text, not a blocker): the report was only partly updated for round 2. "Outputs" says 58 test functions in `test_incremental.py` and "126 collected cases"; the actual numbers are 70 functions, and 162 cases in the two files (126 + 36 added). The Checks entry still carries round-1 sentences: "All 121 failures", "5 new tests already pass" (actual 4) and "No new test passes today" (contradicts the 4), and "36 fail with NotImplementedError; 3 of the 9 new old-schema cases" understates the setup-failing cases (finding 1). The headline 158 failed / 586 passed is correct. Action for the planner at the next touch of the file: correct these counts. No test or spec change is needed.
3. INFO: `notebooks/INC-001-findings-guide.ipynb` is untracked and unrelated; it must stay out of the INC-006 baseline commit.
4. INFO (carried): no reference implementation has run against these tests. Gate 2 must run the real implementation and the real-data evidence.

Round-1 history (short): round 1 was REJECTED with Blocker-Type TESTS (121 failed, 586 passed). Finding 1 (MEDIUM): no test where two files of one source share a participant. Findings 2 and 3 (LOW): old-schema type/order and explicit changed-gold sets were missing. Findings 4 to 6 (LOW): window inference from gold dates, gold pruning and the two side tables needed human decisions. Findings 7 to 9 were informational. All are resolved above.

Next action:
Orchestrator: planning gate 1 is PASS. Route to human review of the Draft spec and red baseline, then the baseline commit (exclude `notebooks/INC-001-findings-guide.ipynb`), then the implementer. Optionally ask the planner to correct the stale counts in `reports/INC-006/planner.md` (finding 2); this does not block.
