# Planning Review Report: INC-010

Status: APPROVED
Inputs:
- intents/INC-010-documentation-and-query-output.md
- specs/INC-010.md
- reports/INC-010/planner.md
- src/wearable_pipeline/query_runner.py (stub)
- tests/test_analytical_queries.py, tests/test_query_runner.py, tests/test_readme_contract.py
- .github/skills/agent-communication/SKILL.md, .github/skills/sdd-tdd/SKILL.md
- src/wearable_pipeline/cohort.py, pipeline.py (gold schemas); reports/INC-009/implementer.md, reports/INC-006/implementer.md, reports/INC-007/planner.md, reports/INC-002/*, specs/INC-002.md, plans/INIT-001-wearable-health-pipeline/vertical-slices.md
- `data/` was not read.

Outputs:
- reports/INC-010/planning-review.md

Checks:
- `python -m pytest -q tests/test_analytical_queries.py tests/test_query_runner.py tests/test_readme_contract.py`: PASS as a red baseline (65 failed, 6 passed, 1.8 s). This matches the planner's 65 failed. Full suite not rerun; the planner's 65 failed / 975 passed / 7 skipped is consistent (969 + 6 new passing; 0 existing failures).
- No `pytest.raises(NotImplementedError)` anywhere in the three files (grep and read): PASS.
- Red causes are genuine: 37 `NotImplementedError` raised by the stub, the rest are assertions on absent `queries/` or README content. Tests call the stub and assert future return values; the 6 green tests only guard existing facts.
- Independent recomputation of the fixtures (Python `statistics`, and DuckDB for the rounding case): PASS. All 5 metrics for cohorts A and B (mean, n, sample SD, valid days), all 5 A-minus-B differences, and the study-wide results (steps 3/5/5333.33, sleep 2/3/525.0, hr 2/3/72.5) match `CHRONO_EXPECTED` and `STUDY_EXPECTED`. The rounding trap is real: DuckDB gives 10.01 and 10.0, while the unrounded difference is 0.002 (rounds to 0.0).
- Fixture types match code: `COHORT_COLUMNS` and `ALL_GOLD_COLUMNS` are imported from `src/`, with DOUBLE means and INTEGER valid days. The int/float assertions are satisfiable.
- `git status` / `git diff`: only untracked new files (`reports/INC-010/`, `specs/INC-010.md`, `query_runner.py`, the three test files). No tracked file is modified; `README.md`, `pyproject.toml`, `uv.lock`, existing tests and specs are untouched; `queries/` does not exist: PASS.
- Spec alignment with every intent requirement and constraint: PASS, except the P8 contradiction below, which the spec flags itself.
- P8 verification: CONFIRMED (see F1).
- P10 / P12 evidence check against reports: PARTLY CONFIRMED (see F5, F6).

Findings:
- F1 (HUMAN DECISION REQUIRED, not blocking Gate 1): the P8 contradiction is real. The intent says `gold_participant_devices` "was delivered in INC-002".
  - No code, schema or test in `src/` or `tests/` defines it; only the README, specs, plans and reports mention it.
  - `specs/INC-002.md:252` and the plan (`vertical-slices.md` A3 and the deferred list) put it out of scope.
  - `reports/INC-002/planner.md` and `planning-review.md` confirm it stayed out of scope.
  - INC-008's intent line 84 ("already produced since INC-002") is also wrong.
  - The spec and tests require the README to record it as a client requirement the pipeline does not produce, and forbid "delivered in INC-002". The spec correctly diverges from the false intent line rather than forcing a false README claim.
  - The human must ratify one option at the gate: keep P8 as written, amend the intent, or add a code increment that creates the table. Under the third option the tests would need rework.
- F2 (Low): the spec has Status "Draft" with P1-P12 awaiting ratification. This is correct for the gate. The human must ratify P1-P12 before the baseline commit.
- F3 (Medium, informational): the Assumptions test requires every line containing "week" to also contain "implemented", and every line containing "coverage" to contain "assumed".
  - A natural line such as "weekly coverage threshold: assumed" would fail.
  - It is satisfiable (for example, "assumed; implemented as a provisional constant"), and the intent does require both labels, so it is not blocking. The implementer should be told.
- F4 (Low, over-pinning): wording pins are modest and match the intent. The tests check keywords (`Spark`, `rejected` >= 6, `deviation`, `heredoc`, `INC-006`, `INC-007`, `not recorded`). The preserved README sections are pinned by key lines (P11), so a factual correction there needs a test change. Accepted as disclosed.
- F5 (Medium, human confirmation): the intent claims heredoc appends to the INC-006 and INC-007 evidence reports that "were not disclosed in those reports". The reports show something different.
  - `reports/INC-006/implementer.md:54` does disclose a heredoc, for a `/tmp/dbg.py` file.
  - `reports/INC-007/planner.md:85` records a script-edit slip, not a heredoc append.
  - The tests only require the tokens `heredoc`, `INC-006`, `INC-007`, so a truthful README is possible. The human should confirm the exact wording.
- F6 (Low): the INC-009 evidence supports the run and incremental commands, the HOST_UID/HOST_GID prefix, the test command, and the `mkdir -p output` note (F1 of that report). It also supports 70-71 s, 5,252,268,032 bytes (about 4.9 GiB) and the 19.4 MB database. The exact `compare` command line is not in the INC-009 report; the spec takes it from `specs/INC-009.md` section 4 (`compare /output /other`), and the test regex allows optional `-v` mounts. The human should confirm it. There is no raw dataset size, so Scaling uses the database size (P12), which is honest.
- F7 (Low, test weakness): in `test_readme_runs_no_unverified_container_command`, the env-prefix strip regex leaves `-u) HOST_GID=...` for the HOST_UID line. That line is therefore never checked against the allowed patterns, although an exact-match test covers it. It is harmless and not a blocker.
- F8 (Low, process): the planner disclosed editing `tests/test_analytical_queries.py` once with a Python one-liner instead of the edit tool. This violates the file-change rules. The impact is limited to a rewritten code comment; the file content is reviewed here and is clean. The human may want it listed among the deviations in the AI disclosure; the tests require only the word "deviation".
- F9 (Low, minor spec gaps, accepted):
  - The precedence is unspecified when the database and the SQL path are both missing.
  - Reading a non-DuckDB file is not specified.
  - The directory-case failure when a directory is named `*.sql` is not specified.
  The runner contract otherwise covers exit codes (0, 1, 2), stderr format, no partial output, read-only behavior, determinism and sorting.
- Contract completeness (PASS): grain (3 rows A / B / A_minus_B; 3 metric rows), 21 and 4 columns in order, sample SD (n-1, NULL under 2), `ROUND(...,2)` last, difference from unrounded means, sign (A minus B), NULL and empty-table behavior (0 counts, NULL mean), cohort-NULL exclusion, valid-day definition (consistent between the two queries by P5), ORDER BY, and gold-only reads (static and behavioral).
- Test quality (PASS): tests are behavioral against real DuckDB fixtures. They are not tautological: pooled-vs-participant-mean, rounded-vs-unrounded difference, NULL participants, missing cohort and insertion-order independence each have a distinguishing fixture. Nothing makes implementation impossible, and nothing forces false README claims. The AI-disclosure model-name ban and the proposed-label checks are consistent with the intent.

Next action:
Route to the human for the Phase Gate 1 review. The human must (1) decide F1/P8, (2) ratify P1-P12, and (3) confirm the compare command (F6) and the heredoc wording (F5). If the human accepts the planning as is, commit the baseline and route to the implementer, passing along F3 so the Assumptions lines satisfy the week/coverage keyword constraints. If the human changes P8 or any other decision, route a `SPECIFICATION` or `TESTS` correction to the planner.
