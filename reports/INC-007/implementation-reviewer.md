# Implementation Review Report: INC-007

Status: APPROVED
Verdict: PASS (no blocker or major findings; one Medium for the human, three Low observations)
Inputs:
- intents/INC-007-weekly-biostatistics.md, specs/INC-007.md (D1-D11, INC-006 amendment)
- reports/INC-007/planner.md (including Revisions 2 and 3), reports/INC-007/planning-reviewer.md, reports/INC-007/implementer.md (Status READY)
- src/wearable_pipeline/weekly.py, incremental.py (git diff vs HEAD); pipeline.py, sleep.py, steps.py, heart_rate.py, compare.py, cli.py for context
- tests/test_weekly.py, tests/test_incremental.py (git diff)
- reports/INC-006/implementation-reviewer.md and reports/INC-005/implementation-reviewer.md (format)

Outputs:
- reports/INC-007/implementation-reviewer.md

Checks:
- `python -m pytest -q` (repo root): PASS (839 passed in 238.6 s; matches the expected count)
- Test-edit audit (`git diff tests/`): PASS. The only change to an existing test is `tests/test_incremental.py::test_failure_inside_the_transaction_rolls_back_everything`: one added line saves `original_derived = incremental.DERIVED_TABLES` before the first monkeypatch, and the last step restores it instead of `[]`. Every assertion is intact (database bytes, report bytes, quality file list, only `wearable.duckdb` in the output root, `InjectedFailure` raised, then `assert_equals_full`). The purpose is unchanged (failure rolls back everything, then the same change succeeds and equals a full rebuild). The edit is needed because the pre-INC-007 `[]` removed the weekly refresh and made the incremental output differ from a fresh full rebuild. It is not weakened. It is the planner's Revision 3 edit, not an implementer edit.
- Revision 3 changes in `tests/test_weekly.py` (new file, so reviewed by reading): PASS. `weekly_type_changed` now alters the non-key `period_start_date` to TIMESTAMP (DuckDB cannot alter a PRIMARY KEY column); the new `weekly_key_type_changed` recreates the table with a BIGINT `week_id`, so the key-type case is still tested. The survey-only test deletes its own `steps.json` so that `valid_days == 0` is checked as named, because a recorded zero-step day is a valid day under spec section 4. `valid_days` stays "a day with a valid sleep, steps or heart-rate value" (D9), identical in `reconcile`. `make_data` is unchanged, as are the other weekly tests.
- No `pytest.raises(NotImplementedError)`, and no `NotImplementedError` in `src/` or `tests/`: PASS. The `weekly.py` stub is fully replaced. Implementer left tests and specs untouched (git status: tests/test_incremental.py is modified only by the planner's amendment).
- Baseline unchanged vs pre-INC-007 code: PASS. I ran the HEAD code (`git archive HEAD src` into /tmp) and the new code on the same synthetic fixture (14-day window, orphans, flags, duplicates). Every table except `gold_biostat_weekly` (gold_clinical_daily, all silver, quality_results, quality_rejects, quality_file_counts, raw_manifest, run_window, silver_file_owners), the report bytes, the `run_id` (`69010611858c6eea`) and the output file list are identical.
- No new dependencies: PASS. `pyproject.toml`, `uv.lock`, `requirements/`, `Dockerfile`, `data/` unchanged; `weekly.py` imports only the standard library, DuckDB and `sleep.compute_sleep_metrics`. No import cycle (`weekly` does not import `incremental`).
- Scope: PASS. `git status`: `src/wearable_pipeline/weekly.py` (new), `incremental.py` (+5 lines: import, `DERIVED_TABLES = [WEEKLY]`, expected table, expected schema), `tests/test_weekly.py` (new), `tests/test_incremental.py` (one test), `specs/INC-007.md`, `reports/INC-007/`. `pipeline.py`, `compare.py`, `cli.py` untouched. CLI help shows only `run` and `compare` (surface unchanged).
- `data/` access: PASS (not read; the pipeline was never run on it).
- Randomized equivalence and reconciliation fuzz (scratch under /tmp, deleted): PASS. 40 seeds x 3 successive incremental runs, windows of 6, 14, 17, 21 and 28 days (non-multiples of 7 and < 7), three enrolled participants plus an orphan folder. Mutations: random rewrite or delete of hr, steps and sleep files, with out-of-range and non-numeric values, duplicate and conflicting slots, duplicate identical and conflicting sleep sessions, a bogus stage, and sleep records carrying another participant's id (cross-file ownership). After every run `compare_outputs` against a fresh full rebuild was `identical` (all tables, column types, run_id and report) and the test suite's own `reconcile` (weekly vs `gold_clinical_daily`: totals, means, minute-weighted HR mean, min/max, valid-day counts, threshold) held. 0 mismatches; a `noop` never changed the weekly rows.
- Targeted probes: PASS.
  - Atomicity: a failure raised inside `weekly._compute` during an incremental run leaves the whole output directory (database, report) byte-identical (SHA-256 per file); the next run succeeds and equals a full rebuild.
  - Determinism: DOUBLE columns are bit-identical between the incremental result and an independent full rebuild (compared through `struct.pack`).
  - Shared input (participant removed, `P003` added): full fallback `shared_input_changed`, identical to a full rebuild, removed participant's weekly rows pruned.
  - Window change 14 -> 10, 7, 6, 21, 28: `full`/`window_changed`, identical to a full rebuild; 2, 2, 0, 6, 8 rows for two participants (6 days gives an empty table).
  - D11: direct `refresh` on a missing table (with an empty `affected`) and on a wrong-layout table (with `affected = {"P001": frozenset()}`) recreates the table and recomputes every week (8 rows), no error, no temp table left behind.
  - Compare: a changed `gold_biostat_weekly` row is reported as a table diff, so the new table is covered by name discovery.
  - Week bounds, `MIN_VALID_DAYS` boundary (3 vs 4), partial days, folder-mismatch, orphan, out-of-window, identical vs conflicting duplicate sleep sessions and old-schema fallbacks are exercised by the 95 weekly tests (all pass) and, for equality with daily, by the fuzz.
- Performance and memory (synthetic, deleted): PASS. 50 participants x 28 days x 1440 minutes of heart rate and steps (about 4 M records): full pipeline 93.8 s, of which weekly refresh 1.32 s (200 rows); incremental after one corrected record: 3.0 s total, weekly 0.03 s. Peak RSS 995 MB with weekly vs 999 MB with the refresh replaced by a no-op at 10 participants, so the refresh adds no measurable memory (the footprint is the existing pipeline's). Full-mode refresh is linear in the silver rows; per-key work uses a temp-table join, not per-row Python.
- Code quality: PASS with Low notes. One code path (`_compute`) serves full and incremental refresh. Participant ids reach SQL only as bound parameters of `executemany`; table and column names come from constants, so no injection path. The temp tables (`weekly_keys`, `weekly_stage`) are created inside the caller's transaction and issue no `BEGIN`/`COMMIT`; `weekly_keys` is dropped in a `finally`. `refresh` never raises for a wrong or missing layout (D11).

Findings:
- Blocker: none.
- Major: none.
- Medium (human decision, no code change needed): the INC-006 test amendment above touches an existing test. I judge it a faithful, non-weakening change required by the spec's INC-006 amendment, but it is a protected-artifact edit, so the human should confirm it at the commit. The extension point `DERIVED_TABLES` is now also non-empty by default, so any future test that sets it to `[]` and then compares with a full rebuild will hit the same issue.
- Low 1: `_in_scope(con, table, rule, alias)` takes two parameters it does not use (`del con, table`). The implementer already notes it. Harmless; trimming them would make the helper honest.
- Low 2: `_daily_sleep` re-derives sessions from stage rows by "a new session starts at `stage_index == 1`, in `rowid` order" (D10). It matches the daily builder in every tested and fuzzed case (including duplicate, conflicting and stage-less sessions), but it depends on silver insertion order and on the builder always restarting `stage_index` at 1. If the sleep builder changes how it numbers stages, only the reconciliation tests will notice. Acceptable as the ratified D10.
- Low 3: `_mean` is a one-line division helper and `_week_row` is long (about 50 lines of aggregation). Readable and gated per measure as the spec says; no dead code found.
- Process note: `reports/INC-007/implementer.md` has `Status: READY` (updated after Revision 3) but still carries the original BLOCKED evidence text, marked as resolved; fine. Real-data evidence (full run then no-op, one corrected record, extra day, timings, `data/` SHA-256 before and after) and `notebooks/INC-007-review.ipynb` were not produced here and remain human steps. `notebooks/INC-001-findings-guide.ipynb` is unrelated and untouched.
- Cleanup: all scratch files under /tmp (probe scripts, fixtures, the HEAD copy) were deleted. One empty placeholder file I created in `reports/INC-007/` by mistake was removed straight away; the directory now holds only the four expected reports. No commits.

Next action:
Orchestrator: record INC-007 as passing Gate 2 (`APPROVED`). Human: confirm the INC-006 test amendment, run the real-data evidence on a copy of `data/`, create `notebooks/INC-007-review.ipynb`, then commit. Nothing was committed by this review.
