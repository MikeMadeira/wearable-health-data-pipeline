# INC-004: Steps Silver and Gold

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-003
(`intents/INC-003-sleep-silver-and-gold.md`); uses the approved G1 decisions in
`reports/INC-001/profiling-findings.md` (Section 7, steps and duplicate-slot decisions).

## Goal

Per-minute step records from the raw wearable steps files are validated into a typed silver
steps table, with defects flagged at field level. Daily step totals and their supporting
counts are derived per participant-day and added to `gold_clinical_daily` without changing
its existing columns or keys.

## Boundary

One code slice: steps end to end (silver, quality rules, gold columns). Heart rate,
cross-source metrics and incremental mode are separate increments.

## Requirements

### Silver table

- `silver_steps_minute`: one row per source record of every `steps.json` file. Natural key
  (`participant_id`, `minute_ts`), where `minute_ts` is the record timestamp as a local
  naive timestamp. Columns include `participant_id`, `local_date`, `minute_ts`, the typed
  `steps` value (NULL when withheld), the per-record flags, and `quality_flags`. The exact
  column list, including how the `_unit` field is represented, is chosen at planning.
- No row is dropped. The original text of a value is recoverable from the immutable raw
  files and, for flagged rows, from `quality_rejects`. Values are typed and normalized as in
  INC-002-01 and INC-003.
- Naive timestamps are UTC-05:00 local time. A timestamp with `Z` or an offset is converted
  to UTC-05:00 before use, reusing `parse_local_timestamp`. `local_date` is the local
  calendar date of the converted timestamp.
- A steps record for a participant not in `participants.csv` stays in silver, flagged as an
  orphan, and never reaches gold. An enrolled participant without a `steps.json` is allowed:
  there are no silver rows and the gold step columns hold NULL, 0 and false.
- A record whose local date is outside the study window stays in silver, is flagged, and is
  excluded from gold. A record whose timestamp cannot be parsed keeps its row, is flagged,
  and is excluded from gold.

### Quality rules

Rule ids, the source name and exact reason texts are chosen at planning. Every steps rule
always produces one `quality_results` row, with zero counts when nothing fails, and flagged
records are written to `quality_rejects` with their raw values. Handling follows the G1
decisions:

| Defect | Observed on profiled data | Handling |
|--------|---------------------------|----------|
| M-01: undocumented field `_unit` with value `steps_per_hour` | 120,963 records in 3 participants (P005, P033, P038), 84 participant-days | flag; withhold the step value and its contribution to `total_steps` and `steps_valid_minute_count`; no division by 60 |
| M-02: negative `steps` | 2 records | flag; withhold the value |
| M-02: `steps` above 30 | 95,902 records | flag as a burst only; the value stays in the totals; no upper cutoff |
| M-07: conflicting duplicate records for one participant-minute | P010, P019, P022, P025 on 2026-01-10 (full days), plus 5 duplicate slots each for P005, P034, P040, P050 | flag; withhold that minute (no first/last pick, no averaging); other minutes stay usable |
| M-07: exact duplicate records for one participant-minute | counted within the profiled duplicate slots | counted once |
| M-09: non-zero seconds in a timestamp | 28 records in 5 participants, each sharing its minute slot with another record | flag; no flooring; the duplicate rule applies when two records share a minute |

- Missing minutes (M-08) are not imputed: they are not zero, and they are not counted in
  `steps_valid_minute_count`. Gaps are visible through that count. Out-of-order records
  (M-10) are not a defect.
- Rules are also defined for defects not observed on the profiled data and report zero:
  null or non-integer step values, unparseable timestamps, and a participant id that does
  not match the folder.
- Counts of the observed rules are recomputed at planning where the rule definition differs
  from the profiling definition.

### Gold columns

`gold_clinical_daily` gains these columns, rebuilt per participant-day from silver:

- `total_steps`: sum of the valid per-minute step values of the day, NULL when the day has
  no valid minute (not 0).
- `steps_valid_minute_count`: number of minutes that contribute to `total_steps`, 0 when
  none.
- `steps_present`: BOOLEAN, true when the day has at least one steps record inside the study
  window, valid or not.
- `steps_quality_flags`: comma-joined sorted rule ids of the records of the day, or NULL.

Behavior:

- Days with no steps records hold NULL `total_steps`, a valid-minute count of 0,
  `steps_present` false and NULL flags.
- A day whose records are all withheld holds NULL `total_steps`, a count of 0 and
  `steps_present` true.
- Orphan, out-of-window and unparseable-timestamp records never contribute to gold.
- The existing columns, key and staged row set (enrolled participants x study-window days)
  are unchanged. For unchanged inputs the existing columns, including the sleep columns,
  keep their values.

### Operational behavior

- A rerun on unchanged inputs gives identical tables and a byte-identical report.
- Changing one steps record changes exactly that day's step columns in gold.
- `run_id` still derives from the manifest.
- Old databases without the step columns are upgraded in place with the new columns added
  before the MERGE, as in INC-003.

## Constraints

- Do not change: the existing `gold_clinical_daily` columns, key, values and MERGE behavior
  for the same inputs; the survey, participants and sleep silver tables; `raw_manifest`; run
  id derivation; CLI arguments; public function signatures; the harness (`scripts/`,
  `.github/`); `data/`.
- Steps parsing and rules live in a new module under `src/wearable_pipeline/`, reusing
  `normalize.py`; `pipeline.py` orchestrates. No new dependency: standard-library `json`
  plus DuckDB.
- Processing stays in the single full-rebuild run. The steps files total about 200 MB
  (roughly 2 million records); the real-data run time is recorded, and no run-time or memory
  target is set.
- INC-002, INC-002-01 and INC-003 tests and spec text that assert the exact gold column set
  or the rule count are updated by this increment. No other test is weakened.
- Out of scope:
  - heart-rate silver or gold;
  - cross-source metrics such as sleep-to-activity joins;
  - active minutes, coverage percentage, minimum-coverage thresholds and other step metrics;
  - deciding what `_unit=steps_per_hour` means, or any positive upper cutoff for steps;
  - weekly and cohort outputs;
  - incremental mode and file-change detection;
  - README, Docker and cloud changes;
  - profiling notebooks.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- silver grain and keys, column types, and no dropped rows;
- every rule in the table and the rules for unobserved defects, with counts, rejects with
  raw values and exact reasons;
- the daily total and valid-minute count on a hand-computed day, including a gap, a burst
  above 30 and a negative value;
- `_unit` records withheld from totals while kept in silver;
- exact duplicates counted once, and conflicting duplicates withholding only that minute;
- a non-zero-second timestamp not floored, and a collision resolved by the duplicate rule;
- naive versus `Z` timestamps, and a record crossing local midnight after conversion;
- orphan, out-of-window and unparseable-timestamp records absent from gold;
- a participant without `steps.json`, and a day with records that are all withheld, giving
  the specified column values;
- existing gold columns and values unchanged, and an old-schema database upgraded;
- rerun idempotency, and a one-record correction changing exactly one gold row;
- a deterministic run id and report.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-004/`.

Real-data evidence, run by the human and recorded in the implementation report:

- 2,014,950 `silver_steps_minute` rows (42 x 40,320 + 5 x 40,326 + 3 x 39,960) and 1,400
  gold rows; no row dropped;
- rule counts match profiling: `_unit` 120,963 records over 84 participant-days; 2 negative
  records; 28 non-zero-second records in 5 participants; the duplicate-slot and burst counts
  recomputed under the final rule definitions and recorded;
- whole-day and partial gaps visible as lower `steps_valid_minute_count` (P010, P019, P022,
  P025 on 2026-01-08; P020, P028, P041 partial), and conflicting duplicate days for those
  participants on 2026-01-10 withheld;
- participant-days without valid minutes show NULL `total_steps`, a count of 0 and
  `steps_present` as specified;
- the existing 16 gold columns identical to the previous run;
- a second run gives identical tables and report, `data/` SHA-256 is unchanged, and the
  run time is recorded.

Human step after implementation: a new steps review notebook is created and committed with
its outputs cleared, including the gold schema and example records, as for
`notebooks/INC-003-review.ipynb`. The orchestrator's implementation phase may not edit
`notebooks/`.
