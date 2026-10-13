# Planning Review Report: INC-005 (round 1)

Status: APPROVED
Verdict: PASS
Inputs:
- intents/INC-005-heart-rate-silver-and-gold.md (unedited)
- specs/INC-005.md (Status: Draft)
- reports/INC-005/planner.md
- src/wearable_pipeline/heart_rate.py (stub)
- tests/test_heart_rate.py, tests/test_walking_skeleton.py, tests/test_silver_normalization.py, tests/test_sleep.py, tests/test_steps.py (git diff)
- reports/INC-004/planning-reviewer.md (precedent)
- `data/` not read

Outputs:
- reports/INC-005/planning-reviewer.md

Checks:
- Red baseline (`python -m pytest -q`): PASS. 137 failed, 445 passed, as claimed. `tests/test_heart_rate.py` alone: 125 failed, 4 passed. The rest of the suite: 12 failed, 441 passed. The 12 are the existing tests that now assert 28 gold columns or 41 rules. The 4 passing heart-rate tests are the rules-table check, existing-columns-unchanged, run id from manifest, and raw data untouched; they are invariants, not stub-satisfied behaviour.
- Stub: PASS. `heart_rate.py` holds the 11 rules, the two frozen dataclasses and `build_heart_rate_layer`, whose only statement is `raise NotImplementedError`. It is not wired into `pipeline.py`.
- No `pytest.raises(NotImplementedError)` anywhere in `tests/`: PASS. grep finds no match.
- True TDD: PASS. Layer tests assert concrete return values (rows, `daily`, `failed`, `rejects`). Pipeline tests assert tables and report text that do not exist yet.
- No weakened tests: PASS. In the git diff, existing edits change 22 -> 28 columns and 30 -> 41 rules. They add the `HR_REASONS`/`HR_HANDLING` tables and the heart-rate rule rows (the report test, `expected_checked`, `RULES`), and the `hr_*` expected gold values and types. One test is renamed (`..._all_thirty_rules_...` -> `..._all_rules_...`). Nothing is loosened. The change also tightens the sleep and report checks.
- Scope: PASS. `git status` shows only the four edited test files plus `reports/INC-005/`, `specs/INC-005.md`, `heart_rate.py` and `tests/test_heart_rate.py`. There are no changes under `data/`, `scripts/`, `.github/`, `notebooks/` or `intents/`.
- Spec vs intent: PASS. These are covered:
  - 13-column silver table with natural key and no dropped rows;
  - raw value kept, with the normalized value NULL when withheld;
  - Z/offset conversion with the raw timestamp kept and the local date after conversion;
  - orphan, out-of-window and unparseable records kept in silver and excluded from gold;
  - M-03, M-04, M-05, M-07 (exact and conflict) and M-09 rules, with handling and severity as in the intent table;
  - five unobserved-defect rules, each with zero counts and exact reasons;
  - run breaks (gap, out-of-range, ambiguous duplicate), midnight spans and the 59/60 boundary;
  - gold columns, defaults and the all-withheld day;
  - upgrade of 10-, 16- and 22-column tables, idempotency, run id and the bulk-load scale constraint.
- Test strength (read against the intent's mutation list): PASS.
  - Threshold 59/60/61: `test_stuck_run_boundary...` is parametrised on 59, 60 and 61 and asserts flags, normalized values, rejects, counts and the gold day. A threshold of 59 or 61 would fail.
  - Run breaks: one parametrised test covers gap, out-of-range, invalid, conflict and a different value. Further tests cover exact duplicates counting once, adjacent runs not merging, run order independence, a naive plus converted run, an orphan run and a run across the window edge.
  - Midnight: unit test (30 minutes on each day) and pipeline test (both days flagged, 60 rejects).
  - Z conversion: five cases, including a date change, an offset of `-05:00` and `+05:30`. They check the local date, the raw text kept and the out-of-window flag after conversion.
  - Mean, min, max and counts: the worked example (70.0, min 60, max 80, 3 valid minutes) and the hand-computed day (87.5, 40, 200, 8 valid minutes). They use a raw 300 and a stuck run, so using the raw instead of the normalized value, or a wrong count, would fail. The mean is unrounded (87.5, 70.5 and a non-terminating mean in the scale test).
  - Flag versus reject: exact reason text, handling and `raw_values` are checked; converted writes no rejects; a record is counted once per rule.
  - Upgrade: three schemas (10/16/22 columns), checking the added column types, the retained survey values, the 22-column stale row being overwritten and rows outside the window being retained.
  - Idempotency: rerun, two fresh output directories, and a one-record correction that changes exactly one gold row.
- Reference implementation and mutation run: NOT RUN. I read the spec and tests for consistency; I did not build a reference implementation as in INC-004. See finding 4.

Findings (numbered, with severity):
1. LOW: Spec `Status: Draft`. The planner's 15 decisions need human ratification; they are listed in `planner.md` and agree with the intent and profiling section 7. No change needed before the baseline commit.
2. LOW (ratify): decision 11 treats any `Z` or explicit offset, even `-05:00` or a case with no date change, as `HR_TIMESTAMP_CONVERTED`. This is a reading of "a timestamp with `Z` or an offset" and the tests pin it (`-05:00` case). On the profiled data only `Z` occurs, so the counts match the intent (2,880).
3. LOW (ratify): decision 9 builds stuck runs from all records of a participant id, including orphan and out-of-window records, and flags the whole run. This follows "whole timeline". Orphan records can carry `HR_STUCK_SENSOR_RUN`. Tests pin it. Decision 12 adds the converted-date flag to a day's flags. Both are consistent with the intent.
4. LOW (process): no independent reference implementation was run against the tests this round. The mutation conclusions come from reading assertions. Gate 2 should run the real implementation against these tests and the real-data evidence.
5. INFO: the scale test has no rejected records, so the bulk-load `COPY` of heart-rate rejects is not timed. The same gap is on record for INC-004.
6. INFO: `planner.md` describes the baseline accurately; no stale content found.

Next action:
Orchestrator: Phase Gate 1 is passed (PASS, no blockers). Hand over to the human for ratification of the planner decisions (especially 11 and 9) and the baseline commit, then route to the implementer.
