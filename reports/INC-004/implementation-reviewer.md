# Implementation Review Report: INC-004

Status: APPROVED
Verdict: PASS (no blocker or major findings; two Low observations and one Info note)
Inputs:
- intents/INC-004-steps-silver-and-gold.md, specs/INC-004.md (revision 3)
- reports/INC-004/planner.md, reports/INC-004/planning-reviewer.md (round 5 APPROVED), reports/INC-004/implementer.md
- src/wearable_pipeline/steps.py, src/wearable_pipeline/pipeline.py (git diff vs HEAD)
- tests/ (read-only)

Outputs:
- reports/INC-004/implementation-reviewer.md

Checks:
- `python -m pytest -q`: PASS (453 passed in 33 s, includes tests/test_orchestrator.py)
- `git diff HEAD --stat` on tests/, specs/, intents/, scripts/, .github/, notebooks/, data/, requirements/, pyproject.toml, uv.lock, Dockerfile: PASS (empty; only the two src files are modified, plus the new untracked implementer report)
- New dependencies: PASS (none; stdlib `json`, `tempfile`, `datetime` and DuckDB only)
- No test special-casing or hardcoding: PASS (no literals tied to test fixtures; rules, thresholds and column lists are spec constants)
- Disclosed process slip (python script edits): PASS (`git status` shows no stray files in the repo; only the ignored caches that were already present. The resulting content matches the spec; the slip is procedural only)
- Spec conformance probes, synthetic data only in /tmp (data/ never read): PASS
  - Conversion and precedence: hourly 0 -> 0; hourly 2147483580 -> 35791393 (burst); hourly 2147483640 -> 35791394; hourly not divisible by 60, `Steps_per_hour`, `null`, `5`, `"5"` and `""` units -> `STEPS_UNIT_UNSUPPORTED` with NULL value; negative hourly -> `STEPS_NEGATIVE` only; bool, float, 2147483648 and missing `steps` -> `STEPS_VALUE_INVALID`
  - `STEPS_UNIT_CONVERTED`: counted in `quality_results.failed_count`, no `quality_rejects` rows (0 found); converted records keep the flag inside conflict groups
  - Duplicates by normalized value plus unit text: hourly 300 vs unit-less 5 in one minute -> conflict (both withheld, raw `steps` and `unit` kept); two hourly 600 -> exact (value 10 counted once); `""` vs `""` -> exact; unit `5` vs `"5"` -> distinct signatures, conflict as specified
  - Empty unit string preserved as `''` (not NULL) through the COPY path; JSON `null` unit stored as text `null`; absent key is NULL
  - Timestamps: `Z` and `-04:00` offsets crossing local midnight (04:59:30Z -> 01-08 23:59:30; 05:00Z and 01:00-04:00 -> 01-09 00:00) placed on the correct local dates; slots truncated for grouping while `minute_ts` keeps seconds and microseconds; `.000` and `Z` on whole minutes are aligned
  - Daily gold: 4 contributing slots (minute counts 2 + 2 = 4), total and flags correct; day with only withheld records gives NULL total, 0, 0, 0, true and flags; participants without steps.json and orphan folders give `steps_present` false / silver orphan rows only
  - Gold has 22 columns; 10-, 16- and 20-column legacy tables (the 20-column one with the steps columns in a different order) upgrade by name: existing rows keep values, added columns are NULL, unmatched legacy rows are retained
  - Idempotency and determinism: rerun gives identical ordered gold and a byte-identical report; one-record correction changes exactly that day (existing tests, plus rerun probe)
  - Failure atomicity: an injected `duckdb.Error` after the COPY loads gives `PipelineError`; silver_steps_minute, quality_rejects, quality_results and gold were identical afterwards, and no temp CSV or directory was left. A non-DuckDB exception (see Low 1) also left all tables unchanged and no temp files
  - Quoting in COPY: ids and units containing a comma, quote, newline or NUL round-trip intact and produce rejects
  - Scale (synthetic, single participant): 250,000 records 3.1 s; 1,000,000 records 12.3 s with a peak RSS of 2.3 GB (loading the JSON file included), so about 25 s and about 4.6 GB are expected for the 2 million records of the real data; the 64 GB host has ample headroom

Findings:
- Spec and intent adherence: steps layer is pure (no I/O, reuses `parse_local_timestamp`, never raises); `pipeline.py` owns reading, failure mapping to `PipelineError` before any table is touched, `STEPS_RULES` merging, `failed_count` taken from `StepsLayer.failed`, gold column list by name, and MERGE `UPDATE SET` generated from the same column tuple. The bulk loader writes to a `TemporaryDirectory` outside `data_dir`, and the context manager cleans it up on error. The INC-002/003 tables behave as before.
- [Low 1] A JSON string containing a lone surrogate escape (for example `"\ud800"` in `participant_id` or `_unit`) makes `_bulk_load` raise a raw `UnicodeEncodeError` instead of `PipelineError`. Tables and temp files are left clean. The previous `executemany` path fails on the same input (`RuntimeError`), so this is not a regression, and the spec's "never raises inside a record" is already unmet for such input. Optional follow-up: write the CSV with `errors="replace"` or catch `UnicodeEncodeError` and map it to `PipelineError`.
- [Low 2] Memory is about 2.3 GB per million records because every record is held as a dict (plus rows, rejects and raw JSON). It is acceptable for the stated 2 million-record target, but a leaner structure (`__slots__` or a dataclass) would halve it if the data grows.
- [Info] `steps.py` has a cosmetic style point: only one blank line between `StepsLayer` and the `_INT_MIN` constants, and the raw-signature expression `_ABSENT if value is _ABSENT or "steps" not in obj else ...` repeats the sentinel check (`value is _ABSENT` alone suffices). Neither affects behavior.
- /tmp/test_steps_rev2.py is a planner-era scratch file outside the repository; it is not part of the repo and was not touched.

Next action:
Orchestrator: route to human review and the final commit for INC-004. No implementer rework is required; Low 1 and Low 2 may be recorded as backlog items.
