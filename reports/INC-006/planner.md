# Planning Report: INC-006

Status: READY
Inputs:
- intents/INC-006-incremental-mode-and-compare.md (read only, not edited)
- .github/skills/sdd-tdd/SKILL.md, .github/skills/agent-communication/SKILL.md
- specs/INC-005.md, reports/INC-005/planner.md, tests/test_heart_rate.py (precedent)
- src/wearable_pipeline/pipeline.py, cli.py, steps.py, heart_rate.py, sleep.py, __init__.py
- `data/` was not read; all fixtures are synthetic and written to tmp_path.

Outputs:
- specs/INC-006.md (Status: Draft)
- src/wearable_pipeline/incremental.py (stub: results, `diff_manifests`, `changed_days`, `read_event_file`, `run_incremental`, `DerivedTable`, `DERIVED_TABLES = []`; function bodies are only `raise NotImplementedError`)
- src/wearable_pipeline/compare.py (stub: `TableDiff`, `CompareResult`, `compare_outputs`, `format_comparison`)
- tests/test_incremental.py (58 test functions), tests/test_compare.py (26); 126 collected cases with parametrization
- specs/INC-002.md (one edit: the CLI line now lists `--mode` and points to INC-006)
- reports/INC-006/planner.md (this file). The directory `reports/INC-006/` was made with `mkdir` because the create tool needs an existing parent; no other directory was made.
- No existing test was edited or weakened: none asserts the CLI surface or a full-rebuild-only behavior in a way that conflicts. The "full rebuild" sentences in specs/INC-002/003/004 describe full mode and stay true. `cli.py`, `pipeline.py` and `__init__.py` are not touched (the implementer wires them).

Checks:
- python -m pytest -q (Revision 2): FAIL as intended, **158 failed, 586 passed** (round 1: 121 failed, 586 passed; the previous 586 stay green). The 37 added red tests (all in `tests/test_incremental.py`): 36 fail with `NotImplementedError` from the stub; 3 of the 9 new old-schema cases (`side_table_type_changed`, `owners_columns_reordered`, `missing_window_table`) already fail in their setup with `CatalogException`/`IndexError` because the side table they degrade does not exist yet (same precedent as `missing_file_state_table`); they turn into real checks once the implementer creates the tables. No new test passes today, no collection error, `grep raises(NotImplementedError` in `tests/`: 0 matches. Before this increment: 582 passed. All 121 failures are in the two new files. Most fail with `NotImplementedError` from a stub (calls to `run_incremental`, `compare_outputs`, `diff_manifests`, `changed_days`). The rest fail on the missing CLI surface (`--mode` and `compare` give `SystemExit: 2` from argparse) or on missing side tables (`CatalogException`). They fail for missing behavior, not for broken fixtures. 5 new tests already pass (negative or invariant checks: signatures, default mode and its unchanged stdout line, an unknown mode rejected, compare's wrong argument count). No collection errors.
- grep for `raises(NotImplementedError`: 0 matches. A test-only exception class (`InjectedFailure`) is used for rollback tests because `NotImplementedError` is a `RuntimeError` and would pass `pytest.raises(RuntimeError)` vacuously.
- Fixture facts were checked against the current full pipeline (throwaway script in /tmp, nothing written to the repo): for each parametrized mutation the gold rows that differ between two fresh full runs equal the expected key set, and the quality counts used in the global-count test match.

Findings:
- Process note: I made one small edit to `tests/test_incremental.py` (removed a pointless test, replaced two assertions) with a one-off Python script instead of the edit tool. The resulting file is a plain test file; no other file was written by script.
- Nothing is committed. `intents/`, `data/`, `scripts/`, `.github/`, `notebooks/` were not touched. `notebooks/INC-001-findings-guide.ipynb` shows as untracked in git status; it was not created by this phase.
- Risk for the implementer: the per-file additivity of `build_*_layer` (spec section 4, steps 2 and 5) is assumed. The equivalence tests (`defects`, `hr_folder_mismatch`, `sleep_pid_differs`) will expose it if it fails.

## Human ratification
- Finding 4 (window, human decision, resolved): the study window is stored in the database and
  window changes are detected by comparing the stored window with the requested one. Stale or
  empty gold no longer forces a rebuild. Planner choice: a new one-row side table
  `run_window(study_start DATE, study_days INTEGER)`, written by every run (see Revision 2).
- Finding 5 (ratified by the human): gold pruning outside enrolled participants x window in
  incremental mode (shared-input and window-change path only) stays; full mode keeps retaining
  stale gold rows. Pinned by `test_participant_removed_prunes_its_gold_rows_unlike_full_mode_merge`.
- Finding 6 (ratified by the human): the two side tables `quality_file_counts` and
  `silver_file_owners` stay; databases from before INC-006 fall back to one full rebuild
  (`old_schema`). The finding-4 table `run_window` is a third side table added under the same
  rule (it also makes pre-INC-006 databases fall back once; no new effect).

## Revision 2 (planning-reviewer round 1, CHANGES REQUESTED)
Only the spec, `tests/test_incremental.py` and this report changed. No existing test was edited or
removed; `tests/test_compare.py`, the stubs, `intents/`, `data/`, `scripts/`, `.github/` and
`notebooks/` are untouched (signatures needed no change: `run_pipeline` writes the window).
- Finding 1 (MEDIUM): checked against the real builders. `sleep.py` counts `(participant_id, date)`
  across all files, so `SLEEP_DUPLICATE_PARTICIPANT_DAY` and `is_duplicate_key` span two files;
  `steps.py` (and heart rate) take `participant_id` from the record, exclude folder-mismatch
  records from duplicate groups and so own rows of another folder's participant. Spec section 4
  step 3 now states the rule: when a participant is owned by two files of one source, re-parse all
  remaining files of that source (via `read_event_file`, no other source) and replace it.
  New tests: `test_participant_owned_by_two_files_reparses_the_whole_source` (3 sources x change
  P002 file / change P001 file / remove P002 file / remove P001 file = 12 cases; equality with a
  fresh full rebuild in every table and report bytes, the exact parsed paths, the changed gold keys
  and `affected_gold_rows`), `test_sleep_duplicate_count_spans_the_two_files_and_follows_each_change`,
  `test_sleep_session_under_the_wrong_folder_is_flagged_as_duplicate_in_both_files`, and the
  control `test_an_unrelated_participant_file_is_not_reparsed_with_the_whole_source`. The existing
  `hr_folder_mismatch` and `sleep_pid_differs` cases are kept. A changed-file-only implementation
  fails the equality and the parsed-paths assertions.
- Finding 2 (LOW): 9 new `DEGRADE` cases: column type changed in gold, `quality_results`,
  `raw_manifest` and `quality_file_counts`; column order changed in gold, `raw_manifest`,
  `silver_steps_minute` and `silver_file_owners`; missing `run_window`. Spec section 5 says type
  or position differences are `old_schema`.
- Finding 3 (LOW): `test_defect_and_mismatch_cases_change_exactly_the_expected_gold_rows` gives
  explicit changed gold keys and `affected_gold_rows` for `defects` ({P001 DAY1, P001 DAY2}, 2),
  `orphan_folder` (set(), 0), `hr_folder_mismatch` (set(), 1) and `sleep_pid_differs`
  ({P002 DAY2}, 1). Values were taken from two fresh full runs of the fixtures (throwaway script in
  /tmp, deleted), not from `data/`.
- Finding 4: spec sections 2, 3, 5, 11 updated; `run_window` added to the old-schema table list.
  New tests: `test_study_window_is_stored_by_every_run`,
  `test_changed_window_takes_the_shared_input_path_then_is_stable` (shorter, longer, shifted),
  `test_stale_gold_rows_with_unchanged_inputs_and_window_are_a_noop`,
  `test_empty_gold_with_unchanged_inputs_and_window_is_a_noop`,
  `test_stale_gold_with_an_event_change_stays_incremental`,
  `test_window_equal_to_the_stale_gold_dates_is_still_a_window_change`,
  `test_stale_gold_is_pruned_when_a_window_change_takes_the_shared_path`.
- Findings 5 and 6: recorded above. Findings 7 to 9: no action (gate 2 will run the reference
  implementation; the notebook is not part of this baseline; the one-off script note stands).
- Decisions list: decision 9 is replaced by D17 (window stored in `run_window`, edge cases
  removed); decision 11 now lists 13 tables (adds `run_window`); decisions 8 (ratified), 4 and
  the side-table count (ratified, plus D17) are marked above.

## Decisions to ratify
1. **Flag**: `run --mode {full,incremental}`, default `full`. `--mode full` and no flag call `run_pipeline` and print the same single stdout line as today. `--mode incremental` prints that line plus ` mode=<outcome>` and, after a fallback, a second line `fallback: full rebuild (<reason>)`. Compare is `compare LEFT RIGHT`.
2. **Entry point**: new `run_incremental(data_dir, output_dir, *, study_start, study_days) -> IncrementalResult`. `run_pipeline` gets no new parameter. Outcomes: `incremental`, `noop`, `full`; fallback reasons `no_database`, `no_manifest`, `old_schema`, `shared_input_changed`, `window_changed`.
3. **Previous manifest**: the stored `raw_manifest` table. The current manifest is built as today, which reads and hashes every file. "Unchanged files are not re-read" is interpreted as "not parsed"; the test seam is `incremental.read_event_file`, which tests spy on. No size or mtime shortcuts.
4. **Previous silver and ownership**: the previous silver is the silver tables themselves. Silver rows carry no source path, so two side tables are added and written by every run, full or incremental: `quality_file_counts(relative_path, source, rule_id, checked_count, failed_count)` and `silver_file_owners(relative_path, silver_table, participant_id)`. This adds two tables to full-mode output; no existing column or value changes. If a participant is owned by two files of one source (a record whose `participant_id` differs from its folder; sleep does not even flag it), the whole source is re-parsed.
5. **Changed participant-days**: per affected source and participant, compare the previous silver rows (read from the database before deletion) with the freshly parsed rows as multisets per `local_date` (`changed_days`). Only that source's gold columns of the existing gold row are replaced. Days with no existing gold row are never inserted.
6. **Transactions**: one DuckDB transaction covers silver replacement, rejects, quality tables, side tables, manifest, gold MERGE (same statement form as full mode) and derived-table refresh. Parsing and report text come before it. The report is written to a temp file and moved over `report.md` after commit.
7. **Shared inputs**: a change to `participants`, `wellness_survey` or `device_metadata` (the latter feeds nothing today, but the intent says shared) or to the study window rebuilds all layers in one transaction, outcome `full`. Documentation and unclassified files only touch manifest, run id and report.
8. **Gold pruning (new behavior, please confirm)**: full mode retains stale gold rows (existing test). For equality with a fresh rebuild after a participant is removed, the shared-change path prunes gold rows outside enrolled x window. Fallbacks `no_database`, `no_manifest`, `old_schema` run plain `run_pipeline` and do not prune.
9. **Window (revised, D17)**: the window is stored in the one-row side table `run_window(study_start DATE, study_days INTEGER)`, written by every full and incremental run. It is compared with the requested window; gold dates are never used. Stale or empty gold with unchanged inputs and window is a no-op.
10. **Fallback order**: no database file; `raw_manifest` missing or empty; old schema; shared or window change; no-op; else incremental. A corrupt database file raises `PipelineError` and is left alone.
11. **"Old schema"**: any of the 13 tables (manifest, 7 silver, quality_results, quality_rejects, the three side tables, gold) is missing, or its ordered (name, type) column list differs from what the current pipeline creates (gold: the 28 INC-005 columns). A database from before this increment therefore falls back once, because it lacks the side tables.
12. **Quality equivalence**: `quality_results` is the sum of per-file `quality_file_counts` plus the stored participants and survey counts (their inputs are unchanged on this path). Rejects of changed and removed paths are deleted by `row_ref` prefix and re-inserted; all `run_id` values are rewritten. Tests compare every table and the report bytes with a fresh full rebuild.
13. **No-op**: nothing is written and the database is not opened for writing; the output tree hashes are equal; `refresh` is not called.
14. **Compare**: positional `LEFT RIGHT`; `read_only=True` connections (no `.wal`); tables are all base tables in `main`; columns compared as an ordered (name, type) list; rows as a multiset (NULL equals NULL); report files are all files under `quality/`, compared by bytes. Output: `identical`, or `differ` then `table <name>: missing in left|right`, `table <name>: columns differ: <names>`, `table <name>: rows differ: left_rows=N right_rows=N left_only=N right_only=N`, `report <path>: differs|missing in left|missing in right`. Exit 0 identical, 1 differ or unreadable (stderr `error: ...`, stdout empty), 2 for wrong arguments (argparse).
15. **Extension point**: `incremental.DerivedTable` protocol with `refresh(con, affected)` and module-level `DERIVED_TABLES`, read at call time by `run_incremental` and `run_pipeline`. `affected` is `{participant_id: frozenset[date]}` of recomputed gold rows, or `None` meaning rebuild entirely (every `run_pipeline` call, every fallback and shared change). It runs inside the open transaction; a no-op skips it.
16. **Unchanged**: no new quality rule, gold column or dependency; `data/` is never written.

## Assumptions
- `build_sleep_layer`, `build_steps_layer`, `build_heart_rate_layer` give the same totals when called per file as on all files (checked by the equivalence tests, not by reading `data/`).
- The CLI keeps the default study window; the window parameters are reachable only through the Python functions.

Next action:
Route to the planning reviewer; after approval the human reviews the Draft spec and the red baseline. The implementer wires `pipeline.py`, `cli.py` and `__init__.py`, and implements `incremental.py` and `compare.py` without editing the tests.
