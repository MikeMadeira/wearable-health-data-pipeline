# Implementation Report: INC-012

Status: READY
Inputs:
- intents/INC-012-device-calibration-check.md
- specs/INC-012.md
- reports/INC-012/planner.md
- reports/INC-012/planning-review.md
- tests/test_device_calibration.py and the amended tests

Outputs:
- src/wearable_pipeline/devices.py (`parse_calibration_date`, `classify_calibration`, extended `DEVICE_COLUMNS` / `SILVER_COLUMNS`, required `study_start`, calibration findings in `build_device_layer`)
- src/wearable_pipeline/pipeline.py (`_run_full` passes `study_start` to `build_device_layer`)
- README.md (one Assumptions line for the calibration date and rules)
- reports/INC-012/implementation.md

Checks:
- `python -m pytest -q`: PASS (1175 passed, 7 skipped)
- `git status` / `git diff --stat -- data pyproject.toml uv.lock`: PASS (no changes to `data/`, `pyproject.toml`, `uv.lock`)
- Re-check against `specs/INC-012.md` and README Assumptions line: PASS

Findings:
- Repair attempt 1: the earlier TESTS blocker (final assertion `state(out) == state(fresh)` over whole-database state, which conflicts with the documented non-pruning full-run MERGE of daily/weekly/cohort gold rows) was fixed by the planner, who narrowed the assertion and spec section 4 to device state. No source change was needed; the implementation is unchanged from the previous attempt.
- Device tables, findings and rejects were already correct; daily, weekly and cohort behaviour is unchanged.
- Nothing committed; tests, specs, intents untouched by the implementer. `compare`, `incremental.py` and the CLI needed no change.

Next action:
Route to the implementation reviewer.
