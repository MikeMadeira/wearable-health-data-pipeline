# INC-011: Participant-Device Gold Table

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-010
(`intents/INC-010-documentation-and-query-output.md`). This increment reverses the
INIT-001 scope decision that left `gold_participant_devices` out: the client brief lists
it under "also produce", and the human has now decided to deliver it.

## Goal

The pipeline produces `gold_participant_devices`, one row per enrolled participant linking
`participant_id` to `device_id`, as the client brief requires
(`requirements/business_pipeline_requirements.md`). The table is built from the same shared,
validated silver layer as the other gold outputs, is rebuilt identically by full and
incremental runs, and is reproducible and idempotent.

## Boundary

One `code` slice: the silver and gold participant-device logic, its tests and spec, and the
README and `compare` changes needed to keep documented facts true. No new analytical query,
no cloud work, no change to the daily clinical, weekly or cohort outputs.

## Requirements

### Source and grain

- The grain is one row per `participant_id`. `participants.csv` is the authority for the
  participant-to-device link; `device_metadata.csv` is used only to cross-check it.
- Columns: `participant_id`, `device_id`, and `firmware_version`. `firmware_version` comes from
  `device_metadata.csv`; where that column is blank the version is parsed from `device_label`
  (the data dictionary says the label always carries the true version). Raw values are kept
  in silver, the parsed value is derived and marked as such.
- Only participants that pass the existing enrolment checks appear in the table.

### Data quality

- Profile the real data before asserting any rule. The planner records what is observed
  (50 participants, one device each, no ids missing from either file, 15 blank firmware values
  recoverable from the label) and what is assumed.
- A `device_id` that disagrees between `participants.csv` and `device_metadata.csv`, a device
  shared by two participants, or a `device_id` that does not match `FBT-NNNN-XXXXXXXX` is a
  flagged quality finding in the existing quality report, with the raw value preserved.
  Whether any of these fails the run is a planning decision.
- A blank firmware value that cannot be recovered from the label stays NULL and is counted in
  the quality report; it never gets a default.

### Run behavior

- Full and incremental runs produce the same table. A change to either input file updates the
  table; an unchanged rerun changes nothing and does not duplicate rows.
- `compare` includes the new table in its equality check between runs.
- The table follows the existing gold conventions (naming, MERGE or replace strategy, run
  state), reusing the helpers in `src/wearable_pipeline/pipeline.py` rather than a new pattern.

### Documentation facts that change

- The README stops saying the pipeline does not produce `gold_participant_devices`. The local
  architecture diagram, the table list and the assumptions list name the new table and
  its source; the cloud mapping gains no new component.
- `tests/test_readme_contract.py` currently asserts the table is *not* produced. That test is
  rewritten to assert the opposite and to cross-check the table and columns against the code;
  it is not deleted or weakened.

## Constraints

- No change to the schemas, keys or semantics of existing tables, to `run_id`, or to the
  existing CLI commands and flags.
- No new runtime dependency. `pyproject.toml` and `uv.lock` are unchanged.
- No raw participant data, credentials or generated outputs are committed. `data/` is unchanged.
- Phase edits are confined to `README.md`, `queries/`, `src/`, `tests/`, `specs/` and
  `reports/INC-011/`, per the `code` kind rules.

## Planning Decisions

Left to the planner and ratified by the human at the planning gate:

- Whether a mismatch, a shared device or a malformed id fails the run or only flags.
- The exact firmware regex, and whether the parsed value is stored next to the raw one or
  replaces it in gold.
- Where the derivation lives (silver table versus gold query) and how incremental mode tracks
  the two input files.
- The fixture design for the tests and the rewritten README contract test.

## Verification Expectations

- `python -m pytest -q` passes, including fixture tests with exact expected rows: grain and
  keys, the join between the two files, blank-firmware recovery from the label, each flagged
  case, idempotent reruns, and full versus incremental equality.
- The README contract test cross-checks the new table and its columns against the code.
- Real-data evidence, run by the human: a full run and a second identical run produce the same
  50 rows, the NULL and recovered firmware counts are recorded with the `run_id` and command,
  and `compare` reports equality.
- `data/` is unchanged, and nothing generated is committed.

Human steps after implementation: review the evidence, decide on the commits, and merge.
