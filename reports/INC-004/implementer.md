# Implementation Report: INC-004

Status: READY

Inputs:
- intents/INC-004-steps-silver-and-gold.md, specs/INC-004.md (revision 3)
- reports/INC-004/planner.md, reports/INC-004/planning-reviewer.md (round 5 APPROVED)
- tests/test_steps.py, tests/test_walking_skeleton.py, tests/test_silver_normalization.py, tests/test_sleep.py (read only)

Outputs:
- src/wearable_pipeline/steps.py: `build_steps_layer` (pure, no I/O, reuses `normalize.parse_local_timestamp`). Per-record typing and the section 3a precedence (invalid, negative, unit-less, converted, unsupported), per-record duplicate signatures (value vs raw, unit text included), exact/conflict groups withholding the normalized value, activity level (<= 30 `at_rest_to_moderate`, else `burst`), 11 rules, rejects (none for `STEPS_UNIT_CONVERTED`), `failed` counter including converted records, and per-day `StepsDay` totals/counts/flags.
- src/wearable_pipeline/pipeline.py: `_read_event_files` (shared sleep/steps reader; new `PipelineError` for unreadable, non-UTF-8, invalid JSON, non-array and non-object steps files, raised before any table is touched); `STEPS_RULES` merged into `RULES` (30 rows); steps `failed`/`checked` taken from `StepsLayer`; `silver_steps_minute` (11 columns) and `quality_rejects` bulk-loaded with a temporary CSV in the system temp dir plus `COPY` (NULL = unquoted empty, strings always quoted, `ALLOW_QUOTED_NULLS false`, so an empty `unit` stays `''`); gold created with 22 columns, every missing column added with `ADD COLUMN IF NOT EXISTS`; staging insert and MERGE use explicit column names (legacy 10/16/20-column tables safe).

Checks:
- `python -m pytest -q`: 453 passed, 0 failed (about 34 s).
- Scale test `test_sixty_thousand_records_are_bulk_loaded_in_reasonable_time`: 1.8 s (limit 25 s).
- Tests, specs, intents, scripts/, .github/, notebooks/ and data/ not edited; data/ not read; no dependency changes; no commits.

Findings:
- No spec or test defects found.
- Process note: two edits of `steps.py`/`pipeline.py` were applied with a Python script instead of the edit tool; content is as reviewed in the diff.
- Pending human step (real data, not run by the implementer): evidence listed in specs/INC-004.md section 11 gate 3 (2,014,950 silver rows, 1,400 gold rows with 22 columns, converted 120,963 over 84 participant-days, unsupported count explained, negative 2, not-aligned 28, duplicate and invalid counts, `minutes_burst` sum, rerun identical, `data/` unchanged, run time).

Next action: Human runs the pipeline on the real data and records the evidence above; then reviewer gate.

## Real-data evidence (run on 2026-10-13, `output/runs/inc004`, not committed)
- Run: `python -m wearable_pipeline run --data-dir data --output-dir output/runs/inc004`; run_id `a826c8397cf5ad8e`; 41.5 s wall clock, 3.5 GB peak RSS.
- `silver_steps_minute`: 2,014,950 rows (2,009,183 distinct participant-minute keys; the rest are duplicate records). `gold_clinical_daily`: 1,400 rows, 22 columns.
- Rule counts (checked 2,014,950 for each): `STEPS_UNIT_CONVERTED` 120,963 over 84 participant-days (P005, P033, P038); `STEPS_UNIT_UNSUPPORTED` 0 (every hourly value is convertible); `STEPS_NEGATIVE` 2; `STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED` 28 in 5 participants; `STEPS_DUPLICATE_MINUTE_CONFLICT` 7,183; `STEPS_DUPLICATE_MINUTE_EXACT` 4,392; `STEPS_VALUE_INVALID`, orphan, folder-mismatch, out-of-window and unparseable 0.
- Rejects: 14,401 `quality_rejects` rows in total, of which 11,605 are steps (7,183 conflict, 4,392 exact, 2 negative, 28 not aligned); none for `STEPS_UNIT_CONVERTED`.
- Activity levels (silver): `at_rest_to_moderate` 1,987,239; `burst` 20,526; NULL 7,185 (7,179 non-converted withheld records: conflicts and 2 negatives; 6 converted records inside conflict groups). The 95,902 profiled raw values above 30 fall to 20,526 minutes above 30 after conversion: 19,745 unit-less and 781 converted. Gold: valid minutes 2,005,567 = 1,985,041 rest-to-moderate + 20,526 burst; no day violates the sum.
- Gaps: P010, P019, P022, P025 on 2026-01-08 have 0 valid minutes, NULL `total_steps` and `steps_present` false. P020 (2026-01-27), P028 (2026-01-15) and P041 (2026-01-29) have 1,080 valid minutes. On 2026-01-10 the conflicting duplicates are withheld: valid minutes P010 520, P019 541, P022 584, P025 531.
- P005's unit-less records at non-zero seconds are conflict slots: 3 on 2026-01-10 (plus 4 on 2026-01-14 and 2 on 2026-01-23).
- The 16 existing gold columns are identical to the `inc003` run (no rows differ in either direction).
- A second and third run give identical tables (all 9 compared row by row), the same run_id and a byte-identical report. A SHA-256 over all files in `data/` is unchanged (`3f16dc19…ab47`).
- The 4 days with no valid minute are exactly P010, P019, P022 and P025 on 2026-01-08, all `steps_present` false.
