# INC-002-01: Silver Normalization

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-002
(`intents/INC-002-walking-skeleton.md`) and amends its silver contract before INC-003 to
INC-005 build on it.

## Goal

`silver_participants` and `silver_wellness_survey` hold typed, normalized values only. The
original text of every value stays recoverable from the immutable raw files and, for flagged
rows, from `quality_rejects`. Survey dates are normalized where the repair is unambiguous,
and the reason a date is unusable is reported precisely.

## Boundary

One code slice that changes the silver and quality contract only. `gold_clinical_daily`,
the CLI and the manifest are unchanged.

## Requirements

### Silver columns

- `silver_wellness_survey` no longer has `date_raw`, `fatigue_score_raw`, `stress_score_raw`,
  `readiness_score_raw` or `sleep_quality_score_raw`. It keeps the normalized `local_date`,
  the four typed scores, the orphan, duplicate and out-of-window flags, and `quality_flags`.
- `silver_participants` stores `age`, `height_cm` and `max_heart_rate` as integers. Other
  participant columns keep their current types. The raw text of these three fields is not
  kept in silver.
- Neither silver table has `source_row_number`. Rows are identified by their natural key
  (`participant_id`, and `local_date` for the survey). Original values for any flagged row
  are in `quality_rejects`, whose `row_ref` is unchanged.
- No row is dropped from either silver table.

### Typed conversion failures

- A participant value that cannot be converted to an integer becomes NULL in silver. The row
  stays, only that field is withheld, and the other fields remain usable.
- A quality rule reports these failures with checked and failed counts. Affected rows are
  written to `quality_rejects` with their raw values. The rule id and exact reason text are
  chosen at planning. Like the existing rules, it is reported with zero counts when nothing
  fails.

### Date handling

- Surrounding whitespace is trimmed before a survey date is parsed. A value such as
  `" 2026-01-08 "` normalizes to the date 2026-01-08.
- No other layout is converted. A date that is still not a valid `YYYY-MM-DD` calendar date
  after trimming becomes NULL.
- The current out-of-window rule is split into two rules with distinct ids and reasons: one
  for a date that cannot be parsed, and one for a valid date outside the study window. Both
  keep the `exclude_from_output` handling and both write to `quality_rejects`.

### Review notebook

- `notebooks/INC-002-review.ipynb` is updated for the new contract and committed with this
  increment, with its outputs cleared, as in INC-001.
- Its checks follow the new schemas: no raw-column references, the silver-to-gold trace uses
  the normalized values, and the expected rule and row counts match the new rule list.
- This is a human step done after implementation. The orchestrator's implementation phase
  may change only `reports/` and `src/`, so it would reject a `notebooks/` edit, and the
  notebook can only be verified against the new pipeline's output.

## Constraints

- `gold_clinical_daily` keeps its key, columns, values and MERGE behavior for valid inputs.
  On the profiled data its content is identical to the INC-002 result.
- No new dependency, no change to the CLI, `raw_manifest`, the run id, the report format
  beyond the rule rows, the harness (`scripts/`, `.github/`), or `data/`.
- INC-002 tests and spec text that assert the removed columns are updated by this increment.
  No other test is weakened.
- Out of scope:
  - an incremental or persistent manifest, and per-row or per-file lineage columns;
  - date layouts other than trimmed ISO `YYYY-MM-DD`;
  - sleep, steps and heart-rate silver, device metadata, weekly and cohort outputs;
  - participant domain validation beyond integer conversion.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- the removed columns are absent and the participant integers are typed;
- a non-integer participant value gives NULL for that field only, keeps the row, is flagged,
  appears in `quality_rejects` with its raw value, and is counted by its rule;
- a survey date with surrounding whitespace is parsed;
- an unparseable date and a valid out-of-window date are reported by different rules, both
  excluded from gold and written to rejects;
- gold content for valid inputs is unchanged;
- rerun idempotency, a deterministic run id and report, and unchanged raw files.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-002-01/`. The notebook update is made by the human outside those phases.

Real-data evidence, run by the human and recorded in the implementation report:

- 1,400 gold rows and 1,414 silver survey rows, with the 14 P999 orphans still flagged;
- every new rule has a zero failed count and the orphan rule still fails 14;
- `gold_clinical_daily` is identical to the INC-002 run on the same data;
- a second run gives identical tables and report, and `data/` SHA-256 is unchanged;
- the updated review notebook runs top to bottom against the new run with every check
  PASS, except those it marks NOT RUN or DEFERRED, and is committed with no stored outputs.
