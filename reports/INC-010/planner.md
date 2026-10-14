# Planner Report: INC-010

Status: READY
Inputs:
- intents/INC-010-documentation-and-query-output.md
- .github/skills/sdd-tdd/SKILL.md, .github/skills/agent-communication/SKILL.md
- specs/INC-009.md, reports/INC-009/implementer.md (verified commands, measured numbers)
- README.md, plans/INIT-001-wearable-health-pipeline/vertical-slices.md (INC-010 section), reports/INC-001/profiling-findings.md (A-02)
- src/wearable_pipeline/ (`cli.py`, `pipeline.py`, `cohort.py`, `weekly.py`, `incremental.py`); `data/` was not read, only synthetic fixtures.

Outputs:
- specs/INC-010.md (Draft; planning decisions P1-P12 in section 9)
- src/wearable_pipeline/query_runner.py (stub: `QueryRunnerError`, `QueryResult`, `collect_sql_files`, `run_queries`, `format_results`, `main`; function bodies only `raise NotImplementedError`)
- tests/test_analytical_queries.py (23 tests), tests/test_query_runner.py (22 tests), tests/test_readme_contract.py (26 tests, 2 of them parametrized cases)
- reports/INC-010/planner.md
- Not created, by design: `queries/` and any SQL file, README edits, implementation. The implementer creates `queries/chronotype_comparison.sql`, `queries/study_wide_metrics.sql`, the runner bodies and the README; the tests never create them.

Checks:
- `python -m pytest -q` (repo root, 310 s): `65 failed, 975 passed, 7 skipped`. Baseline before INC-010 was 0 failed, 969 passed, 7 skipped (INC-009 report), so all 65 failures are in the 71 new tests (6 of them pass).
  - 37 fail with `NotImplementedError` raised by the `query_runner` stub (17 in test_analytical_queries.py, 20 in test_query_runner.py).
  - 6 in test_analytical_queries.py fail by assertion because `queries/` and its SQL files do not exist yet (static SQL tests).
  - 1 in test_query_runner.py (`test_module_runs_as_python_dash_m_and_exit_codes_reach_the_shell`) fails because the subprocess exits non-zero with the stub's `NotImplementedError` traceback.
  - 21 in test_readme_contract.py fail because README content is absent (sections, two Mermaid blocks, commands, captured output, decisions, scaling, AI disclosure): 19 by assertion, 1 by `StopIteration` (no cloud Mermaid block yet), 1 by `assert 0 == 1` (no query-output section).
  - The 6 new tests that pass: runner defaults, preserved README sections, no unverified container command, cloud table rows, no built/tested cloud claim, Mermaid names. They guard existing facts and stay green.
- No `pytest.raises(NotImplementedError)` anywhere: PASS. No existing test changed or weakened; `pyproject.toml`, `uv.lock`, `README.md`, `data/` untouched: PASS.
- Fixture arithmetic (means, sample SD, valid days, participant-average study metrics, rounding case) cross-checked with DuckDB on the fixture: PASS. The tests were not run against any implementation.

Findings:
- Decisions P1-P12 need human ratification (full text in specs/INC-010.md section 9). Most important:
  1. P8, contradiction in the intent: it says `gold_participant_devices` was delivered in INC-002, but no code, schema or test defines it, and the INC-002 spec and the plan list it as out of scope. The tests require the README to say it is a client requirement the pipeline does not produce, and forbid "delivered in INC-002". The human must confirm that, amend the intent, or add a code increment.
  2. P2 wide chronotype layout (3 rows x 21 columns: `<metric>_mean/_participants/_stddev/_valid_days`), P3 sample SD (n-1, NULL for one participant), P4 `ROUND(...,2)` as the last step with A minus B from unrounded means, P6 sign (A minus B, NULL participants, SD and valid days on that row).
  3. P5 study-wide valid day = non-NULL daily value, no 14-day coverage rule and no cohort filter (differs from the cohort table by design); chronotype valid days are summed only over participants with a non-NULL mean.
  4. P7 runner: `SQL_PATH` (default `queries`) and `--database` (default `output/wearable.duckdb`); exit 1, `error:` on stderr, empty stdout, no partial results; usage errors exit 2.
  5. P10 the compare command comes from specs/INC-009.md section 4: the INC-009 evidence records that `compare` ran but not its exact command line. P12 the INC-009 evidence has no raw dataset size (Scaling uses the 19.4 MB database), and the heredoc slips the intent names (INC-006/INC-007 evidence reports) were confirmed only partly (`reports/INC-006/implementer.md`, `reports/INC-007/planner.md`).
- P11: the tests pin key lines of the preserved README sections, so correcting a fact there needs a test change.
- Process slip: one edit to `tests/test_analytical_queries.py` (rewriting a draft code comment) was made with a Python one-liner instead of the edit tool. The file content is what the repository shows now; disclosed as a tool-use deviation.

Next action:
Decisions P1-P12 RATIFIED 2026-10-14 by the human, unchanged (full text in specs/INC-010.md section 9). Human rulings: `gold_participant_devices` stays not implemented (P8; the README must not say it was delivered in INC-002, which supersedes the contrary line in the intent); the exact `compare` command is the form in specs/INC-009.md (P10); everything else accepted as planned. Planning review: APPROVED (reports/INC-010/planning-review.md). Route to the implementer after the planning commit; it creates `queries/*.sql`, the runner bodies and the README without editing tests.
