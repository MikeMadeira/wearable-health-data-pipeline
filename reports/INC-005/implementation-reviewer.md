# Implementation Review Report: INC-005

Status: APPROVED
Verdict: PASS (no blocker, major or medium findings; three Low observations)
Inputs:
- intents/INC-005-heart-rate-silver-and-gold.md, specs/INC-005.md
- reports/INC-005/implementer.md, reports/INC-005/planning-reviewer.md, reports/INC-004/implementation-reviewer.md (precedent)
- src/wearable_pipeline/heart_rate.py, src/wearable_pipeline/pipeline.py (git diff vs HEAD 90e5102)
- tests/ (read-only)

Outputs:
- reports/INC-005/implementation-reviewer.md

Checks:
- `python -m pytest -q`: PASS (582 passed in 46.9 s; matches the claim)
- `git diff HEAD --stat` on tests/, specs/, intents/, scripts/, pyproject.toml, uv.lock, Dockerfile, requirements/: PASS (empty). The tests were not weakened and are the planning-reviewed state. Only the two src files are modified, plus the new untracked implementer report.
- New dependencies: PASS (none; stdlib `json`, `re`, `datetime`, `collections`, plus the existing `normalize.parse_local_timestamp`)
- `data/` access: PASS (not read; the real-data evidence step stays with the human)
- Disclosed script-based edits (Python in-place rewrite of the stub and string-replace of `pipeline.py`): PASS. `git status` shows no stray files. The resulting diff is the implementer's own content, and the full suite confirms it. This is a procedural slip only.
- Synthetic probes (temp dir, deleted afterwards, nothing committed): PASS
  - Stuck runs: 59 slots not flagged, 60 and 61 flagged, every record of the run flagged and withheld. Non-zero seconds inside slots still form a run (slot-based, as specified). An exact duplicate counts once (59 slots plus a duplicate gives no flag; 60 slots plus a duplicate flags 61 records). A folder-mismatched record in the middle neither joins nor bridges the run. Records from another folder carrying P001's id are mismatched and excluded from P001's timeline. An out-of-window or orphan record stays on the timeline.
  - Value typing: 2**31 and `"70"` and `1e2` give invalid (NULL raw). `True` is invalid, and `True` plus `1` in one slot gives a conflict with distinct signatures. 2**31-1 and -2**31 are valid int32 and out of range, with raw kept and normalized NULL. 39, 40, 200 and 201 respect the inclusive bounds. `70` against `"70"` is a conflict. Exact duplicate 300s are both flagged `HR_VALUE_OUT_OF_RANGE` and `HR_DUPLICATE_MINUTE_EXACT`, with the day present, NULL aggregates and count 0.
  - Timestamps: `{}`, a non-object element, `"x"` and lowercase `z` give `HR_TIMESTAMP_UNPARSEABLE` plus `HR_VALUE_INVALID` with NULL date and timestamp. `-05:00` is converted with raw text kept, and a padded `" ...Z "` keeps its original text. `2026-01-09T04:59:59Z` lands on 2026-01-08 23:59:59, is flagged converted and not aligned, and its day is the converted date. An out-of-window record is flagged and keeps its normalized value but has no daily entry.
  - Daily aggregates: mean 60.666666666666664 for 60, 61, 61 (unrounded, over contributing slots).
  - Pipeline (via `make_data`): gold has 28 columns and the existing 22 are unchanged in order. `silver_heart_rate_minute` holds the expected rows. `quality_results` has 41 rules, and each heart-rate rule has `checked_count` equal to the record count. `HR_TIMESTAMP_CONVERTED` has failed_count 1 and writes no reject (61 HR rejects, all for the stuck rule). A day with a 61-minute stuck run plus two valid records gives mean 89.5, min 80, max 99, count 2, present true and flags `HR_STUCK_SENSOR_RUN,HR_TIMESTAMP_CONVERTED`.
  - Additive upgrade: after dropping `hr_mean_bpm` and `hr_valid_minute_count` and inserting an unmatched legacy row, a rerun restored 28 columns by name, retained the legacy row with NULL values in every other column, and did not duplicate keys. The 10/16/22-column paths are covered by the existing tests, and the `ADD COLUMN IF NOT EXISTS` loop is generic.
  - Rerun idempotency, byte-identical report and mapping-order independence are covered by the passing tests and by the deterministic code (sorted paths, no time or randomness).
- Spec adherence and extras: PASS. Rules, handling, severities and reasons come from the table in `HEART_RATE_RULES` (merged into `RULES`, 41 rows). `HR_VALUE_OUT_OF_RANGE` flag is derived from the rule set, and there is no additional feature. Stuck detection runs before duplicate marking, which is consistent with the spec (clean slots exclude conflicts). `failed` comes from the layer, and converted records write no rejects.

Findings:
1. Low (style): `src/wearable_pipeline/heart_rate.py` ends with two extra blank lines (W391-style trailing blank lines). Remove them.
2. Low (style): in `pipeline.py` the new `from wearable_pipeline.heart_rate import ...` line sits after the `normalize` import, so it is out of alphabetical order (`heart_rate` should come before `normalize`). Trivial.
3. Low (maintainability): `_record` builds a large untyped dict per record, and `_mark_stuck` and the daily loop access it by string key. This is correct and fast enough (60,000 records in 1.7 s per the implementer, tests pass), but a small dataclass would be clearer. It is not required for approval.

Next action:
Orchestrator: route to human review. The human then runs the real-data evidence step (spec section 11.3: 2,014,950 silver rows, 1,400 gold rows, recomputed rule counts, existing 22 columns unchanged, rerun identical, `data/` unchanged, run time). The Low findings may optionally be fixed in the same commit; they do not block.

Verdict: PASS
