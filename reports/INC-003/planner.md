# Planner Report: INC-003

Status: READY
Inputs:
- intents/INC-003-sleep-silver-and-gold.md (controlling scope)
- reports/INC-001/profiling-findings.md (G1 decisions, S-01..S-07)
- specs/INC-002.md, specs/INC-002-01.md, src/wearable_pipeline/pipeline.py and normalize.py (read only)
- AGENTS.md, .github/skills/sdd-tdd/SKILL.md, .github/skills/agent-communication/SKILL.md
- scripts/orchestrate_increment.py (planning gate)

Outputs:
- specs/INC-003.md (new; full contract)
- src/wearable_pipeline/normalize.py (stub: `parse_local_timestamp`)
- src/wearable_pipeline/sleep.py (new: `SLEEP_RULES`, `SleepMetrics`, `SleepDay`, `SleepLayer`, stubs `compute_sleep_metrics`, `build_sleep_layer`)
- tests/test_sleep.py (new; synthetic fixtures)
- tests/test_walking_skeleton.py (edited only: rule list, reason map, report rows, gold column types; no assertion weakened)
- tests/test_silver_normalization.py (gold column count 10 -> 16)
- reports/INC-003/planner.md

Checks:
- `python -m pytest -q`: FAIL as intended (169 failed, 133 passed after the review repair). 125 failures are `NotImplementedError` from the new stubs; the rest are assertion, `KeyError` (missing gold columns) or `CatalogException` (missing sleep tables) failures from the pipeline lacking a sleep layer. No import or collection errors; `tests/test_orchestrator.py` passes.
- Harness red gate (non-zero exit and `NotImplementedError` in output): satisfied.
- `data/` read or modified while planning: NO. Dependency changes: NONE. Commits: NONE.

Findings:
- Rule count: 7 existing + 12 sleep = 19 rows in `quality_results` and the report.
- Planner decisions for the human to ratify:
  1. Gap/overlap between consecutive stages is its own rule `SLEEP_STAGE_GAP_OR_OVERLAP` (flag, medium); stages are never repaired.
  2. `checked_count` = sessions read; `failed_count` = sessions flagged (once per rule); one reject per flagged session and rule, `row_ref` `path:position`, `raw_values` the whole raw session.
  3. Timestamps: naive = UTC-05:00 local; `Z`/offset converted; space separator invalid.
  4. Duplicates: exact duplicates keep their metrics; two or more distinct metric tuples null all three; `sleep_session_count` counts all contributing sessions.
  5. The intent's "MERGE updates only sleep columns" was read as: unchanged inputs reproduce unchanged survey values; the MERGE still updates all non-key columns.
  6. Participant id falls back to the folder name when the record value is missing or blank; a mismatch is not checked.
  7. New `PipelineError` cases: unreadable/non-UTF-8/invalid JSON sleep file, non-array top level, non-object array element.
  8. Old databases: `ALTER TABLE ADD COLUMN IF NOT EXISTS` for the six sleep columns before the MERGE.
  9. S-04 uses the derived efficiency from valid stage durations (null when any stage is invalid); the sleep review notebook is a human step after implementation.

Review repair (reports/INC-003/planning-review.md, F1-F5):
- Spec section 5 now states which names/durations the layer passes to `compute_sleep_metrics` (missing or non-string name -> `""`; non-object element -> `("", None)`; `duration_min` 0 -> `None`); section 10 examples are split into metric-level and session-level.
- Tests added: layer-level metrics for zero-duration deep, unparseable stage times, invalid awake stage, non-object and non-string-named stages; pipeline-level gold for out-of-window, orphan, exact duplicate and conflicting days; cross-file duplicate; more timestamp offsets. Assertions tightened (exact reason sets incl. the three omitted rules, handling per rule, exact None negatives, NULL-date `is_out_of_window`, unchanged snapshot after a malformed file).

Second repair (review N1): the exact-set reasons test now includes `SLEEP_FINAL_STAGE_MISALIGNED`, which its `broken` fixture legitimately triggers (sleep_end 03:00 vs final stage start 03:30).

Third repair (review N2, N3): reject-tuple indexes in four `test_sleep.py` tests corrected to `(rule_id, path, position, raw_session)`; `test_gold_values_and_presence_flags` expected dicts extended with the six sleep columns (P001 sleep file is `[]`, so NULL/0/false).

Next action: route to planning reviewer.
