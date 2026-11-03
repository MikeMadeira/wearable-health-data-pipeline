# Planning Report: INC-012

Status: READY
Inputs:
- intents/INC-012-device-calibration-check.md
- specs/INC-011.md, reports/INC-011-participant-devices/planner.md, tests/test_devices.py, tests/test_readme_contract.py
- src/wearable_pipeline/devices.py, pipeline.py, incremental.py, compare.py
- data/health_summaries/device_metadata.csv (read-only profiling)

Outputs:
- specs/INC-012.md
- src/wearable_pipeline/devices.py (stubs `parse_calibration_date`, `classify_calibration`; constants and two `DEVICE_RULES` entries; `build_device_layer` gains optional `study_start`)
- tests/test_device_calibration.py (new, 71 red tests)
- tests/test_devices.py, tests/test_walking_skeleton.py, tests/test_sleep.py, tests/test_heart_rate.py, tests/test_readme_contract.py (amended: 46 -> 48, 4-column gold, two new rules)
- reports/INC-012/planner.md

Checks:
- `python -m pytest -q` (red baseline): FAIL as intended, 93 failed, 1082 passed, 7 skipped
- Failure breakdown: 71 in test_device_calibration.py, 18 in test_devices.py, 2 in test_walking_skeleton.py, 2 in test_readme_contract.py; none elsewhere
- Red causes: 45 `NotImplementedError` raised by the two stubs; the rest are assertion mismatches (gold/silver tuples and DDL still 3 and 8 columns, calibration findings not produced, README line absent). No test uses `pytest.raises(NotImplementedError)`
- Rule count 48 assertions in test_sleep.py and test_heart_rate.py already pass because the stub registers the two rules
- Profiling of the real file: PASS (50 devices, all ISO `YYYY-MM-DD`, 2025-12-04..2026-01-07, 10 stale, exactly 1 at 30 days, 0 invalid, no duplicates)

Findings:
- Observed vs assumed is in spec section 1: the format-uniform and no-duplicate assumptions of the intent hold on the real data; "on or after study_start" has zero real examples, covered by fixtures only.
- Decisions (spec section 10, need human ratification): P1 `calibration_date DATE` becomes the 4th gold column (supersedes ADR-007's 3 columns) plus `calibration_date_raw`/`calibration_date` in silver; P2 findings only flag, participant stays (ADR-005); P3 incremental needs no new state (`shared_input_changed` for metadata, existing `window_changed` for `study_start`); P4 strict ISO `YYYY-MM-DD`, ASCII digits, stripped, calendar-valid, never guess formats; P5 `study_start` is an explicit `date` argument of `build_device_layer` passed by `_run_full`; P6 `compare` unchanged, tests prove coverage; P7 no metadata row counts as INVALID, stale and invalid are exclusive, severity medium; P8 fixtures reuse `mrow`/`prow` with START 2026-01-08 and LATER_START 2026-02-01.
- Deliberate stub state: `DEVICE_COLUMNS`, `SILVER_COLUMNS` and the `build_device_layer` body are still the INC-011 versions and `study_start` is optional/ignored, so unrelated suites stay green (a required argument gave 469 `TypeError`s in a first attempt). The implementer extends the columns, makes `study_start` required, and wires `pipeline._run_full` and README.
- Default-fixture `device_metadata.csv` has no `calibration_date` column, so both default participants are INVALID: `test_walking_skeleton` expects failed 2 and rejects 5 (was 3).
- Tooling deviation: the initial stub edit to `devices.py` was applied with a Python script instead of the edit tool; later changes used the edit tool. Content was reviewed afterwards. The full pytest run takes about 6 minutes on this machine.
- Nothing committed; `data/`, `intents/`, `pyproject.toml`, `uv.lock` untouched.

Next action:
Route to the planner reviewer (`reports/INC-012/planning-review.md`), then the human planning gate for decisions P1-P10 and the baseline commit.

## Repair note (attempt 1, Blocker-Type: TESTS)
- `test_rerun_with_a_changed_study_start_replaces_the_findings_in_the_same_directory` now compares `device_state(out) == device_state(fresh)` (device tables, DEVICE_* rejects and DEVICE_* report rows) instead of full `state`, because full runs merge gold rows without pruning (pinned by `test_incremental.py::test_participant_removed_prunes_its_gold_rows_unlike_full_mode_merge`).
- Spec section 4 idempotency sentence narrowed the same way; non-device gold tables are out of scope per the intent. No other assertion, src/, README, data/ or intents/ changed. Status stays READY.
