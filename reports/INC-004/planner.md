# INC-004 Planner Report

## Status
READY for planning review (round 4). Revision 3 below supersedes all earlier counts and rules (revisions 1 and 2).

## Revision 3 (intent amended: unit conversion, two activity levels)

### Outputs
- `specs/INC-004.md` rewritten (Status Draft, sections 1-12); stub `src/wearable_pipeline/steps.py` (11 rules, new `StepsDay`, `NotImplementedError` only).
- `tests/test_steps.py` rewritten (146 tests); shared tests updated: `test_walking_skeleton.py` (11-rule tables, gold dicts, type map), `test_silver_normalization.py` (gold 22 columns), `test_sleep.py` (30 `quality_results` rows).

### Checks
- `python -m pytest -q`: **162 failed, 286 passed** (448 collected, was 128/286); failures include `NotImplementedError`; no collection errors. Exit non-zero.
- `tests/test_orchestrator.py`: 29 passed. No `pytest.raises(NotImplementedError)` used. `data/` not read; no commits; intent and `reports/INC-001/profiling-findings.md` untouched.
- Throw-away reference implementation in /tmp (layer plus patched pipeline, CSV COPY bulk load): full suite 448/448 green after fixing one test ordering bug (alphabetical LOW before HIGH). Reference deleted.

### Decisions to ratify
1. Conversion: `_unit == 'steps_per_hour'` exactly, non-negative int divisible by 60 → `steps_per_minute = steps // 60` (0→0). Raw `steps` and `unit` text unchanged.
2. Precedence: invalid value → `STEPS_VALUE_INVALID` only; negative → `STEPS_NEGATIVE` only (with or without unit, never converted); then with `_unit`: convertible → `STEPS_UNIT_CONVERTED`, otherwise (other string, null, number, bool, non-divisible) → `STEPS_UNIT_UNSUPPORTED` (withhold, reject with raw values).
3. `STEPS_UNIT_CONVERTED`: handling `convert`, severity `low` (new vocabulary; my choice), informational: record flag and day `steps_quality_flags`, no `quality_rejects`, `failed_count` = converted records (120,963 on real data). Pipeline must take steps `failed` from the layer (as sleep does), not from rejects; `_persist`/report need no structural change. Converted records inside a conflict group keep the flag, value NULL.
4. Silver: new `steps_per_minute` INTEGER at position 5 (after `steps`); raw `steps` is kept for all withheld records and is NULL only when invalid. 11 silver columns.
5. Duplicates are per record: signature = normalized value + unit text (unit never inferred from participant); unconvertible/negative/invalid use raw-based distinct signatures. Hourly and unit-less records with equal normalized value are a CONFLICT. P005-style pattern = 3 conflict slots (hourly members `CONFLICT,CONVERTED`, unit-less `CONFLICT,ALIGN`).
6. Levels `at_rest_to_moderate` (0..30), `burst` (>30), NULL when normalized value NULL; 15 boundary dropped.
7. Gold 16→22 columns: `minutes_rest_to_moderate`, `minutes_burst` (sum = `steps_valid_minute_count`); `StepsDay` updated.
8. Table evolution by column name (staging insert and MERGE named); 10-, 16- and superseded 20-column gold tables are upgraded. The unreleased revision-2 23-column table is deliberately unsupported (ratify).
9. Reason texts for the two new rules are my wording.

### Assumptions
- Hourly values act as a rate (A-02 supported by multiples-of-60 evidence but unconfirmed by the provider).
- Pending human step: update the M-01 decision in `reports/INC-001/profiling-findings.md` ('convert', multiples-of-60 evidence, A-02 supported but unconfirmed). Not done by the planner.

### Human ratification (2026-10-13)
Ratified by the human, no change requested:
- Decision 3 and 9: `STEPS_UNIT_CONVERTED` (`convert`, `low`, "steps_per_hour value converted to steps per minute") and `STEPS_UNIT_UNSUPPORTED` (`withhold_field`, `high`, "steps _unit is not steps_per_hour or the hourly value is not divisible by 60"). `low` is new vocabulary, accepted because the rule is informational; labelling its count "failed" follows the sleep convention and stays.
- Decision 8: the unreleased 23-column gold table is not upgraded. Only 10-, 16- and 20-column tables are; a 23-column table would fail with a missing-column error, and the remedy is to rebuild the output directory.
- Decision 4: raw `steps` stays populated for withheld records and is NULL only when the JSON value is not a valid int32 integer. `steps_per_minute` alone feeds totals, valid-minute counts and activity levels.
Also ratified by the human (2026-10-13), no change requested:
- Decision 1: conversion only for `_unit` exactly `steps_per_hour` and a non-negative integer divisible by 60; `steps_per_minute = steps // 60`; 0 converts to 0.
- Decision 2: rule precedence (invalid, then negative, then unit handling).
- Decision 5: per-record duplicate semantics (normalized value plus unit text); the P005-style pattern gives 3 conflict slots.
- Decision 6: levels `at_rest_to_moderate` (0 to 30) and `burst` (above 30); the 15 boundary is dropped.
- Decision 7: gold grows from 16 to 22 columns, with `minutes_rest_to_moderate` and `minutes_burst` summing to `steps_valid_minute_count`.
All nine planner decisions are now ratified.

### Revision 3.1 (planning-reviewer round 4 finding)
The non-divisible hourly parametrisation now includes 15, 30, 45, 90 and 1830, so a wrong divisor (30, 15, 45, 90) no longer survives. Baseline: 167 failed, 286 passed; orchestrator tests 29 passed. No spec change.

### Next action
Planning reviewer (round 4); asked to build a pipeline-level reference implementation as well.

## Revision 2 and original (superseded where counts differ)

## Inputs
- `intents/INC-004-steps-silver-and-gold.md`
- `specs/INC-003.md`, `reports/INC-003/planner.md`, `reports/INC-001/profiling-findings.md`
- `src/wearable_pipeline/{pipeline,sleep,normalize}.py`

## Outputs
- `specs/INC-004.md`
- `src/wearable_pipeline/steps.py` (stub, `NotImplementedError` only)
- `tests/test_steps.py` (92 tests)
- Edited: `tests/test_walking_skeleton.py` (steps rules, silver snapshot, 4 gold columns, `checked` for steps), `tests/test_silver_normalization.py` (gold columns 16→20), `tests/test_sleep.py` (rule rows 19→30)

## Checks
- `python -m pytest -q`: 108 failed, 286 passed; failures include `NotImplementedError`; no collection errors.
- `tests/test_orchestrator.py`: 29 passed.
- `data/` not read; synthetic fixtures only. No dependency changes. No commits.

## Findings / decisions to ratify
1. 11 `STEPS_*` rules (reasons in spec §6); total rule rows 19→30.
2. `checked_count` = records read; `failed_count` = flagged records per rule; one reject per record and rule.
3. Duplicates: grouped by (participant, minute slot); signature = raw `steps` + `_unit` JSON text. Exact group counted once and kept; conflict group withheld. Timestamps keep seconds; the slot is used only for grouping.
4. `_unit` is present when the key exists (any value); stored as text, NULL if absent.
5. Folder-mismatched records are excluded and do not join duplicate groups.
6. `STEPS_BURST` (>30, no `_unit`) never withholds; it also fires for duplicate members.
7. `steps` is valid only as a JSON int in the int32 range; `total_steps` is BIGINT.
8. Bulk loading is required: measured DuckDB `executemany` at about 0.77 ms/row against about 2M real records. A 60k-record timing test (<25 s) enforces it.
9. New `PipelineError` cases for malformed steps files, same contract as sleep.
10. Old 10- and 16-column gold tables are upgraded in place.
11. The review notebook is a human step.

## Next action
Planning reviewer (re-run).

## Revision 2 (intent amended: activity levels)

Status: READY for planning review (round 3). Supersedes the counts and decisions above where they differ.

Amended intent: `STEPS_BURST` removed; silver gains `activity_level`; gold gains `minutes_at_rest`, `minutes_moderate`, `minutes_burst`.

Changes:
- Spec `specs/INC-004.md`: sections 1, 4, new 4a, 5, 6, 8, 9, 10, 11, 12 and header updated.
- Stub `steps.py`: `STEPS_RULES` 11 -> 10 rules; `StepsDay` fields now `total_steps, steps_valid_minute_count, minutes_at_rest, minutes_moderate, minutes_burst, steps_quality_flags`. `build_steps_layer` still only raises `NotImplementedError`.
- Tests: 92 -> 112 in `tests/test_steps.py` (414 collected overall, was 394). Every fixture that used `STEPS_BURST` was re-traced (flag strings, counts, reject lists, gold tuples, worked example now 6 rejects, daily 46/5/4/0/1 with flags `CONFLICT,EXACT,NEGATIVE,UNIT`). New tests: boundaries 0, 1, 14, 15, 16, 17, 29, 30, 31, 32, 500, 1,000,000, 2^31-1; boundary day with counts summing to the valid minutes; burst raises no flag/reject/failed count and burst-only days have NULL flags (layer and pipeline); burst in an exact duplicate group keeps its level; conflicting burst duplicates give NULL level and only the conflict flag; `_unit` record > 30 has NULL level and counts in no bucket; NULL level for negative/invalid/`_unit`/conflict; counts sum to `steps_valid_minute_count` in gold; silver `activity_level` column type and position; upgrade tests for 10-, 16- and (new) 20-column gold tables; scale test now asserts `activity_level` is loaded (60,000 `burst`), no steps rejects, `minutes_burst` 1440.
- Edited existing tests: `test_walking_skeleton.py` (no `STEPS_BURST` in reasons/handling/rule table; gold dicts and type map with 23 columns), `test_sleep.py` (rule rows 30 -> 29), `test_silver_normalization.py` (gold columns 20 -> 23).

Design choices (stated in the spec):
- Silver `activity_level` is column 5, directly after `steps`; row tuple index 4 (flags move to index 9, `is_duplicate_minute` to 8). Silver has 10 columns.
- Gold column order: `total_steps, steps_valid_minute_count, minutes_at_rest, minutes_moderate, minutes_burst, steps_present, steps_quality_flags` (gold 16 -> 23).
- Records keep their level even when orphan, mismatched, out of window, unparseable or non-minute-aligned, as long as `steps` is non-NULL; gold only counts in-scope contributing minutes.

Checks:
- `python -m pytest -q`: 128 failed, 286 passed (red; failures show `NotImplementedError` or pipeline assertion failures; no collection errors; non-zero exit). Was 108 failed, 286 passed; the 20 extra failures are the new tests.
- `tests/test_orchestrator.py`: 29 passed.
- Layer-level tests (80 selected, all non-pipeline) were also run against a throw-away reference implementation under `/tmp` (deleted): 80 passed, so layer fixtures and spec agree. Pipeline-level tests were hand-traced only (no pipeline reference built this round).
- No `pytest.raises(NotImplementedError)`; `data/` not read; intent not edited; nothing committed.

Decisions to ratify:
1. Burst is no longer a rule (10 steps rules; 29 `quality_results` rows).
2. The 15 boundary is analyst-chosen (dictionary gives only 0-30 as "at rest through moderate"); recorded in spec 4a.
3. Gold column names `minutes_at_rest`, `minutes_moderate`, `minutes_burst`, and the column/field orders above.
4. A 20-column gold table from the superseded plan is also upgraded (adds the 3 columns).

Assumptions: `activity_level` strings are exactly `at_rest`, `moderate`, `burst`. Old `silver_steps_minute` tables are not migrated (rebuilt in the full rebuild; the table was never released).

Next action: planning reviewer (round 3).

## Revision 1 (planning-reviewer findings)
1. `test_report_lists_run_id_and_one_row_per_rule` now lists the 11 `STEPS_*` rows (source `steps`, checked 1, failed 0) in rule_id order between SLEEP and SURVEY.
2. `test_unit_with_invalid_or_negative_value_carries_both_rules` now expects `UNIT,INVALID` (alphabetical).
3. The frozen-dataclass test uses `dataclasses.FrozenInstanceError`.
No spec change. Baseline: 108 failed, 286 passed; orchestrator tests 29 passed.
