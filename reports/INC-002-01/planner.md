# Planner Report: INC-002-01

Status: READY
Inputs:
- intents/INC-002-01-silver-normalization.md (controlling scope)
- intents/INC-002-walking-skeleton.md, specs/INC-002.md, tests/test_walking_skeleton.py
- reports/INC-002/implementation.md, reports/INC-002/implementation-review.md
- src/wearable_pipeline/pipeline.py (read only)
- AGENTS.md, .github/skills/sdd-tdd/SKILL.md, .github/skills/agent-communication/SKILL.md
- scripts/orchestrate_increment.py (planning gate)

Outputs:
- specs/INC-002-01.md (new; authoritative for the amended silver schemas and rules)
- specs/INC-002.md (header note pointing to the amendment; body unchanged)
- src/wearable_pipeline/normalize.py (stubs: `parse_integer`, `parse_iso_date`)
- tests/test_silver_normalization.py (new: 35 unit cases + pipeline-level tests)
- tests/test_walking_skeleton.py (edited only where it asserted removed columns, the old rule list or the old out-of-window reason; no assertion weakened)
- reports/INC-002-01/planner.md

Checks:
- `python -m pytest -q`: FAIL as intended (55 failed, 81 passed). 35 failures are `NotImplementedError` from the `normalize` stubs; the remaining 20 are assertion failures because the current pipeline still has the old schema, rule list and reason text. No import or collection errors; `tests/test_orchestrator.py` passes.
- Harness red gate (non-zero exit and `NotImplementedError` in output): satisfied.
- `data/` read or modified while planning: NO (synthetic fixtures only).
- Dependency changes: NONE. Commits made: NONE.

Findings:
- The INC-002 pipeline already exists, so a stub-only red baseline is impossible for pipeline-level behavior. The two pure normalizers were declared as a new stub seam so unit tests fail genuinely with `NotImplementedError`; pipeline-level tests fail on their assertions, which is stated rather than disguised.
- Four new pipeline-level tests pass before implementation by design (regression guards: quality/gold shape unchanged, invalid-integer participant still enrolled, no survey row dropped, rerun determinism). They must stay green.
- Planner decisions for the human to ratify:
  1. New rule `PARTICIPANTS_INTEGER_INVALID` (withhold_field, medium): `age, height_cm or max_heart_rate is not an integer`; one failed row regardless of how many fields fail.
  2. New rule `SURVEY_DATE_UNPARSEABLE` (exclude_from_output, medium): `date is not a valid YYYY-MM-DD calendar date`. `SURVEY_DATE_OUT_OF_WINDOW` keeps its id, now only for valid dates, reason `date is outside the study window`.
  3. `checked_count` is all source rows of the source for every rule, including both date rules.
  4. `is_out_of_window` is false when the date is unparseable; unparseable rows are identified by NULL `local_date` plus the rule flag (no new silver column).
  5. Integers accept optional sign and ASCII digits after trimming and must fit signed 32-bit; overflow, decimals and non-ASCII digits are invalid. Dates accept only trimmed ASCII `YYYY-MM-DD` real calendar dates (compact, week, slash and time-bearing forms are rejected).
  6. `INC-002` spec is amended by reference (header note) rather than rewritten, to keep one source of truth.
- `notebooks/INC-002-review.ipynb` update remains a human step after implementation, as the intent states.

Next action:
Route to the planning reviewer for INC-002-01.
