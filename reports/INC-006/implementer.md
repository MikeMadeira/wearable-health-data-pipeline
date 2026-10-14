# Implementation Report: INC-006

Status: READY
Inputs:
- intents/INC-006-incremental-mode-and-compare.md
- specs/INC-006.md
- reports/INC-006/planner.md (incl. Human ratification and Revision 2)
- reports/INC-006/planning-reviewer.md
- tests/test_incremental.py, tests/test_compare.py
- src/wearable_pipeline/incremental.py, compare.py (stubs), pipeline.py, cli.py, steps.py, sleep.py, heart_rate.py

Outputs:
- src/wearable_pipeline/incremental.py
- src/wearable_pipeline/compare.py
- src/wearable_pipeline/pipeline.py
- src/wearable_pipeline/cli.py
- reports/INC-006/implementer.md

Checks:
- python -m pytest -q: PASS (744 passed, about 171 s) after the planner corrected the two test defects (planner.md Revision 3). Baseline was 158 failed, 586 passed. The first implementation run ended at 2 failed, 742 passed; the failures were test defects (see Findings), not code defects. The implementation reviewer then passed it (reports/INC-006/implementation-reviewer.md).
- python -m pytest -q tests/test_incremental.py tests/test_compare.py: all passed after Revision 3.
- python -m pytest -q tests/test_heart_rate.py: PASS (129 passed; 60,000-record scale test 1.77 s against the 25 s limit; INC-005 recorded 1.70 s).
- `data/` was never read or run on. Tests and specs were not edited. No new dependency, no commit.

Findings:
- RESOLVED (was a BLOCKER, Blocker-Type TESTS; the planner fixed the tests in Revision 3): two tests cannot pass with any implementation that follows the spec and the other tests. Both build their database with `prepared()` (a 3-day window, `DAYS = 3`) and then go through the CLI, which uses the default 28-day window.
  1. `tests/test_incremental.py::test_default_mode_ignores_an_existing_database_like_before`: a default-mode (full) CLI run on the 3-day database gives 56 gold rows (28 days x 2 participants, the old rows are retained by MERGE) and `run_window = (2026-01-08, 28)`. The test compares the whole state with a fresh 3-day full rebuild (6 gold rows, `run_window = (…, 3)`). Only `gold_clinical_daily` (56 vs 6 rows) and `run_window` differ. Full mode must retain gold rows (existing tests, spec section 6) and must store the requested window (spec section 2), so this cannot be equal.
  2. `tests/test_incremental.py::test_cli_incremental_run_and_noop_report_their_mode`: expects `mode=incremental` and one stdout line after a corrected record. The CLI passes the default 28-day window (spec section 1), the stored window is 3 days, so the run correctly takes the `window_changed` path (outcome `full`, second line `fallback: full rebuild (window_changed)`); the same behavior is demanded by `test_changed_window_takes_the_shared_input_path_then_is_stable`. The test also compares with a fresh 3-day rebuild.
  Suggested fix (planner): build the database for these two tests with the default window (for example by calling `main(["run", ...])` or `run_pipeline` without `study_start`/`study_days`, and compare with a fresh full run on the default window), or use a CLI-reachable window. The spec needs no change.
- All other 742 tests pass, including every equivalence, cross-file ownership, old-schema, window, no-op, rollback, extension-point, compare and CLI case.

Next action:
Done: the planner corrected the two tests, the suite passes (744), the implementation reviewer passed it and the real-data evidence is recorded below. Remaining: the human creates `notebooks/INC-006-review.ipynb` and commits.

## Summary of the implementation

- `pipeline.py` (full mode behavior and `run_id` unchanged; `run_pipeline` keeps its signature and now delegates to `_run_full`):
  - Every run writes three side tables: `run_window`, `quality_file_counts` and `silver_file_owners`. Per-file counts and owners come from `_file_stats`, which relies on the builders emitting one session/minute row per record, in sorted-path then position order, with every rule id in the row's last column.
  - Table DDL moved to `TABLE_DDL` (one source of truth, also used to compute the expected schema). `_ensure_gold` creates `gold_clinical_daily`, or rebuilds it (keeping common columns) when its ordered (name, type) list differs from the 28 current columns; this replaces the old `ADD COLUMN IF NOT EXISTS` loop and also repairs changed types and column order. `_merge_gold` is the same MERGE statement as before, with an optional prune (used only by the incremental shared-input and window-change path).
  - `_persist` calls `incremental.DERIVED_TABLES[...].refresh(con, None)` inside the transaction (read at call time through a lazy import to avoid a cycle). Any exception rolls back; DuckDB errors are wrapped in `PipelineError`, other exceptions propagate unchanged.
  - JSON reading is shared in `_read_event_json`; the report text is separated from writing (`_report_text`).
- `incremental.py`: `diff_manifests`, `changed_days`, `read_event_file` and `run_incremental` as in spec sections 2 to 7.
  - Order: validate inputs, no database (`no_database`), read-only snapshot of the stored state (`no_manifest`, `old_schema` by ordered (name, type) comparison against an in-memory database built from `TABLE_DDL`), shared input or window change (full rebuild with gold pruning), no-op, otherwise incremental.
  - Incremental: parse new and changed event files only; per source, if a participant is owned by two current files the whole source is re-parsed (changed files are not parsed twice) and replaced. Silver rows are replaced by participant, rejects by `row_ref` path prefix (or by `source` for a whole-source replacement), side tables and `quality_results` rebuilt from the per-file counts, `raw_manifest` replaced, `run_id` rewritten in rejects. Previous and new silver rows per participant are compared with `changed_days`; only existing gold rows of changed days are staged, with only that source's six columns replaced.
  - The read phase uses a read-only connection and everything is parsed before the write transaction opens, so a failure there leaves the directory byte-identical. The report is written to `report.md.tmp` and moved over `report.md` after the commit.
- `compare.py`: both databases are attached read-only to an in-memory connection (one attach if both directories point to the same file); tables are discovered dynamically; ordered (name, type) columns are compared first, then rows with `EXCEPT ALL` in both directions; every file under `quality/` is compared by bytes. `format_comparison` prints names and counts only.
- `cli.py`: `run --mode {full,incremental}` (default `full`, same single output line) and `compare LEFT RIGHT` (exit 0 identical, 1 differ or unreadable with `error:` on stderr).

## Decisions and deviations

- Fallbacks 1 to 3 and the shared/window path report `affected_gold_rows` as the number of gold rows staged by the rebuild; spec only fixes 0 for a no-op. The fallback paths 1 to 3 leave `new_paths`, `changed_paths` and `removed_paths` empty; the shared/window path fills them from the manifest diff.
- `_ensure_gold` rebuilds a gold table whose column list differs from the current one, instead of only adding missing columns. For tables that only lack trailing columns (10, 16 and 22 columns) the result is the same as before (existing rows kept, new columns NULL until merged).
- For a column-order-only difference, `TableDiff.columns` lists the names whose position differs.
- All file changes were made with the edit and create tools. `/tmp/dbg.py` (a throwaway diagnostic reading only synthetic test fixtures) was written with a heredoc, outside the repository, and its output was only used to diagnose the second failure above.

## Scale-test timing

- INC-005 60,000-record heart-rate scale test: 1.77 s (limit 25 s). No INC-006 scale test exists. The two new test files take about 89 s together; the full suite about 144 s.
- Real-data timings of full versus incremental runs are for the human step (on a copy of `data/`); not run here.

## Open issues

- None blocking. `notebooks/INC-006-review.ipynb` is a human step after review.

## Real-data evidence (run on copies of `data/`)

Run on 2026-10-13 from a throwaway driver (`/tmp/inc006_evidence.py`, outside the repo) on copies of `data/` under `/tmp/inc006`. Raw values were not printed. `data/` was never used as a run input.

| Scenario | Run | Time | Peak RSS | Result |
|---|---|---|---|---|
| A: baseline | full | 76.9 s | 4.8 GB | `run_id=a826c8397cf5ad8e` (same as INC-005) |
| A: nothing changed | incremental | 0.4 s | 0.08 GB | `mode=noop`, same run id, `compare` vs pre-run snapshot: identical, `quality/` tree hash unchanged |
| B: one P001 steps record corrected (2026-01-20T12:00) | incremental | 2.7 s | 0.17 GB | `compare` vs full rebuild of the same copy (85.4 s, 4.9 GB): identical, equal run id `41c339cef2ec4025`; only gold row (P001, 2026-01-20) differs from baseline |
| C: P050 `sleep.json` added (baseline lacked it) | incremental | 2.1 s | 0.10 GB | `compare` vs full rebuild (78.3 s): identical, run id back to `a826c8397cf5ad8e`; gold differs from baseline only for P050's 28 days |
| D: extra day (2026-02-05 steps records for P002, outside the window) | incremental | 2.7 s | 0.17 GB | `compare` vs full rebuild (78.1 s): identical, equal run id `b7beb2ce9b14f2e9`; gold rows unchanged (1,400), only quality outputs change |

Every `compare` exited 0 with `identical`. The `data/` tree hash (path plus SHA-256 of every file) was `abd71e95…012b` before and after: unchanged.

Observations: incremental runs cost 2-3 s against 77-85 s for a full rebuild (about 30x faster); the no-op run is 0.4 s. Scenario C's baseline was a full run on a copy missing one file, so it also shows incremental recovering the full result when a file appears.
