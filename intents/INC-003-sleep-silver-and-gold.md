# INC-003: Sleep Silver and Gold

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-002 and
INC-002-01 (`intents/INC-002-walking-skeleton.md`,
`intents/INC-002-01-silver-normalization.md`); uses the approved G1 decisions in
`reports/INC-001/profiling-findings.md` (Section 7), including the updated sleep formulas.

## Goal

Sleep data from the raw wearable sleep files is validated into typed silver sleep session
and stage tables, with defects flagged at field level. The sleep metrics approved at G1 are
derived per participant-day and added to `gold_clinical_daily` without changing its
existing columns or keys.

## Boundary

One code slice: sleep end to end (silver, quality rules, gold columns). Steps, heart rate,
cross-source metrics and incremental mode are separate increments.

## Requirements

### Silver tables

- `silver_sleep_sessions`: one row per source session. Natural key
  (`participant_id`, `local_date`), where `local_date` is the source wake-up `date`.
  Columns: `participant_id`, `local_date`, `sleep_onset` and `sleep_end` as local
  timestamps, `supplied_efficiency_pct` (the source `efficiency_pct`, kept as supplied),
  `restlessness`, `is_duplicate_key`, `is_orphan`, `is_out_of_window` and `quality_flags`.
- `silver_sleep_stages`: one row per stage of a session. Key (`participant_id`,
  `local_date`, `stage_index`), where `stage_index` is the order of the stage in the
  session. Columns: `participant_id`, `local_date`, `stage_index`, `stage`, `start_time`,
  `end_time`, the supplied `duration_min`, the duration derived from the timestamps, and
  `quality_flags`. The supplied and derived durations are both kept.
- Values are typed and normalized as in INC-002-01: the original text of a value is
  recoverable from the immutable raw files and, for flagged rows, from `quality_rejects`.
  No row is dropped from either table.
- Naive timestamps are UTC-05:00 local time. A timestamp with `Z` or an offset is
  converted to UTC-05:00 before use. Silver stores local naive timestamps. The sleep
  `local_date` is the source wake-up `date`, not derived from the timestamps.
- A sleep row for a participant not in `participants.csv` stays in silver, flagged as an
  orphan, and never reaches gold. An enrolled participant without a `sleep.json` is allowed:
  there are no silver rows and the gold sleep columns are NULL.
- A session whose wake date is outside the study window stays in silver, is flagged, and
  is excluded from gold. A session whose wake date cannot be parsed keeps its row, is
  flagged, and is excluded from gold.

### Quality rules

Rule ids, source name and exact reason texts are chosen at planning. Every sleep rule always
produces one `quality_results` row, with zero counts when nothing fails, and flagged rows
are written to `quality_rejects` with their raw values.

| Defect | Observed on profiled data | Handling |
|--------|---------------------------|----------|
| S-01: stage `duration_min` differs from its timestamp interval | 4 sessions | flag and keep; metrics use the supplied `duration_min`; both durations are kept |
| S-02: session span over 12 hours | 3 sessions | flag and keep; metrics still computed |
| S-03: final stage start differs from `sleep_end` | 3 sessions | flag and keep; metrics still computed |
| S-04: supplied efficiency differs from the derived efficiency by more than 1 point | recomputed with the new formula at planning | flag only; the derived value is used and the supplied value is kept separately |
| S-05: `sleep_onset` has non-zero seconds | 1,379 sessions | flag and keep; no flooring |

- Session spans under 3 hours are not treated as outliers and get no rule.
- A final awake stage that ends after `sleep_end` (S-07) is expected: `sleep_end` is the wake
  time and the start of the final awake tail. It is not a defect and gets no rule.
- Rules are also defined for defects not observed on the profiled data and report zero:
  duplicate (`participant_id`, wake date) sessions, unparseable session fields, unknown stage
  names, non-integer or non-positive `duration_min`, unparseable stage times, and stage gaps
  or overlaps.
- Invalid session fields (unparseable `sleep_onset`, `sleep_end` or `date`): the field is
  withheld (NULL), the row is kept and flagged.
- Invalid stage values (unknown stage name, non-integer or non-positive `duration_min`,
  unparseable stage time): only the affected stage's contribution is withheld and the
  session is flagged. If any stage of a session is withheld, the metrics that depend on the
  full timeline (`deep_sleep_pct`, `sleep_efficiency_pct`) are NULL. `total_sleep_min` is
  kept only when all non-awake stages are valid.

### Gold columns

`gold_clinical_daily` gains these columns, rebuilt per participant-day from silver:

- `total_sleep_min`: sum of non-awake stage `duration_min`.
- `deep_sleep_pct`: deep minutes / total non-awake minutes x 100.
- `sleep_efficiency_pct`: sum of non-awake `duration_min` / sum of all stage `duration_min`
  x 100. The denominator is the stage timeline and does not use `sleep_onset` or
  `sleep_end`.
- `sleep_session_count`: number of sessions contributing to the day.
- `sleep_present`: BOOLEAN, true when at least one session contributes.
- `sleep_quality_flags`: comma-joined sorted rule ids of the contributing sessions, or NULL.

Behavior:

- Days with no contributing session hold NULL metrics, `sleep_present` false and a count of 0.
- If non-awake minutes are 0 or no valid stages exist, the percentage columns are NULL, not 0.
- Two sessions for the same participant and wake date: exact duplicates count once;
  conflicting ones withhold all sleep metrics for that day. There is no first/last pick and
  no averaging.
- The MERGE updates only the sleep columns of matched keys. The key and the staged row set
  (enrolled participants x study-window days) are unchanged.

### Operational behavior

- A rerun on unchanged inputs gives identical tables and a byte-identical report.
- Changing one sleep session changes exactly that day's sleep columns in gold.
- Orphan and out-of-window sleep never appear in gold.
- `run_id` still derives from the manifest.

## Constraints

- Do not change: the existing `gold_clinical_daily` columns, key, values and MERGE behavior
  for the same inputs; the survey and participants silver tables; `raw_manifest`; run id
  derivation; CLI arguments; public function signatures; the harness (`scripts/`,
  `.github/`); `data/`.
- Sleep parsing and rules live in a new module under `src/wearable_pipeline/`, reusing
  `normalize.py`; `pipeline.py` orchestrates. No new dependency: standard-library `json`
  plus DuckDB.
- INC-002 and INC-002-01 tests and spec text that assert the exact gold column set or the
  rule count are updated by this increment. No other test is weakened.
- Out of scope:
  - steps and heart-rate silver or gold;
  - cross-source metrics such as sleep-to-activity joins;
  - weekly and cohort outputs;
  - incremental mode and file-change detection;
  - chronotype-specific sleep logic;
  - restlessness or supplied efficiency columns in gold;
  - README, Docker and cloud changes;
  - profiling notebooks.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- silver grain and keys for sessions and stages, column types, and no dropped rows;
- every S-01 to S-05 rule and the rules for unobserved defects, with counts, rejects with
  raw values and exact reasons;
- the three formulas on a hand-computed session, including a zero non-awake case;
- wake-date assignment for a session that crosses midnight;
- naive versus `Z` timestamps;
- duplicate and conflicting sessions;
- orphan and out-of-window sleep absent from gold;
- a participant without `sleep.json` giving NULL sleep columns and `sleep_present` false;
- existing gold columns and values unchanged;
- rerun idempotency, and a one-session correction changing exactly one gold row;
- a deterministic run id and report.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-003/`.

Real-data evidence, run by the human and recorded in the implementation report:

- 1,400 gold rows, 1,400 silver sleep sessions and 7,000 silver sleep stages;
- sleep rule counts match profiling: S-01 4 sessions, S-02 3 sessions over 12 hours, S-03 3
  sessions, S-05 1,379 sessions; the S-04 count is recomputed with the new formula and
  recorded;
- the `sleep_efficiency_pct` range is recorded and compared with the stage-based range in
  the profiling findings (Section 7), not with the superseded span-based one;
- the existing gold columns are identical to the previous run;
- a second run gives identical tables and report, and `data/` SHA-256 is unchanged.

Human step after implementation: a new sleep review notebook is created and committed with
its outputs cleared, as for `notebooks/INC-002-review.ipynb`. The orchestrator's
implementation phase may not edit `notebooks/`.
