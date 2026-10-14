# Implementation Report: INC-010

Status: READY
Recovery: rerun after planner Repair 1 (the `assert_rows` int-type check). No implementation change was made in this rerun.
Inputs:
- intents/INC-010-documentation-and-query-output.md, specs/INC-010.md (Approved, P1-P12 ratified)
- tests/test_analytical_queries.py, tests/test_query_runner.py, tests/test_readme_contract.py
- reports/INC-010/planner.md, reports/INC-010/planning-review.md, reports/INC-009/implementer.md
- reports/INC-001 to INC-009 (read only to verify the disclosure of tool-use slips), .github/skills/agent-communication/SKILL.md, README.md, src/
- `data/` was not read and nothing was run on it.

Outputs:
- src/wearable_pipeline/query_runner.py (bodies only; contract unchanged; imports `argparse`, `re`, `sys`, `duckdb`, no new dependency)
- queries/chronotype_comparison.sql, queries/study_wide_metrics.sql (new)
- README.md (new sections, preserved sections kept, `queries/` line added to the project tree)
- reports/INC-010/implementer.md

Checks:
- `python -m pytest -q tests/test_analytical_queries.py tests/test_query_runner.py tests/test_readme_contract.py`: PASS, 71 passed, 0 failed (rerun after Repair 1; the earlier run was 4 failed, 67 passed).
- `python -m pytest -q` (full, 314 s): PASS, 1040 passed, 7 skipped, 0 failed (the earlier run was 4 failed, 1036 passed, 7 skipped).
- Smoke check of the runner on a synthetic gold DuckDB built in /tmp (not `data/`), then deleted: PASS. Output equals the hand-computed values of the spec (section 7) for all 21 chronotype columns and the study-wide rows. A missing database gives `error: database not found: ...` on stderr and exit 1.
- Value-level check of the 4 failing tests, with only the string-type assertion skipped: PASS (all expected values match).
- Allowed paths only (`src/` query_runner bodies, `queries/`, `README.md`, this report); no test, spec, intent, `pyproject.toml`, `uv.lock`, Dockerfile, compose or `data/` edited: PASS.
- Mermaid render check: NOT RUN (human/orchestrator).
- Real-data captured output: NOT RUN (see Next action).

Findings:
- RESOLVED (was BLOCKER, TESTS; fixed by planner Repair 1, no implementation change needed): `assert_rows` in tests/test_analytical_queries.py (line 160) asserts `type(got[key]) is int` for every expected value that is not None and not a float. The expected rows include the string labels `group_label` (`A`, `B`, `A_minus_B`) and `metric` (`steps_per_day`, ...), so a string can never pass: it fails with `assert <class 'str'> is int` on the first key. No implementation can satisfy it. Affected tests: `test_chronotype_comparison_exact_values`, `test_chronotype_query_needs_only_the_gold_cohort_table`, `test_study_wide_metrics_are_means_of_participant_averages`, `test_study_wide_query_needs_only_gold_clinical_daily`. The fix belongs to the planner: apply the integer-type check only to `int` expected values (for example `isinstance(value, int) and not isinstance(value, bool)`). The SQL returns BIGINT for counts and valid days, and a string for the label, as the spec requires. I did not edit the test.
- Runner: a read-only `duckdb.connect(read_only=True)`; database checked first (missing database is never created), then the SQL path; comment-only file is an error naming the file; all queries run before anything prints; DuckDB errors become `query failed in <file>: <message>`; no traceback. Exit codes 0, 1 and 2 (argparse) as in the spec.
- SQL: the chronotype query joins a two-row cohort list (`A`, `B`) to `gold_chronotype_cohort` so both rows exist for an empty table; sample standard deviation `stddev_samp`; valid days summed only over participants with a non-NULL mean; the A_minus_B row uses unrounded means and `ROUND(..., 2)` last. The study-wide query averages per participant first, then across participants.
- README: placeholder in "Analytical query output". The tests require the runner-format block, the real column names, the labels and a 16-hex `run_id=`, so a placeholder was the only way to satisfy them without inventing results. The block has `PENDING` in every cell, `run_id=0000000000000000` marked "placeholder", and an explicit "PLACEHOLDER" paragraph. It is NOT captured output. It must be replaced before the increment is accepted.
- README decisions that need human confirmation: (1) `gold_participant_devices` recorded as a requirement the pipeline does not produce (ruling P8; the intent line saying INC-002 delivered it is not followed). (2) The compare command is the `specs/INC-009.md` form with `-v $PWD/other:/other:ro`; the INC-009 report does not record the exact line. (3) Scaling uses 70-71 s, 4.9 GiB (5,252,268,032 bytes, may include page cache), 19.4 MB database, 1.1 s no-op and 3.1 s one-record incremental, all from the INC-009 evidence; no raw dataset size exists. (4) The Iceberg justification (snapshots, schema evolution) and the Assumptions labels (A-02, stuck-run 60 minutes, weekly 4-day and cohort 14-day thresholds assumed; timezone UTC-5, week rule, quality handling, incremental corrections implemented) are my wording from the code and the profiling report. The Assumptions lines follow the reviewer note (lines with "week" contain "implemented", lines with "coverage" contain "assumed").
- AI disclosure, verified against the reports. Recorded in reports: script instead of edit tools in INC-002 (`pipeline.py`), INC-002-01 (`normalize.py`), INC-004, INC-005, the INC-006 planner and the INC-007 planner; INC-006 `/tmp/dbg.py` written with a heredoc; INC-010 planner Python one-liner on a test comment. NOT verifiable in the reports, so the README attributes them to the author instead of asserting them: the heredoc appends of the real-data evidence to the INC-006 and INC-007 reports (the INC-006 report discloses only `/tmp/dbg.py`; the INC-007 report records a script edit, not a heredoc), the INC-008 evidence script rewritten through Python, and notebook edits made inline through Python. The author should confirm that wording.
- Implementer tool-use slips (this phase): two `python` commands were run without writing files (a harmless `print` heredoc with no file output, and read-only checks). All file contents were written with the create and edit tools. A smoke script ran inline via `python -c` and wrote only to /tmp (deleted).

## Real-data evidence (run by the orchestrator at the human's request, 2026-10-14)

- Pipeline: `docker compose run --rm pipeline` against the real `data/` (mounted read-only), output to `./output`. `run_id=a826c8397cf5ad8e` (same as the INC-008 and INC-009 evidence), about 71 s.
- Queries: `python -m wearable_pipeline.query_runner queries --database output/wearable.duckdb`, exit 0. A second invocation gave byte-identical output (deterministic).
- Independent check: a read-only DuckDB query on `gold_chronotype_cohort` reproduces the cohort rows (A 28 participants, mean 414.6, sample SD 13.33, 784 sleep valid days, HR mean 71.33, 781 HR valid days; B 22 participants, 407.67, 12.85, 616, 68.12, 615). `gold_clinical_daily` has 50 distinct participants. Valid days agree with the INC-008 evidence (sleep 28 days for every participant; steps and heart rate 27 days for 4 participants, so 1,396 of 1,400).
- The README placeholder (`PENDING` cells, all-zero `run_id`) was replaced with the unedited runner output; the README block is identical to the captured output. The three INC-010 test files still pass (71).
- `data/` SHA-256 (sorted per-file hashes) `3f16dc19…ab47`, unchanged before and after.
- Not done: the Mermaid render check and the human confirmation of the README wording remain with the human. The independent check used a `python -` heredoc for a read-only query (no files written); `output/wearable.duckdb` and `output/quality/` remain locally (Git-ignored).

Next action:
Orchestrator: proceed to review. Tool-use slips in this rerun: none (only read-only `cat`/`grep`/`git status` through bash and pytest runs; the report was edited with the edit tool; `data/` was not read). The orchestrator or human must still run, outside this phase (the README PENDING placeholder is kept and no output was invented): (1) the full pipeline on real data (`docker compose run --rm pipeline`), record the real `run_id`; (2) `python -m wearable_pipeline.query_runner queries --database output/wearable.duckdb` and paste its unedited output over the placeholder block in README "Analytical query output", with that `run_id`, and check the output equals a rerun; (3) render the two Mermaid diagrams in a preview or on GitHub; (4) confirm the disclosure wording and the compare command.
