# Profiling Findings: INC-001

Scope: observed evidence from the four notebooks in `notebooks/INC-001-*.ipynb`, executed top to bottom with nbclient
against the unmodified `data/` directory. **Every rule, threshold and handling named below is a proposal for the human at
gate G1; nothing here is decided.** Handling vocabulary: `fail` / `quarantine` / `flag-and-keep` / `exclude`.

Notebook references use the cell id (`tab-NN`, `slp-NN`, `shr-NN`, `xs-NN`; stored as `metadata.id` and top-level `id`).
Each cited count is the printed output of that code cell in the final verification run. Examples are participant and
date/timestamp identifiers only; no raw measurement rows are reproduced.

Notebooks: `tabular` = `INC-001-tabular.ipynb`, `sleep` = `INC-001-sleep.ipynb`, `steps-hr` = `INC-001-steps-heart-rate.ipynb`,
`cross` = `INC-001-cross-source.ipynb`.

## 1. Runtime and parse-time measurements

| Notebook | Total runtime (final run) | Measurement |
|---|---|---|
| tabular | 0.11 s (`tab-14`) | three small CSVs |
| sleep | 0.12 s (`slp-12`) | 50 files, 1,489,394 bytes; read about 0.002 to 0.01 s, JSON parse 0.007 s in total (about 0.1 ms per file) |
| steps-hr | about 20 s (`shr-17`: 20.3 s) | 100 files, 403,475,691 bytes; JSON parse 2.09 s in total (steps 1.06 s, heart rate 1.03 s; about 21 ms per file; 0.52 microseconds per minute-record); read 0.07 s in the final run (0.57 to 1.15 s on cold file cache) |
| cross | about 11 s (`xs-14`: 10.9 s) | streams 150 files once; dominated by per-record Python work, not by JSON parse |

All four notebooks finish far below the 600 s harness limit (largest about 21 s wall clock). Memory: one participant file at a time.

## 2. Observed defects

Severity scale (proposal): High = would corrupt a daily aggregate if ignored; Medium = affects some participant-days or needs a rule;
Low = cosmetic or rare.

### Tabular (`tabular` notebook)

| ID | Source / check | Count / rate | Cell | Examples | Severity | Proposed handling (proposal) |
|---|---|---|---|---|---|---|
| T-01 | `wellness_survey.csv` participant orphan (participant_id not in `participants.csv`) | 14 rows, 1 id (rows are one per day for 14 days; 1,414 rows total vs 1,400 enrolled) | `tab-10`, `tab-12` | P999 | Medium | quarantine (keep out of participant-keyed outputs; retain the raw rows) |
| T-02 | `device_metadata.csv` blank `firmware_version` | 15 of 50 rows (30%); `device_label` carries a parseable `fw<version>` in 15 of 15; 0 conflicts where both are present | `tab-06` | P006, P008, P015, P016, P021 | Medium | flag-and-keep: derive from `device_label` by regex, record the source of the value |
| T-03 | `device_label` firmware values for the 15 blank rows | all 15 parse to `2.1.8`, a value that appears in no populated `firmware_version` cell (populated values: `2.2.0` x23, `2.2.1` x12) | `tab-06` | same 15 rows | Low | flag-and-keep; ask the human whether `2.1.8` is genuine |

### Sleep (`sleep` notebook)

| ID | Source / check | Count / rate | Cell | Examples | Severity | Proposed handling (proposal) |
|---|---|---|---|---|---|---|
| S-01 | Stage `duration_min` vs `end_time - start_time` (whole minutes) | 4 sessions of 1,400 (7,000 stages); 4 participants | `slp-08` | P007@2026-01-21, P010@2026-01-20, P024@2026-01-11, P035@2026-01-17 | Medium | flag-and-keep; proposal: derive durations from timestamps, keep the raw `duration_min` |
| S-02 | Session span outliers (`sleep_end - sleep_onset` > 12 h, < 3 h) | 3 sessions > 12 h (max span 1,327.5 min), 2 sessions < 3 h (min 168 min); the three > 12 h sessions are the same three as S-03 | `slp-08`, `slp-10` | > 12 h: P026@2026-01-23, P035@2026-01-20, P039@2026-01-14; < 3 h: P010@2026-01-25, P027@2026-01-27 | Medium | quarantine pending review, or flag-and-keep |
| S-03 | Final stage starts at a time other than `sleep_end` | 3 of 1,400 sessions | `slp-08` | P026@2026-01-23, P035@2026-01-20, P039@2026-01-14 | Medium | flag-and-keep |
| S-04 | `efficiency_pct` vs asleep minutes / session span (difference > 1 point) | 218 sessions across all 50 participants (15.6%); `efficiency_pct` equals exactly 100.0 in 1,132 of 1,400 sessions; minimum 50.0 | `slp-08`, `slp-10` | P001@2026-01-28, P002@2026-01-09, P002@2026-01-18 | Low to Medium | flag-and-keep; proposal: derive efficiency from stages (dictionary says to) rather than trust the field |
| S-05 | `sleep_onset` not minute-aligned | 1,379 of 1,400 sessions have non-zero seconds in `sleep_onset` | `slp-08` | all participants | Low | flag-and-keep; relevant to any join to the minute grid |
| S-06 | Sleep session stage-sum vs span (all four stages incl. awake) | `sum(duration_min)` differs from `sleep_end - sleep_onset` in 1,400 of 1,400 sessions (median +59 min); `sum(non-awake duration_min)` differs in only 7 sessions (6 participants) | `slp-08`, `slp-10` | P007@2026-01-21, P010@2026-01-20, P024@2026-01-11, P026@2026-01-23, P035@2026-01-17 | Low | flag-and-keep; see assumption A-03 |
| S-07 | Last stage ends after `sleep_end` | 1,397 of 1,400 sessions: the final `awake` stage begins at `sleep_end` and ends later, so `sleep_end` is the start of the awake tail, not the end of the last stage | `slp-08`; `xs-08` | all participants | Medium | flag-and-keep; document the definition of `sleep_end` |

Notes: sessions with onset after midnight (155 sessions, 47 participants; `slp-08`) and sessions with onset on 2026-01-07 (43, before
the study window; `xs-06`) are observations that matter for day assignment, not defects.

### Steps and heart rate (`steps-hr` notebook; some counts from `cross`)

| ID | Source / check | Count / rate | Cell | Examples | Severity | Proposed handling (proposal) |
|---|---|---|---|---|---|---|
| M-01 | `steps.json` undocumented field `_unit` (single value `steps_per_hour`) | 120,963 records in 3 participants (P005 40,323; P033 40,320; P038 40,320); 84 participant-days; median steps with `_unit` about 60 times the unit-less median (P033 300, P038 420 vs 5 overall) | `shr-04`, `shr-11`, `xs-10` | P005, P033, P038 | High | quarantine until the unit semantics are confirmed; do not mix with per-minute counts silently |
| M-02 | `steps` out of the documented 0-30 expectation | 95,902 records above 30 (4.8%), 56,389 above 200, 5,141 above 1,000 (26 distinct values), max 1,000,000; 2 records below 0 (min -1). Participants with any value > 200: P005, P029, P033, P037, P038 (P005/P033/P038 are the `_unit` participants; P029/P037 are not) | `shr-13` | P029, P037 (negative and extreme values) | High | flag-and-keep for burst values (dictionary allows bursts); quarantine negative/sentinel-like values |
| M-03 | `heart_rate_bpm` outside 40-200 | 4 records (2 below 40, 2 above 200; observed min -10, max 999) in 2 participants | `shr-13` | P029, P037 | High | quarantine the records; exclude from daily aggregates |
| M-04 | Heart-rate stuck-sensor runs (identical consecutive values, in range) | 2 runs of 180 min (runs >= 60 min: 2; runs >= 240 min: 0; any run >= 10 min: only these 2); 360 minutes = 0.018% of heart-rate records | `shr-15` | P003 from 2026-01-09T04:44; P042 from 2026-01-24T09:16 | Medium | flag-and-keep with a run-length flag; proposal: threshold of 60 identical minutes |
| M-05 | `heart_rate.json` UTC `Z` timestamps | 2,880 records in 2 participants (P018 1,440 on a full day; P044 1,440); 600 of them change calendar date after UTC-05:00 conversion. Interpreted as UTC and shifted by -5 h, all 2,880 land in empty grid slots and 0 collide; interpreted as naive labels, 2,280 fill empty slots and 600 collide with existing records | `shr-08`, `xs-08` | P018 (gap 2026-01-25), P044 (gap 2026-01-17) | High | flag-and-keep with conversion to local; proposal pending decision on the timezone rule |
| M-06 | Records per participant vs 40,320 (both files) | 42 participants have 40,320; 5 have 40,326 (P005, P010, P034, P040, P050); 3 have 39,960 (P020, P028, P041) | `shr-06` | as listed | Medium | flag-and-keep |
| M-07 | Duplicate minute slots (naive timestamps) | steps and heart rate each: 5,790 excess records in 8 participants. P019, P022, P025 each duplicate the whole day 2026-01-10 (1,440 slots) and miss the whole of 2026-01-08; P010 does the same plus 5 more duplicate slots; P005, P034, P040, P050 have 5 duplicate slots each. Duplicate pairs with conflicting values (full-day cases): steps P010 921, P019 899, P022 856, P025 909; heart rate P010 1,297, P019 1,300, P022 1,290, P025 1,289 | `shr-06`, `shr-10` | P010, P019, P022, P025 (day 2026-01-10); P005, P034, P040, P050 | High | quarantine conflicting duplicates; the rule for choosing among them is for the human |
| M-08 | Gaps on the minute grid | steps: 6,840 missing slots in 7 participants; heart rate: 9,720 in 9 participants (2,880 of these are the Z records of M-05, which refill them after conversion). Whole-day gaps: P010, P019, P022, P025 on 2026-01-08. Partial gaps of 360 slots in both sources: P020 on 2026-01-27, P028 on 2026-01-15, P041 on 2026-01-29 | `shr-06`, `shr-10` | as listed | High | flag-and-keep; no imputation (proposal) |
| M-09 | Non-zero-second timestamps | 28 records in 5 participants per file (P005 6, P010 6, P040 6, P050 6, P034 4); every such record shares its minute slot with another record | `shr-08`, `shr-10` | as listed | Medium | quarantine or deduplicate by minute floor (proposal) |
| M-10 | Out-of-order records (timestamp earlier than previous record) | 4 records in each file (P010, P019, P022, P025, one each) | `shr-06` | as listed | Low | flag-and-keep; sort by timestamp before profiling or processing |
| M-11 | Steps and heart-rate timestamp sequences differ | 48 of 50 participants identical; the 2 that differ are P018 and P044 (the Z files) | `shr-15` | P018, P044 | Low | flag-and-keep |

### Cross-source (`cross` notebook)

| ID | Source / check | Count / rate | Cell | Examples | Severity | Proposed handling (proposal) |
|---|---|---|---|---|---|---|
| X-01 | Participant-days with a minute-grid gap | naive only: steps 1,393 / heart rate 1,391 of 1,400 days have all 1,440 slots; with converted Z: heart rate 1,393; 4 steps days and 6 heart-rate days have zero naive slots; 3 days each are partial | `xs-10` | P010, P019, P022, P025 (2026-01-08); P020, P028, P041 (partial) | Medium | flag-and-keep with a completeness column |
| X-02 | Participant-days carrying at least one quality flag (duplicate slot, steps < 0, heart rate outside 40-200, heart-rate run >= 60, `_unit` field) | 108 of 1,400 (7.7%): `_unit` steps 84, duplicate slots 19 (steps) and 19 (heart rate), steps < 0 2, heart rate out of range 4, stuck runs 2. Complete (with Z conversion) and flag-free: 1,285 of 1,400 | `xs-10` | per reason, see M-01 to M-09 | Medium | informs the quality policy |
| X-03 | Sleep sessions starting before the minute grid (onset before 2026-01-08T00:00) | 43 sessions (night before the window start); heart-rate coverage over the full session span is complete for 1,350 of 1,400 sessions | `xs-06`, `xs-08` | first-night sessions | Low | flag-and-keep |

## 3. Checks that found nothing (reported for completeness)

| Source | Check | Result | Cell |
|---|---|---|---|
| tabular | Duplicate `participant_id` / `device_id` in participants; duplicate keys in device_metadata; duplicate (participant, date) in survey; exact duplicate survey rows | 0 | `tab-04` |
| tabular | `participant_id` / `device_id` format violations; whitespace in survey fields | 0 | `tab-04` |
| tabular | Blank/null values other than `firmware_version`; non-integer or out-of-range survey scores | 0 (survey observed ranges: fatigue 1-5, stress 1-5, readiness 0-10, sleep quality 2-5) | `tab-06`, `tab-08` |
| tabular | Domains: gender male 38 / female 12; chronotype A 28 / B 22; wear_site 2 values; calibration dates parse and all precede the window (2025-12-04 to 2026-01-07) | no violation | `tab-08` |
| tabular | `firmware_version` vs `device_label` disagreement; label serial vs `device_id`; device_id mismatch between participants and device_metadata | 0 | `tab-06`, `tab-10` |
| tabular | Participants without device metadata, without survey, without a wearable folder or missing JSON files; device-metadata orphans; folders not in participants | 0 | `tab-10` |
| tabular | Survey dates not ISO, or outside the study window; days with no survey rows | 0 | `tab-12` |
| sleep | Sessions per participant other than 28; duplicate (participant, date); missing wake dates in the window | 0 (50 participants x 28 = 1,400) | `slp-06` |
| sleep | `participant_id` != folder; null/blank fields; unparseable dates; wake date outside window; timestamp format other than `...000` | 0 | `slp-08` |
| sleep | `sleep_onset >= sleep_end`; stage order other than light-deep-rem-light-awake; unknown stage names; non-integer, non-positive durations; stage gaps/overlaps; first stage start != onset; stage spans not whole minutes | 0 | `slp-04`, `slp-08` |
| sleep | `sleep_end` date != `date` (wake-date misalignment); `efficiency_pct` outside 0-100; `restlessness` outside 0-1 | 0 | `slp-08` |
| steps-hr | `participant_id` != folder; null or non-integer values; unparseable timestamps; fractional seconds; timezone offsets other than `Z`; naive timestamps outside the 28-day grid | 0 | `shr-08`, `shr-06` |
| steps-hr | `Z` timestamps in `steps.json` | 0 | `shr-08` |
| steps-hr | Heart-rate runs of identical values >= 240 minutes (and any other run >= 10 minutes besides the two in M-04) | 0 | `shr-15` |
| cross | Participants in one source but not another (enrolled participants) | 0 for all seven sources; only P999 in the survey | `xs-06` |
| cross | Participant-days with survey but no sleep, or sleep but no survey | 0 (1,400 of 1,400 aligned) | `xs-06` |
| cross | Sleep wake dates outside the window; survey dates outside the window | 0 | `xs-06` |

## 4. Survey grain

**The survey is daily grain per participant**: 1,400 rows for the 50 enrolled participants, one row per (participant_id, date), 0 duplicate
keys, all 28 study days present for every enrolled participant (`tab-04`, `tab-12`, `xs-12`). The extra 14 rows belong to P999 (T-01).

## 5. Assumptions (not verified by data, kept separate from defects)

| ID | Assumption | Evidence / status |
|---|---|---|
| A-01 | Naive timestamps are local UTC-05:00 (dictionary statement). | Cannot be proven from naive values. Supporting evidence: shifting the `Z` records by -5 h fills all 2,880 empty slots with 0 collisions (`shr-08`). |
| A-02 | `_unit = steps_per_hour` means the value is a per-hour rate and would need conversion to per-minute counts. | Unverified; the scaling is consistent with about 60x (`shr-11`) but no conversion rule was tested. |
| A-03 | `sleep_end` marks the start of the final awake tail (not the end of the last stage). | Consistent with 1,397 of 1,400 sessions (`slp-08`); the data dictionary does not state it. |
| A-04 | The duplicated 2026-01-10 day and the missing 2026-01-08 day in P010, P019, P022, P025 are related (a mis-stamped day). | The values do not match exactly (conflicting values in 856 to 1,300 pairs), so equivalence is not shown (`shr-10`). |
| A-05 | The 40-200 bpm and 0-30 steps/minute ranges from the dictionary are the right validity thresholds. | Dictionary-provided; the 0-30 steps bound is flagged as an "expected" rate with bursts allowed. |

## 6. Decision Options from Profiling (Superseded)

The following entries preserve the options and evidence considered during profiling. Their former unresolved statuses are
superseded by the human decisions in Section 7; unselected options remain context only.

1. **Quality policy** (UNRESOLVED). Options: (a) fail the run on any defect; (b) quarantine defective records into a
   side table and keep the rest; (c) flag-and-keep every record with quality columns; (d) exclude whole affected
   participant-days. Evidence: 108 of 1,400 participant-days carry a flag, 1,285 are complete and flag-free (`xs-10`). Proposal:
   (b) for records with impossible values or conflicting duplicates (M-02 negative, M-03, M-07, M-09, T-01, M-01), (c) for
   statistical anomalies and gaps (M-04, M-08, S-01 to S-07); thresholds are for the human to confirm.
2. **Timezone** (UNRESOLVED). Options: (a) treat naive timestamps as local UTC-05:00 and convert `Z` to local; (b) store UTC
   everywhere; (c) reject `Z`. Evidence: `Z` records exist only in heart rate for 2 participants and fit the grid only after the
   -5 h shift (M-05, `shr-08`). Proposal: (a), with the fixed offset recorded in the contract.
3. **Day assignment** (UNRESOLVED). Minute data options: M1 local date of the naive timestamp; M2 same after converting `Z`.
   Sleep options (`xs-08`): S1 wake date `date` field (1,400 distinct participant-days, 0 collisions); S2 onset date (1,267
   distinct, 43 outside the window, 133 collisions); S3 date holding most of the session (1,393 distinct, 7 collisions);
   S4 `sleep_end` date (identical to S1 for 1,400 of 1,400). 1,245 sessions cross midnight. If the naive timestamps were
   treated as UTC, 20.8% of minute records would move to the previous date (418,800 steps and 418,200 heart-rate records,
   `xs-08`). Proposal: M2 for minutes and S1 for sleep (1:1 with survey days).
4. **Daily grain** (UNRESOLVED). Options: participant-day (`participant_id`, local date) as the key of the daily output; or
   participant-day with an explicit completeness/quality column. Evidence: survey and sleep are exactly one row per
   participant-day (1,400 each, `xs-06`); minute data aggregates to the same key. Proposal: participant-day keyed output.
5. **Walking-skeleton source** (UNRESOLVED). Candidates: `wellness_survey.csv` (1,400 enrolled rows, one defect T-01, flat CSV, daily
   grain, `tab-12`) or `sleep.json` (1,400 sessions, nested stages, 7 defect types). Proposal: the survey, as the
   smallest source with the target grain.
6. **Week boundary** (UNRESOLVED). The window starts on Thursday 2026-01-08 and ends on Wednesday 2026-02-04 (`xs-12`). Options:
   W1 study-relative 7-day blocks (four full weeks, 7/7/7/7); W2 ISO Monday-start weeks (4/7/7/7/3 days, partial first and last
   week); W3 Sunday-start weeks (3/7/7/7/4). Proposal: W1 if four equal weeks are wanted; W2 if calendar weeks are wanted, with an
   explicit partial-week flag.
7. **Weekly schema** (UNRESOLVED). Options: a distinct weekly table keyed (`participant_id`, `week_id`) with biostatistics
   columns (mean/variance, number of valid days, coverage); a long format keyed by metric; or reuse of the daily table. Evidence:
   coverage varies per week for 7 participants with incomplete minute days, so a `valid_days` column is needed under any option
   (`xs-10`). Proposal: separate weekly table with `valid_days` and the week-definition recorded.
8. **Cohort definition** (UNRESOLVED). Chronotype A 28, B 22 (`tab-08`). Options: all enrolled participants (28/22); participants
   with all 28 days complete (43 total, A 23 / B 20 after converting `Z`; 41 without); participants with at least 26 complete
   days (all 50: A 28 / B 22) (`xs-12`). Sleep sessions by chronotype: A 784, B 616. Proposal: all enrolled participants with a
   per-day validity filter instead of dropping participants.
9. **Daily freshness** (UNRESOLVED). Options: (a) full refresh per batch of all 28 days; (b) incremental by participant-day with
   idempotent upsert on the daily key; (c) watermark per participant based on the last complete day. The data is a static 28-day
   batch with no arrival timestamps in any file; late-arriving or corrected rows cannot be identified from the data (M-07 conflicting
   duplicates are the closest evidence). Proposal: (b), keyed on the daily grain, so reruns are idempotent.
10. **Further items to record** (UNRESOLVED): the handling of `_unit` records (M-01), whether `2.1.8` firmware is real (T-03), the
    definition of `sleep_end` (A-03), and the rule for choosing between conflicting duplicate records (M-07).

## 7. Human Decisions

Selected by the human during profiling review on 2026-10-11. The G1 choices below are approved for the walking skeleton.
G2/G3/G4 directions are selected but must be ratified at their named review gates before those slices are implemented. Keep
all source records and raw values; handle defects at field/measure level so unrelated valid measurements remain usable.

### G1: walking-skeleton decisions

1. **Quality policy:** preserve each raw row in bronze. Normalize, flag, or make only the affected normalized field unavailable
    so it cannot contaminate metrics. Preserve other valid fields from the same row/day; do not fail or exclude a whole day for a
    field-level defect. Persist quality outcomes and provenance.
2. **Timezone:** interpret naive wearable timestamps as local UTC-05:00 and convert explicit `Z` timestamps to that local
    timeline before joins and local-date assignment. Record the offset in the data contract. This matches the observed grid fit
    for the `Z` records (M-05, `shr-08`).
3. **Day assignment:** use the sleep `date` wake-up field for sleep and normalized local calendar date for minute records
    (`xs-06`, `xs-08`).
4. **Daily grain:** `gold_clinical_daily` is keyed by (`participant_id`, `local_date`) and includes quality/completeness or
    valid-count fields.
5. **Walking skeleton:** start with `wellness_survey.csv`. Keep P999 in silver with an orphan flag; exclude it only from outputs
    requiring an enrolled-participant join.

### Field-level quality decisions

- **Steps `_unit=steps_per_hour` (M-01):** preserve raw rows; flag and withhold the normalized per-minute step value and its
   aggregate contribution until source semantics are confirmed. Do not assume division by 60.
- **Step values (M-02):** negative values are invalid for normalized step metrics. Values above 30 are flagged as bursts, not
   rejected. No positive upper cutoff is approved; decide only after semantic review. Apply after resolving `_unit`.
- **Heart-rate range (M-03):** values outside inclusive 40-200 bpm are preserved raw, flagged, and excluded only from the
   normalized HR field and HR aggregates.
- **Stuck HR (M-04):** provisionally flag runs of at least 60 identical consecutive minutes. Preserve raw readings but exclude
   flagged HR values from aggregates; revisit this provisional threshold with validated behavior or later data.
- **Duplicate minute slots (M-07):** preserve all raw rows. For conflicting values at one participant-minute, mark only that
   measure/slot ambiguous; do not pick first/last or average it, and omit that measure from aggregates. Exact duplicates must not
   inflate counts or totals; unrelated measures/slots remain usable.
- **Missing minutes (M-08):** do not impute. Aggregate observed eligible readings and expose coverage/valid counts; missing
   steps are not zero and missing HR is not a measured value.
- **Non-zero-second timestamps (M-09):** preserve and flag; do not silently floor where it creates a duplicate. Apply the
   duplicate-slot rule to any resulting collision.
- **Firmware fallback (T-02/T-03):** parse `device_label` when structured `firmware_version` is blank, preserving the raw blank
   and recording fallback provenance. Flag `2.1.8` as unexpected for review; do not discard the device row.
- **Sleep stage durations (S-01):** use `duration_min` for stage metrics as documented; flag mismatches with timestamp
   intervals and preserve both values rather than silently recomputing.
- **Sleep formulas:** `total_sleep_min` = sum of non-awake stage durations; `deep_sleep_pct` = deep minutes / total non-awake
   minutes * 100; derived `sleep_efficiency_pct` = sum of non-awake `duration_min` / sum of all stage `duration_min` * 100.
   The denominator is the stage timeline and does not use `sleep_end` or `sleep_onset`. The earlier span-based denominator
   (`sleep_end` - `sleep_onset`) is superseded: it gave about 100% for 1,393 of 1,400 sessions (median 100, observed range
   26.9 to 158.8), while the stage-based value ranges from 74.6 to 94.4 (median 87.4). Preserve the supplied efficiency field
   separately for comparison; it matches the stage-based value within 1 point in only 7 sessions.
- **`sleep_end` (S-03/S-07/A-03):** treat as wake-up time and start of the final awake tail, consistent with 1,397/1,400
   sessions. Assign by the explicit wake-up date; do not count the post-wake awake tail as sleep.

### Later-gate directions selected

6. **Week boundary (G2):** study-relative seven-day blocks anchored on 2026-01-08: Jan 8-14, Jan 15-21, Jan 22-28, and
    Jan 29-Feb 4. Store `week_id`, `period_start_date`, and `period_end_date`.
7. **Weekly schema (G2):** distinct wide participant-week table keyed by (`participant_id`, `week_id`), with period boundaries,
    approved weekly measures, and valid-day/coverage fields. Exact statistical measures remain for G2 review.
8. **Chronotype population (G3):** retain all enrolled participants in A/B cohorts; use metric-specific validity/coverage
    denominators and report cohort size and valid counts. Cohort output grain and metric list remain open for G3.
9. **Daily freshness (G4):** incremental processing by affected participant-day with idempotent MERGE on
    (`participant_id`, `local_date`). Detect changed source inputs with a deterministic file manifest/checksum or equivalent;
    source files do not provide arrival timestamps, so do not rely on watermarks alone.
10. **Local storage (G4):** native DuckDB tables for local persisted outputs, using its transaction and MERGE semantics.
      Iceberg remains a proposed cloud table format only.

### Still open

- Exact weekly statistical measures, chronotype output grain, and cohort metric list require consumer requirements and G2/G3 review.
- Positive upper cutoff for steps; semantics of `_unit=steps_per_hour`; and whether firmware `2.1.8` is valid.
- Minimum daily/weekly coverage thresholds and when an aggregate should be unavailable for insufficient coverage.
- The 60-minute stuck-sensor threshold is provisional and should be revisited with validated device behavior or future data.
