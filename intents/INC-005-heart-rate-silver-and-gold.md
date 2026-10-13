# INC-005: Heart Rate Silver and Gold

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-004
(`intents/INC-004-steps-silver-and-gold.md`); uses the approved G1 decisions in
`reports/INC-001/profiling-findings.md` (Section 7: timezone, day assignment, heart-rate
range, stuck-sensor, duplicate-slot, missing-minute and non-zero-second decisions).

## Goal

Per-minute heart-rate records from the raw wearable heart-rate files are validated into a
typed silver heart-rate table, with defects flagged at field level and stuck-sensor runs
detected. Daily mean, minimum and maximum heart rate and their supporting counts are
derived per participant-day and added to `gold_clinical_daily` without changing its
existing columns or keys.

## Boundary

One code slice: heart rate end to end (silver, quality rules, gold columns). Weekly and
cohort outputs, incremental mode and cross-source metrics are separate increments.

## Requirements

### Silver table

- `silver_heart_rate_minute`: one row per source record of every `heart_rate.json` file.
  Natural key (`participant_id`, `minute_ts`), where `minute_ts` is the record timestamp as
  a local naive timestamp (UTC-05:00). Columns include `participant_id`, `local_date`,
  `minute_ts`, the raw `heart_rate_bpm` value as typed, a normalized heart-rate column that
  is NULL when the value is withheld, the per-record flags and `quality_flags`. The planner
  chooses the final names, positions and exactly what the raw column holds for records
  whose value is not a valid integer, and states them in the spec. Normalization never
  overwrites the raw value.
- No row is dropped. The original text of a value is recoverable from the immutable raw
  files and, for flagged rows, from `quality_rejects`. Values are typed and normalized as in
  INC-002-01, INC-003 and INC-004.
- Naive timestamps are UTC-05:00 local time. A timestamp with `Z` or an offset is converted
  to UTC-05:00 before use, reusing `parse_local_timestamp`, and `local_date` is the local
  calendar date of the converted timestamp. The raw timestamp text of a converted record is
  kept.
- A heart-rate record for a participant not in `participants.csv` stays in silver, flagged
  as an orphan, and never reaches gold. An enrolled participant without a `heart_rate.json`
  is allowed: there are no silver rows and the gold heart-rate columns hold NULL, 0 and
  false.
- A record whose local date is outside the study window stays in silver, is flagged, and is
  excluded from gold. A record whose timestamp cannot be parsed keeps its row, is flagged,
  and is excluded from gold.

### Quality rules

Rule ids, the source name and exact reason texts are chosen at planning. Every heart-rate
rule always produces one `quality_results` row, with zero counts when nothing fails, and
flagged records are written to `quality_rejects` with their raw values, except the
informational timezone rule. Handling follows the G1 decisions:

| Defect | Observed on profiled data | Handling |
|--------|---------------------------|----------|
| M-03: `heart_rate_bpm` outside inclusive 40 to 200 | 4 records in 2 participants (P029, P037; min -10, max 999) | flag; withhold the normalized value (`withhold_field`, severity high); raw kept; rejects written; excluded from aggregates and from valid-minute counts |
| M-04: stuck-sensor run of at least 60 identical consecutive minute slots | 2 runs of 180 minutes (P003 from 2026-01-09T04:44, P042 from 2026-01-24T09:16); 360 minutes | flag every minute of the run, from the first identical one; raw kept; withhold the normalized value so flagged minutes are excluded from aggregates and valid-minute counts; threshold 60 is provisional |
| M-05: `Z` (UTC) timestamp | 2,880 records in 2 participants (P018, P044); 600 change calendar date after conversion | convert to UTC-05:00; informational rule (handling `convert`) sets the record and day flag, rejects nothing, and its failed count is the number of converted records |
| M-07: conflicting duplicate records for one participant-minute | P010, P019, P022, P025 on 2026-01-10 (full days), plus 5 duplicate slots each for P005, P034, P040, P050 | flag; withhold that minute (no first/last pick, no averaging); other minutes stay usable |
| M-07: exact duplicate records for one participant-minute | counted within the profiled duplicate slots | counted once |
| M-09: non-zero seconds in a timestamp | 28 records in 5 participants, each sharing its minute slot with another record | flag; no flooring; the duplicate rule applies when two records share a minute |

- Stuck-run detection:
  - A run is made of consecutive minute slots with the same in-range value. A missing
    minute slot, an out-of-range value, or a slot that is ambiguous because of conflicting
    duplicates breaks the run.
  - Runs are detected over the participant's whole timeline and may span midnight; flagged
    minutes are excluded on each local day they fall on.
  - A run of 59 identical minutes is not flagged; 60 is.
- Missing minutes (M-08) are not imputed: a missing minute is not a measured value and is
  not counted in the valid-minute count. Gaps are visible through that count.
  Out-of-order records (M-10) are not a defect.
- Rules are also defined for defects not observed on the profiled data and report zero:
  null or non-integer heart-rate values, unparseable timestamps, timestamps outside the
  study window, and a participant id that does not match the folder.
- Counts of the observed rules are recomputed at planning where the rule definition differs
  from the profiling definition.

### Gold columns

`gold_clinical_daily` gains these columns, rebuilt per participant-day from silver. The
planner chooses names and positions (for example `hr_mean_bpm`, `hr_min_bpm`, `hr_max_bpm`):

- mean, minimum and maximum of the day's valid normalized heart-rate values, NULL when the
  day has no valid minute (not 0). Whether the mean is rounded is decided at planning.
- `hr_valid_minute_count`: number of minutes that contribute to the aggregates, 0 when none.
- `hr_present`: BOOLEAN, true when the day has at least one heart-rate record inside the
  study window, valid or not.
- `hr_quality_flags`: comma-joined sorted rule ids of the records of the day, or NULL.

Behavior:

- Days with no heart-rate records hold NULL aggregates, a valid-minute count of 0,
  `hr_present` false and NULL flags.
- A day whose records are all withheld holds NULL aggregates, a count of 0 and `hr_present`
  true.
- Orphan, out-of-window and unparseable-timestamp records never contribute to gold.
- The existing 22 columns, the key and the staged row set (enrolled participants x
  study-window days) are unchanged. For unchanged inputs the existing columns, including the
  sleep and steps columns, keep their values.

### Operational behavior

- A rerun on unchanged inputs gives identical tables and a byte-identical report.
- Changing one heart-rate record changes exactly that day's heart-rate columns in gold.
- `run_id` still derives from the manifest.
- Old databases without the heart-rate columns (10-, 16- and 22-column gold tables and any
  other prior shape) are upgraded in place additively by column name, with the new columns
  added before the MERGE, as in INC-003 and INC-004.

## Constraints

- Do not change: the existing `gold_clinical_daily` columns, key, values and MERGE behavior
  for the same inputs; the survey, participants, sleep and steps silver tables;
  `raw_manifest`; run id derivation; CLI arguments and behavior; public function
  signatures; the harness (`scripts/`, `.github/`); `data/`.
- Heart-rate parsing and rules live in a new module under `src/wearable_pipeline/`, reusing
  `normalize.py`; `pipeline.py` orchestrates. No new runtime dependency: standard-library
  `json` plus DuckDB.
- Processing stays in the single full-rebuild run and keeps the INC-004 bulk-load approach.
  A scale test with heart-rate fixtures must stay fast (INC-004's scale test limit is under
  25 s). The real-data run time is recorded; no other run-time or memory target is set.
- INC-002, INC-002-01, INC-003 and INC-004 tests and spec text that assert the exact gold
  column set or the rule count are updated by this increment. No other test is weakened.
- Out of scope:
  - weekly and cohort outputs;
  - incremental mode and file-change detection;
  - resting heart rate, heart-rate zones relative to `max_heart_rate`, median, variability
    and any other heart-rate metric beyond mean, minimum and maximum;
  - imputation;
  - deciding a winner among conflicting duplicates;
  - cross-source metrics;
  - README, queries, Docker and cloud changes;
  - profiling notebooks.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- silver grain and keys, column types, and no dropped rows;
- every rule in the table and the rules for unobserved defects (unparseable timestamp,
  null or non-integer value, orphan, folder mismatch, out-of-window), with counts, rejects
  with raw values and exact reasons;
- the daily mean, minimum, maximum and valid-minute count on a hand-computed day, including
  a gap, an out-of-range value and a stuck run;
- the stuck-run boundary at 59, 60 and 61 identical minutes; a run broken by a gap, by an
  out-of-range value and by an ambiguous duplicate slot; a run spanning midnight;
- naive versus `Z` timestamps, including a record whose local date changes after
  conversion, the informational rule raising a flag but no reject;
- exact duplicates counted once, and conflicting duplicates withholding only that minute;
- a non-zero-second timestamp not floored, and a collision resolved by the duplicate rule;
- orphan, out-of-window and unparseable-timestamp records absent from gold;
- a participant without `heart_rate.json`, and a day with records that are all withheld,
  giving the specified column values;
- existing gold columns and values unchanged, and an old-schema database (10, 16 and 22
  columns) upgraded;
- rerun idempotency, and a one-record correction changing exactly one gold row;
- a deterministic run id and report;
- a scale test with heart-rate fixtures.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-005/`.

Real-data evidence, run by the human and recorded in the implementation report:

- 2,014,950 `silver_heart_rate_minute` rows (42 x 40,320 + 5 x 40,326 + 3 x 39,960) and
  1,400 gold rows; no row dropped;
- rule counts compared with profiling and recomputed under the final definitions: 4
  out-of-range records in 2 participants (M-03); 2 stuck runs and 360 minutes in P003 and
  P042 (M-04); 2,880 `Z` records in P018 and P044, 600 of which change calendar date
  (M-05); the duplicate-slot (M-07) and 28 non-zero-second (M-09) counts;
- whole-day and partial gaps visible as lower `hr_valid_minute_count` (P010, P019, P022,
  P025 on 2026-01-08; P020, P028, P041 partial), and conflicting duplicate days for those
  participants on 2026-01-10 withheld;
- participant-days without valid minutes show NULL aggregates, a count of 0 and
  `hr_present` as specified;
- the existing 22 gold columns identical to the previous run;
- a second run gives identical tables and report, `data/` SHA-256 is unchanged, and the
  run time is recorded.

Human step after implementation: a new heart-rate review notebook
(`notebooks/INC-005-review.ipynb`) is created and committed with its outputs cleared,
including the gold schema and example records, as for `notebooks/INC-003-review.ipynb` and
`notebooks/INC-004-review.ipynb`. The orchestrator's implementation phase may not edit
`notebooks/`.
