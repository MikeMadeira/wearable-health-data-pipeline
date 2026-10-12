# Planner Report: INC-002

Status: READY
Inputs:
- intents/INC-002-walking-skeleton.md (controlling scope)
- intents/INIT-001-wearable-health-pipeline.md
- plans/INIT-001-wearable-health-pipeline/vertical-slices.md (approved, revision 4)
- reports/INC-001/profiling-findings.md (G1–G4 decisions, section 7)
- AGENTS.md, .github/skills/sdd-tdd/SKILL.md, .github/skills/agent-communication/SKILL.md
- scripts/orchestrate_increment.py (planning gate and path rules)

Outputs:
- specs/INC-002.md
- src/wearable_pipeline/pipeline.py (stub: `run_pipeline`, `RunResult`, `PipelineError`)
- src/wearable_pipeline/cli.py (stub: `main`)
- src/wearable_pipeline/__main__.py (wiring only), src/wearable_pipeline/__init__.py (exports)
- tests/test_walking_skeleton.py (54 tests, synthetic fixtures, 3-day window)
- pyproject.toml, uv.lock (runtime dependency `duckdb>=1.4`; MERGE requires 1.4+)
- reports/INC-002/planner.md

Checks:
- `python -m pytest -q`: FAIL as intended (54 failed, 29 passed). All 54 new tests fail with `NotImplementedError` raised by the stubs; no collection, import or other errors; existing 29 harness tests pass.
- `uv lock --check --python 3.12.6`: PASS
- `data/` read or modified while planning: NO (synthetic fixtures only)
- Commits made: NONE

Findings:
- Tests assert behaviour (`assert run_pipeline(...) ...`); none uses `pytest.raises(NotImplementedError)`.
- Coverage: grain/keys of every table, survey→gold join, local-date window, rule results (checked/failed per rule, zero-failure rows), rejects with raw values, duplicate/orphan/invalid-score handling, MERGE idempotency and single-row update, retained stale gold rows, determinism of run_id and tables, report format, CLI exit codes, failure atomicity, raw files untouched.
- Planner-made decisions the human should ratify at review:
  1. Bronze is the unchanged raw tree plus a `raw_manifest` table (deviates from the plan's `bronze_*` tables).
  2. Duplicates are kept in silver and flagged, never dropped; conflicting scores are withheld per field.
  3. `run_id` = first 16 hex of SHA-256 over sorted `relative_path\tsha256` lines.
  4. Handling vocabulary `flag | withhold_field | exclude_from_output`; severity `high | medium`.
  5. `record_count` populated for CSV only (NULL for JSON/markdown).
  6. Gold rows no longer produced are retained (MERGE never truncates).
  7. DB at `output_dir/wearable.duckdb`, report at `output_dir/quality/report.md`.
- Open scope conflict (not resolved here): client brief requires `gold_participant_devices`; INIT-001 lists it out of scope.
- Plan/INIT wording on `bronze_*` tables should be updated by the human after ratification.

Human ratification (recorded after review):
- Decisions 1–7 accepted. Decision 1: INIT-001 and the vertical-slice plan updated to describe bronze as the raw tree plus `raw_manifest`.
- Decision 4 amended: `quality_results` gains a `reason` column (exact per-rule text in spec section 7; also used as `quality_rejects.reason`). Test `test_every_rule_carries_its_exact_reason_text` added; 
- Review gaps closed: report line `Manifest files: <n>` specified; added tests for `unclassified` source, report manifest count, gold column types and primary key, and exact reject reason for every rule. `intents/INC-002-walking-skeleton.md` deviation note updated. Baseline now 54 failed (all `NotImplementedError`), 29 passed.
- `gold_participant_devices` stays out of scope (no change to INIT-001).

Next action:
Route to the planning reviewer for INC-002.
