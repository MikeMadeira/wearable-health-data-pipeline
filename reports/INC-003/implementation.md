# Implementation Report: INC-003

Status: READY

Inputs:
- intents/INC-003-sleep-silver-and-gold.md, specs/INC-003.md
- reports/INC-003/planner.md, reports/INC-003/planning-review.md (APPROVED)
- tests/test_sleep.py, tests/test_walking_skeleton.py, tests/test_silver_normalization.py (read only)

Outputs:
- src/wearable_pipeline/normalize.py: `parse_local_timestamp` (strict ASCII ISO 8601 regex; naive = UTC-05:00 local; `Z`/offset converted to UTC-05:00; invalid calendar/offset values return `None`).
- src/wearable_pipeline/sleep.py: `compute_sleep_metrics` and `build_sleep_layer` (pure, no I/O): sessions/stages silver rows, 12 rules, rejects, failed counter, per-day gold values (exact duplicates keep metrics, conflicting tuples null all three).
- src/wearable_pipeline/pipeline.py: reads manifest `sleep` files (new `PipelineError` for unreadable, non-UTF-8, invalid JSON, non-array, non-object element; raised before any table is touched); merges `SLEEP_RULES` into `RULES` (19 rows); writes `silver_sleep_sessions` and `silver_sleep_stages`; sleep rejects and quality rows; gold created with 16 columns, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for each sleep column before the MERGE, MERGE updates the sleep columns too.

Checks:
- `python -m pytest -q`: 302 passed, 0 failed.
- Tests, specs and data/ not edited or read; no dependency changes; no commits.

Findings:
- No test or spec defects found.
- Pending human step (real data, not run by the implementer): 1,400 gold rows; 1,400 silver sleep sessions and 7,000 stages; S-01 4, S-02 3, S-03 3, S-05 1,379 sessions; S-04 count recomputed and recorded; sleep efficiency range recorded; existing gold columns identical to the previous run; rerun identical; `data/` unchanged.

Next action: Human runs the pipeline on the real data and records the evidence above; then reviewer gate.

## Review repair (implementation-review.md, REJECTED / IMPLEMENTATION)

- `sleep.py`: a stage `duration_min` above 2147483647 is now an invalid duration (same field-level
  defect as other invalid durations: `SLEEP_STAGE_INVALID`, no new rule id). If a session's summed
  non-awake minutes exceed 2147483647, `compute_sleep_metrics` returns all-None (total and both
  percentages withheld) instead of letting the INTEGER gold column raise. `build_sleep_layer` uses
  `compute_sleep_metrics`, so day-level values are consistent.
- Minor: removed unused `position` parameter and trailing `None` placeholder from `_stage`;
  `pipeline.py` gold-stage insert wrapped under 100 columns, placeholders derived from row width.
- Verification: synthetic /tmp data (one stage of 3000000000; two stages of 2000000000) now
  completes; gold `total_sleep_min`, `deep_sleep_pct`, `sleep_efficiency_pct` are NULL.
  `python -m pytest -q`: 302 passed. Tests, specs, data/ untouched; no dependency changes; no commits.
- **Implementer decision needing human ratification:** the spec is silent on the INTEGER bound.
  The 2147483647 limit (invalid duration; withheld total/percentages on overflow) follows the repo's
  32-bit `parse_integer` convention. Ratify it, or have the planner specify a different rule.

Status: READY (for re-review).

