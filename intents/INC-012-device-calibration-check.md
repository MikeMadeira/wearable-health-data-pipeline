# INC-012: Device Calibration Check

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-011
(`intents/INC-011-participant-devices.md`). This increment extends the participant-device layer
with the calibration date and a staleness data-quality finding.

## Goal

Analysts can see when each device was calibrated, and devices calibrated too long before the
study started are reported as a data-quality finding, because a stale calibration makes sensor
readings less trustworthy.

## Boundary

One `code` slice: the calibration-date derivation, the two new quality rules, the way the
date is exposed next to the device, and the tests, spec, README and `compare` changes needed
to keep documented facts true. No change to the daily, weekly or cohort outputs, no cloud
work, no new input files.

## Requirements

### Calibration date visibility

- Analysts can see each device's calibration date next to the device, taken from
  `calibration_date` in `device_metadata.csv`. Where it is stored is a planning decision.
- A blank or unparseable value is shown as NULL. The raw text is preserved.
- Any change to documented facts that follows from the chosen storage (for example the
  3-column decision in ADR-007) is made explicitly in the spec and README contract test,
  not by weakening them.

### Staleness rule

- `DEVICE_CALIBRATION_STALE`: the calibration date is strictly more than 30 days before the
  run's `study_start`. Exactly 30 days before is not stale. A calibration date on or after
  `study_start` is valid and not flagged.
- The reference date is the `study_start` of the run (default 2026-01-08), never a hard-coded
  date. The 30-day threshold is a fixed constant with no CLI flag.
- `DEVICE_CALIBRATION_INVALID`: the calibration value is blank or cannot be parsed as an
  ISO 8601 date. It is reported in the quality report with the raw text preserved.
- Both are participant-level findings with source `device_metadata`, recorded in the
  existing quality report with the raw value preserved. The rule count rises from 46 to 48.

### Observed vs assumed

- Observed on the real data: 50 devices, all 50 calibration dates parse, they range from
  2025-12-04 to 2026-01-07, all precede the default `study_start` 2026-01-08, and about 10 are
  more than 30 days before it (calibrated before 2025-12-09).
- Assumed, to be verified by the planner: the date format is uniform ISO 8601 and there are
  no duplicate device rows.

### Run behavior

- Full and incremental runs produce the same outputs and the same findings. A change to
  `device_metadata.csv` or to `study_start` updates the table and findings; an unchanged
  rerun changes nothing and does not duplicate rows.
- `compare` includes the calibration date output in its equality check.
- The rules follow the existing rule and quality-report conventions in
  `src/wearable_pipeline/pipeline.py`.

## Constraints

- No change to the keys or semantics of existing tables, to `run_id`, or to the existing CLI
  commands and flags. Any added column or table is justified in the spec.
- No new runtime dependency. `pyproject.toml` and `uv.lock` are unchanged.
- Existing tests are not weakened. Tests that hard-code the rule count (46) or the table
  column lists are updated to the new facts.
- No raw participant data, credentials or generated outputs are committed. `data/` is unchanged.
- Phase edits are confined to `README.md`, `queries/`, `src/`, `tests/`, `specs/` and
  `reports/INC-012/`, per the `code` kind rules.

## Planning Decisions

Left to the planner and ratified by the human at the planning gate:

- Where the calibration date is stored and shown next to the device (for example a
  `calibration_date` column in `gold_participant_devices`, which would change the 3-column
  decision in ADR-007, or silver only), and how it is derived.
- Whether a stale or invalid finding fails the run or only flags (ADR-005 makes device rules
  non-failing flags), and whether the participant stays in the gold table.
- How incremental mode tracks the change.
- How `study_start` reaches the rule so a changed window triggers the rebuild.
- The accepted date formats and the fixture design.

## Verification Expectations

- `python -m pytest -q` passes, including fixture tests with exact expected rows: the
  calibration date next to the device, grain and keys, the 30-day boundary (29, 30 and 31 days before `study_start`), a date on
  or after `study_start`, a blank and an unparseable date, a non-default `study_start`,
  idempotent reruns, and full versus incremental equality.
- The README contract test cross-checks the affected tables, columns and the rule list against
  the code.
- Real-data evidence, run by the human: a full run and a second identical run produce the same
  50 devices with their calibration dates, the stale count (about 10) and invalid count (0) are recorded with the `run_id`
  and command, and `compare` reports equality.
- `data/` is unchanged, and nothing generated is committed.

Human steps after implementation: review the evidence, decide on the commits, and merge.
