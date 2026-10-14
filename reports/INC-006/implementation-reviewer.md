# Implementation Review Report: INC-006

Status: APPROVED
Verdict: PASS (no blocker or major findings; one Medium process item for the human, three Low observations)
Inputs:
- intents/INC-006-incremental-mode-and-compare.md, specs/INC-006.md
- reports/INC-006/planner.md (including Revision 3), reports/INC-006/implementer.md, reports/INC-006/planning-reviewer.md, reports/INC-005/implementation-reviewer.md (format)
- src/wearable_pipeline/incremental.py, compare.py, pipeline.py, cli.py (git diff vs HEAD)
- tests/test_incremental.py (git diff), tests/test_compare.py

Outputs:
- reports/INC-006/implementation-reviewer.md

Checks:
- `python -m pytest -q`: PASS (744 passed in 163.7 s; matches the expected count)
- Test-edit audit (`git diff tests/`): PASS. The only test change is in `tests/test_incremental.py`: new helpers `prepared_cli` and `fresh_default_full`, and one added `capsys.readouterr()`. This is the planner's Revision 3 fix, not an implementer edit. The two tests (`test_default_mode_ignores_an_existing_database_like_before`, `test_cli_incremental_run_and_noop_report_their_mode`) keep every assertion: same mutation, same CLI call, same `state(out) == state(fresh)` equality, same `mode=incremental` then `noop` checks. Only the baseline changed, to a database built through the CLI with its 28-day default window. The old fixture mixed a 3-day window with the 28-day CLI. The equality is not weakened (full `state()` comparison of every table, column and the report bytes). `tests/test_compare.py` is unmodified.
- `grep NotImplementedError` in src/ and tests/ (sources): PASS. No matches in .py files (only stale .pyc in `__pycache__`); no `pytest.raises(NotImplementedError)`. Stubs in `incremental.py` and `compare.py` are fully replaced.
- No new dependencies: PASS. `pyproject.toml`, `uv.lock`, `requirements/`, `Dockerfile`, `.github/`, `scripts/` and `data/` show no changes. Imports are standard library plus DuckDB.
- Scope: PASS. Changes are confined to `src/` (4 files), `tests/test_incremental.py` and `reports/INC-006/`. No new gold table, quality rule, weekly or cohort logic, or documentation.
- `data/` access: PASS (not read; only synthetic fixtures from the tests' own helpers in temp dirs).
- Randomized equivalence probe (`/tmp/probe/fuzz.py`, outside the repo): PASS. 80 seeds x 4 successive runs. Mutations: random create/overwrite/delete of hr, steps and sleep files for P001, P002, P003 and an orphan P999. Contents include invalid values, bad timestamps, out-of-window dates, records carrying another participant's id (folder mismatch), cross-participant sleep sessions, and a non-event file. Occasional survey change. After every incremental run `compare_outputs` against a fresh full rebuild reported identical (all tables, column types, report bytes); 0 mismatches. A run that raised `PipelineError` left the full `state()` unchanged.
- Targeted probes (`/tmp/probe/p2.py`): PASS
  - Corrupt or invalid files (`{bad`, invalid UTF-8): `PipelineError`; a SHA-256 snapshot of every file in the output directory (database, report) is unchanged.
  - A `DERIVED_TABLES` entry that creates a table and raises: the original exception propagates; output snapshot unchanged (table not left behind, report not rewritten).
  - No-op: outcome `noop`, `affected_gold_rows = 0`, snapshot unchanged on a second run, and works when `wearable.duckdb` is chmod 444 (never opened for writing).
  - Old schema: dropped table, added column, changed column type, two `run_window` rows and a dropped gold column each give `full` with `old_schema`. Emptied `raw_manifest` gives `no_manifest`. A garbage database file raises `PipelineError` and is left byte-identical.
  - Window change (28 -> 30 days) gives `full` / `window_changed` and equals a fresh rebuild. Removing P002's hr file gives `incremental`, 1 affected gold row, equal to a full rebuild. Raw data directory unchanged by an incremental run.
  - Compare: output directories are byte-identical before and after (no `.wal`, no new files); identical outputs print `identical` and exit 0; a changed gold row gives `rows differ` with counts only; an extra report file gives `missing in left`; a changed report gives `differs`; a table present on one side only (`gold_new`) and an added column are discovered dynamically (`columns differ: zzz`); a nonexistent directory exits 1 with `error:` on stderr. Printed lines hold only table names, column names, report paths and counts.
  - Cross-file participant ownership (hr folder mismatch, sleep participant differing from folder) is covered by the fuzz (mismatch and cross-participant generators) and equals a full rebuild.
- Full-mode behaviour: PASS. All 582 pre-existing tests pass unchanged; the CLI line and report format are unchanged. Additional side tables are written by full mode as the spec requires.
- Code quality: PASS. Builders are reused rather than duplicated (per-file build, `_file_stats`, shared `_persist`, `_merge_gold`, `_reject_rows`). The extension point is read at call time (`incremental.DERIVED_TABLES`), is called inside the transaction in both `run_pipeline` and `run_incremental`, and is skipped for a no-op. No dead code observed (static linters are not installed here; I checked imports by reading).

Findings:
- Blocker: none.
- Major: none.
- Medium (process, for the human; no code change): `reports/INC-006/implementer.md` still says `Status: BLOCKED` (two defective tests). The planner has since fixed the tests (Revision 3) and the suite is green at 744. The implementer report needs its status updated to `READY` / READY FOR REVIEW, and the real-data evidence section stays a human step. I did not edit it.
- Low 1: The report is moved into place after the transaction commits (as specified, section 7). If that move fails (I forced it with a directory at `report.md.tmp`) the database is already updated and `PipelineError` is raised; the next incremental run is then a `noop` and never regenerates the stale `report.md`. This only happens on a local I/O failure and matches the spec text, but a later spec revision could define a repair (for example, compare the report text, not only the manifest, in the no-op check).
- Low 2: Full mode previously added missing gold columns with `ALTER ... ADD COLUMN IF NOT EXISTS`. `_ensure_gold` now rebuilds the table by copying the columns shared with the current layout, so columns that are not part of the current layout are dropped and a column type or order difference is normalised. For the current layout and all INC-002 to INC-005 upgrade paths this is equivalent (existing tests pass), but it is a small behaviour change beyond a literal reading of "full-mode behaviour unchanged".
- Low 3: Pruning applies only on the incremental shared-input and window-change path, as ratified by the human; full mode keeps stale gold rows. A fallback caused by `no_database`, `no_manifest` or `old_schema` is a plain `run_pipeline` and also keeps them (matches spec section 5).
- Real-data evidence (full run then no-op, one corrected record, extra day or file, timings, `data/` SHA-256) is not run by this review; it remains the human step. The review notebook `notebooks/INC-006-review.ipynb` is also a human step. `notebooks/INC-001-findings-guide.ipynb` is unrelated and untouched.

Next action:
Orchestrator: record INC-006 as passing Gate 2. Human: update `reports/INC-006/implementer.md` status to READY FOR REVIEW, run the real-data evidence on a copy of `data/`, create `notebooks/INC-006-review.ipynb`, then commit. Nothing was committed by this review.
