# Planner Report: INC-001

Status: READY
Inputs:
- intents/INC-001-data-profiling-notebooks.md (controlling scope)
- intents/INIT-001-wearable-health-pipeline.md
- plans/INIT-001-wearable-health-pipeline/vertical-slices.md (approved, revision 4)
- AGENTS.md
- .github/skills/sdd-tdd/SKILL.md
- .github/skills/agent-communication/SKILL.md
- scripts/orchestrate_increment.py (notebook validation and path rules)
- data/data_dictionary.md

Outputs:
- reports/INC-001/planner.md

Checks:
- `python -m pytest -q` baseline: PASS (29 passed; no red baseline applies to a notebook increment)
- nbclient, nbformat, ipykernel importable: PASS
- Spec, tests, stubs, notebooks or source created by planning: NONE (by design)
- Raw records inspected while planning: NOT RUN

Findings:
- No spec, tests or stub apply; the increment intent excludes pipeline code and tests.
  Verification is the acceptance checklist below, matching the notebook gate in the
  sdd-tdd skill.
- Harness limits: implementation may change only `reports/` and `notebooks/`; no
  dependency changes; `data/` hashes must be unchanged; notebooks are executed by
  nbclient and outputs are not stored.
- Plan-level reminder: DuckDB is not available here; profiling uses the standard library.

## Deliverables

- `notebooks/INC-001-tabular.ipynb`: participants, device metadata, wellness survey.
- `notebooks/INC-001-sleep.ipynb`: sleep sessions and stages.
- `notebooks/INC-001-steps-heart-rate.ipynb`: per-minute steps and heart rate.
- `notebooks/INC-001-cross-source.ipynb`: join coverage, study window, day-assignment
  and design options.
- `reports/INC-001/profiling-findings.md`: findings and G1 decision list.

(Notebook file names are the proposed `INC-001-*` pattern from the intent; the
implementer may adjust the suffixes.)

## Notebook acceptance checklist

Format and execution (harness-enforced):
- [ ] Every notebook is valid `.ipynb` JSON with a `cells` array.
- [ ] Every markdown cell has `metadata.language = "markdown"`; every code cell has
      `metadata.language = "python"`.
- [ ] Any `metadata.id` is a unique non-empty string; `source` is a string or list of strings.
- [ ] The kernel is `wearable-health-data-pipeline`, and every notebook executes top to
      bottom with nbclient without error.
- [ ] No cell outputs or execution counts are stored in committed notebooks.
- [ ] SHA-256 hashes of every file under `data/` are unchanged after execution.
- [ ] Only `notebooks/` and `reports/INC-001/` paths changed; `pyproject.toml`, `uv.lock`,
      `src/`, `tests/` and `specs/` are untouched.
- [ ] `python -m pytest -q` still passes (29 tests at planning time).

Constraints (reviewer-checked):
- [ ] Only the standard library and installed dev packages are imported; no pandas or DuckDB.
- [ ] Per-participant files are read one at a time (streaming style); no cell loads all 50
      participants' minute data into memory at once.
- [ ] No file is written under `data/`, `output/` or elsewhere; no derived data file is
      created or committed.
- [ ] Notebooks print aggregates, counts and identifiers only, never raw measurement rows.
- [ ] Per-minute JSON read/parse time and total notebook runtime are measured and
      reported in the findings.

Coverage, tabular notebook:
- [ ] Row counts and key uniqueness for each of the three tables.
- [ ] Nulls/blanks, including `firmware_version` vs `device_label`.
- [ ] Value domains and ranges: survey scores, chronotype A/B, gender.
- [ ] Participant/device linkage and orphans.
- [ ] Survey dates vs the study window; per-participant day coverage.

Coverage, sleep notebook:
- [ ] Sessions per participant vs 28 expected; duplicates.
- [ ] Onset/end ordering; stage order; stage `duration_min` vs timestamps (whole minutes);
      sum of stages vs session span.
- [ ] Efficiency and restlessness ranges; wake-date vs timestamp alignment.

Coverage, steps and heart-rate notebook:
- [ ] Records per participant vs 40,320; duplicates and gaps on the minute grid.
- [ ] Timestamp formats including UTC `Z` vs naive, and non-zero seconds.
- [ ] Value ranges (steps; 40-200 bpm).
- [ ] Stuck-sensor runs in heart rate.
- [ ] `participant_id` consistency with the folder name.

Coverage, cross-source notebook:
- [ ] Join coverage between sources and study-window bounds.
- [ ] Day-assignment options for sleep vs minute data under UTC-05:00.
- [ ] Completeness per participant-day.

Findings document `reports/INC-001/profiling-findings.md`:
- [ ] Per finding: source, rule/check, count or rate, cell/query reference, example
      identifiers (no raw rows), severity, proposed handling (fail / quarantine /
      flag-and-keep / exclude) marked "proposal".
- [ ] Observed defects separated from assumptions; checks that found nothing are listed.
- [ ] Runtime and parse-time measurements recorded.
- [ ] G1 decision list: quality policy, timezone, day assignment, daily grain, walking-
      skeleton source, plus options for week boundary, weekly schema, cohort definition,
      daily freshness. Unresolved items are listed explicitly.
- [ ] No rule is stated as decided; every handling is a proposal for the human.

## Verification approach

1. Implementation phase: the orchestrator validates notebook JSON and cell metadata,
   executes each changed notebook with nbclient, and compares `data/` hashes before and
   after.
2. Reviewer reads the findings document against the checklist; spot-checks that each
   finding's cell reference and count match the executed notebook.
3. Run `python -m pytest -q` after implementation.
4. The human approves gate G1 from the decision list; this is outside automated checks.

Next action:
Route to the planning reviewer to review the notebook checklist in reports/INC-001/planner.md against intents/INC-001-data-profiling-notebooks.md (no spec, tests or stub should exist).
