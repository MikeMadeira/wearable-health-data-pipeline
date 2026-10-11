# INC-001: Data Profiling Notebooks

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `notebook`. First slice;
precedes all other slices.

## Goal

A reproducible set of profiling notebooks and one findings document that evidence the
observed defects in each raw source, and propose rules, grain, timezone, week, cohort and
freshness options, so the human can approve gate G1. The increment proposes; it decides
nothing.

## Requirements

Notebooks, one per source family, under `notebooks/INC-001-*.ipynb`:

1. Tabular sources (`participants.csv`, `device_metadata.csv`, `wellness_survey.csv`).
2. Sleep (`sleep.json`).
3. Steps and heart rate (`steps.json`, `heart_rate.json`).
4. Cross-source and design options.

Checks to run. Data-dictionary statements are hints to verify, not known defects:

- **Tabular:** row counts and key uniqueness; nulls and blanks (for example
  `firmware_version` vs `device_label`); value domains and ranges (survey score ranges,
  chronotype A/B, gender); participant/device linkage and orphans; survey dates vs the
  study window and per-participant day coverage.
- **Sleep:** sessions per participant vs 28 expected; duplicates; onset/end ordering; stage
  order and durations vs timestamps (whole minutes); sum of stages vs session span;
  efficiency and restlessness ranges; wake-date vs timestamp alignment.
- **Steps and heart rate:** records per participant vs 40,320; duplicates and gaps on the
  minute grid; timestamp formats including UTC `Z` vs naive and non-zero seconds; value
  ranges (steps; 40-200 bpm); stuck-sensor runs in heart rate; `participant_id`
  consistency with the folder.
- **Cross-source:** join coverage between sources; study-window bounds; day-assignment
  options for sleep vs minute data under UTC-05:00; completeness per participant-day.
- Record per-minute JSON read/parse time and total notebook runtime.

Findings document `reports/INC-001/profiling-findings.md`:

- Per finding: source, rule or check, count or rate with a query or cell reference,
  example identifiers (no raw rows), severity, and a proposed handling option (fail /
  quarantine / flag-and-keep / exclude) marked as a proposal.
- Observed defects are kept separate from assumptions. Checks that found nothing are
  reported too.
- A G1 decision list: quality policy, timezone, day assignment, daily grain, walking-
  skeleton source, and options for week boundary, weekly schema, cohort definition and
  daily freshness.

## Constraints

- No new dependencies: standard library plus installed dev packages only. No pandas or
  DuckDB (DuckDB arrives in INC-002).
- Must run top to bottom on a laptop in reasonable time; process per-participant files in
  a streaming style rather than loading all 50 into memory.
- Changes only under `notebooks/` and `reports/`. Raw `data/` is read-only and unchanged.
- Notebooks store no outputs. Findings contain aggregates, counts and participant/record
  identifiers only, never raw measurement rows. No derived data files are committed.
- Out of scope: pipeline code under `src/`, specs, tests, any change to `data/`, writing
  derived data files, deciding any rule, CLI, DuckDB, `gold_participant_devices`.

## Verification Expectations

Automated:

- Each notebook is valid `.ipynb` JSON and executes top to bottom via nbclient.
- `data/` hashes are unchanged after execution.
- `python -m pytest -q` still passes.
- No changes outside `reports/` and `notebooks/`.

Reviewer content checks:

- Every finding cites a cell or query and a count.
- All four check groups are addressed, including checks that found nothing.
- Observed defects are separated from assumptions; every rule or handling is marked a
  proposal.
- Runtime is recorded; no raw rows appear in the findings.

Human gate (G1): the human approves by recording decisions on the decision list (quality
policy, timezone, day assignment, daily grain, skeleton source); unresolved items are
listed explicitly.
