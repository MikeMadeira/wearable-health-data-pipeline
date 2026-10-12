# Implementation Review Report: INC-003

Status: APPROVED

Inputs:
- intents/INC-003-sleep-silver-and-gold.md, specs/INC-003.md
- reports/INC-003/implementation.md (including "Review repair"), reports/INC-003/planning-review.md
- src/wearable_pipeline/sleep.py, pipeline.py, normalize.py (git diff vs HEAD)
- tests/ (read-only)

Outputs:
- reports/INC-003/implementation-review.md (replaces the earlier REJECTED review)

Checks:
- `python -m pytest -q`: PASS (302 passed)
- `git diff HEAD` on tests/, specs/, intents/, requirements/, pyproject.toml, uv.lock, Dockerfile: PASS (no changes; only src/ modified, plus reports)
- Prior blocker (INTEGER overflow raising on in-object defects): PASS (resolved; see probes)
- Prior minors (unused `position` param / `None` placeholder in `_stage`, over-long gold-insert line): PASS (resolved)
- Synthetic probes in /tmp only (real data/ not read or modified): PASS
  - duration 2147483647 accepted; 2147483648, 3e9, 10**30, negative, 0, None, 1e400 (float) treated as invalid duration -> SLEEP_STAGE_INVALID, no crash
  - two stages of 2000000000 (sum > INT max): run completes, day total/deep%/efficiency NULL
  - 1 light + two awake of 2147483647 (total > INT max, asleep small): completes, efficiency ~2e-8 (DOUBLE, no overflow)
  - efficiency_pct 1e400 / 10**40, restlessness -10**40: invalid fields, NULL, flagged, no crash
  - deeply nested junk stage elements and 300-level nested `stages`: no crash
  - duplicate exact (kept) and conflicting (all three NULL) sessions per day: correct
  - rerun idempotency: all gold, silver sleep and quality tables identical across reruns
  - old-schema gold DB (sleep columns dropped) upgrades via ADD COLUMN IF NOT EXISTS; existing columns unchanged, sleep columns defaulted (NULL,NULL,NULL,0,false,NULL)
- compute_sleep_metrics vs build_sleep_layer consistency: PASS (build_sleep_layer calls compute_sleep_metrics; `_stage` bounds duration to 1..2147483647 and compute_sleep_metrics withholds on asleep sum > 2147483647)

Findings:
- Blocker resolved: no input in the probes makes the INTEGER gold/silver columns raise; overflow is withheld (NULL) instead.
- Implementation-decision needing HUMAN RATIFICATION (not a rejection): specs/INC-003.md is silent on an upper bound for `duration_min` (spec section on stage fields: "integer at least 1") and on summed-minute overflow. The implementer treats duration > 2147483647 as invalid (SLEEP_STAGE_INVALID, no new rule id) and withholds total/deep%/efficiency when the non-awake sum exceeds 2147483647. This is a narrow deviation from the literal spec text (a 2147483648 duration is "valid" by the spec), consistent with the repo's 32-bit `parse_integer` convention and does not contradict any stated outcome. Ratify it or have the planner add it to the spec. Note: when only the awake/total sum overflows, efficiency is still computed (DOUBLE), which is consistent with the spec formula.
- Code quality: no dead code found; reading, building and persistence are separated (sleep.py pure, I/O in pipeline.py); sleep files are validated before any table is touched.
- Still pending (human step, unchanged from implementation.md): run on the real data and record evidence (1,400 gold rows; 1,400 sessions / 7,000 stages; S-01 4, S-02 3, S-03 3, S-05 1,379; S-04 count; efficiency range; existing gold columns unchanged; rerun identical; data/ unchanged). I did not run or read data/.

Next action:
Orchestrator: record the ratification request above for the human, have the human run the pipeline on the real data and record the evidence, then proceed to the human gate/commit. No implementer rework required.
