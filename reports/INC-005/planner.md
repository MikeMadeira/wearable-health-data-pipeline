Status: READY
Increment: INC-005 (heart-rate silver and gold), kind: code
Inputs: intents/INC-005-heart-rate-silver-and-gold.md (unedited), .github/skills/sdd-tdd, .github/skills/agent-communication, INC-004 precedent (specs/INC-004.md, steps.py, tests/test_steps.py, reports/INC-004/planner.md), reports/INC-001/profiling-findings.md section 7. `data/` was not read.
Outputs:
- specs/INC-005.md (Status: Draft)
- src/wearable_pipeline/heart_rate.py (stub: `HEART_RATE_RULES`, `HeartRateDay`, `HeartRateLayer`, `build_heart_rate_layer` raising NotImplementedError; not wired into pipeline.py)
- tests/test_heart_rate.py (about 130 red tests, no `pytest.raises(NotImplementedError)`)
- Edited existing tests: test_walking_skeleton.py, test_silver_normalization.py, test_sleep.py, test_steps.py (22 -> 28 gold columns, 30 -> 41 rules; strengthened, none weakened)
Checks: `python -m pytest -q` -> **137 failed, 445 passed**. Of the 137 failures, 125 are in test_heart_rate.py (93 raise NotImplementedError from the stub; the rest fail on assertions about missing `hr_*` columns, `silver_heart_rate_minute` and the 11 rules). 4 tests in test_heart_rate.py already pass (negative or invariant checks). 12 failures are in existing tests that now assert 28 columns or 41 rules; they fail on assertions, as intended. Without test_heart_rate.py: 12 failed, 441 passed. No collection errors. grep finds no `raises(NotImplementedError`.
Findings: none blocking. The decisions below need human ratification.

## Decisions to ratify
1. Module and source name: `heart_rate.py`; rule ids use the `HR_` prefix, source `heart_rate`.
2. Silver `silver_heart_rate_minute` has 13 columns in this order: participant_id, local_date, minute_ts, raw_timestamp, heart_rate_bpm, heart_rate_bpm_normalized, is_orphan, is_out_of_window, is_duplicate_minute, is_out_of_range, is_stuck_sensor, is_timestamp_converted, quality_flags.
3. `raw_timestamp` (VARCHAR) holds the original text only for converted (Z or offset) records, else NULL.
4. Raw column `heart_rate_bpm` (INTEGER) keeps the integer whenever it is a valid int32, including out-of-range, stuck and duplicate members. It is NULL only for invalid JSON values (null, missing, string, float, bool, list/object, outside int32). It is never overwritten. The normalized column is NULL when withheld.
5. Gold appends six columns, so gold goes 22 -> 28: `hr_mean_bpm` DOUBLE, `hr_min_bpm` INTEGER, `hr_max_bpm` INTEGER, `hr_valid_minute_count` INTEGER, `hr_present` BOOLEAN, `hr_quality_flags` VARCHAR.
6. Mean rounding: none (plain float division, as with sleep percentages). Min and max are INTEGER.
7. Rules (11, so quality_results and the report go 30 -> 41 rows, ordered by rule_id then source). Format: id, handling, severity.
   - HR_ORPHAN_PARTICIPANT: exclude_from_output, medium
   - HR_PARTICIPANT_FOLDER_MISMATCH: exclude_from_output, medium
   - HR_TIMESTAMP_UNPARSEABLE: exclude_from_output, medium
   - HR_TIMESTAMP_OUT_OF_WINDOW: exclude_from_output, medium
   - HR_VALUE_INVALID: withhold_field, medium
   - HR_VALUE_OUT_OF_RANGE: withhold_field, high
   - HR_STUCK_SENSOR_RUN: withhold_field, medium
   - HR_TIMESTAMP_CONVERTED: convert, low (informational, writes no rejects)
   - HR_DUPLICATE_MINUTE_CONFLICT: withhold_field, high
   - HR_DUPLICATE_MINUTE_EXACT: flag, medium
   - HR_TIMESTAMP_NOT_MINUTE_ALIGNED: flag, medium
   - Exact reason texts: specs/INC-005.md section 6, the stub, and `HR_REASONS` in test_walking_skeleton.py.
8. Valid range is 40..200 inclusive. Value precedence: invalid gives INVALID only; valid but out of range gives OUT_OF_RANGE only; stuck and duplicate rules are evaluated afterwards.
9. Stuck run: at least 60 consecutive minute slots with the same clean in-range value (59 no, 60 yes, 61 yes). Detection is per participant id over the whole timeline (across midnight), including orphan and out-of-window records. A run is broken by a gap, an out-of-range or invalid slot, a conflicting duplicate slot, or a different value. An exact-duplicate group is one slot. Every record of every slot in the run is flagged and its normalized value withheld. The threshold 60 is provisional.
10. Duplicates: group by (participant, minute slot), excluding folder-mismatched records. One distinct raw value is an exact duplicate (counted once, value kept). Several distinct values are a conflict (normalized NULL). Converted and naive records in the same slot share a group.
11. Timestamps: any parsed timestamp with Z or an offset triggers HR_TIMESTAMP_CONVERTED, even `-05:00` or when the date does not change (my reading of the intent; please confirm). Non-zero seconds trigger HR_TIMESTAMP_NOT_MINUTE_ALIGNED and truncate to the minute.
12. Gold day flags are the union of rule ids over in-scope records on the converted local date. `hr_present` is true when at least one in-scope record exists. Orphan, mismatch, out-of-window and unparseable records never reach gold. A day with all values withheld has present=true, count 0 and NULL mean/min/max.
13. Malformed `heart_rate.json` (unreadable, not an array, or a non-object element) raises `PipelineError`, as for sleep and steps. The layer treats a non-object element as `{}`.
14. Upgrade of 10-, 16- and 22-column gold tables is by column name; old rows are retained.
15. The pipeline must take HR failed counts from `HeartRateLayer.failed`, not from reject counts. HR silver and rejects are bulk-loaded (the 60,000-record scale test must finish in under 25 s).

## Assumptions
- Raw JSON key names are `timestamp` and `heart_rate_bpm`.
- A participant without `heart_rate.json` gets `hr_present` false with no failure.

## Housekeeping
Nothing committed. intents/, data/, scripts/, .github/ and notebooks/ were not touched. The only source change is the stub. `specs/INC-004.md` was not edited; the new spec's header notes the amendment of earlier specs.

Next action: planning reviewer.
