# Vertical-Slice Proposal: INIT-001 Wearable Health Data Pipeline

Source intent: `intents/INIT-001-wearable-health-pipeline.md`
Revision: 4 (addresses revision-3 review: HX-1 covers `validate_repair_paths`; INC-009/INC-010 checks clarified)

## 1. Initiative summary and assumptions

Deliver a containerized, reproducible, idempotent Python + DuckDB pipeline: immutable raw
exports -> bronze -> silver (validated source of truth) -> gold (daily clinical, weekly
biostatistics, chronotype cohort), with persisted quality outputs, full and incremental
run modes, and README/diagram/query output/AI disclosure.

Assumptions (taken from the intent and `data/data_dictionary.md` only; **no raw records
were inspected while planning**):

- A1. Raw inputs are those in the data dictionary (participants CSV, device metadata CSV,
  wellness survey CSV, per-participant `sleep.json`, `steps.json`, `heart_rate.json`).
  Defects the dictionary hints at (UTC `Z` heart-rate timestamps, stuck sensors, blank
  firmware, stage duration vs timestamp mismatch) are *hints to verify*, not findings.
- A2. Grain, day assignment, week boundary, timezone, weekly schema, quality-failure
  policy, cohort definition and the daily-freshness mechanism are **human decisions**
  taken at gates after profiling. Slices must implement them as approved, not pre-empt them.
- A3. `gold_participant_devices` is out of scope per the intent. `device_metadata.csv` is
  landed in bronze (the intent lands all raw exports) but gets no silver or gold use unless
  profiling shows a kept output needs it.
- A4. Local storage format (Parquet vs DuckDB tables) is decided at G4; criterion:
  closeness to Iceberg semantics (atomic, idempotent MERGE upserts).
- A5. Slice IDs are proposed; increment intents are authored by the human later.
- A6. Sizing (about 40,320 per-minute records per participant per file, 50 participants)
  comes from the data dictionary and is an estimate to be verified by INC-001.

Human gates:

- G1: after INC-001: approve profiling findings, quality-failure policy, timezone, day
  assignment, daily grain, and which source the walking skeleton uses.
- G4: during INC-002 review: approve storage format and daily-freshness mechanism.
- G2: before INC-007: approve week boundary and weekly schema.
- G3: before INC-008: approve chronotype cohort definition and metrics.

(Gate numbers are labels, not an order.)

## 2. Current capabilities and gaps

Present: harness (`scripts/orchestrate_increment.py`, agents, skills); `uv.lock`/pyenv
setup; notebook tooling (`ipykernel`, `nbclient`, `nbformat` in the `dev` extra, kernel
registered by the Dockerfile); a Dockerfile for the test/development image; harness tests;
a README with a *proposed* architecture. `src/wearable_pipeline/` holds only an empty
`__init__.py`; `specs/`, `output/` and `notebooks/` hold no pipeline content.

Gaps: DuckDB dependency (not yet declared; `dependencies = []`), CLI, ingestion, layers,
quality checks, gold outputs, incremental mode, pipeline container, final documentation and
query output.

Harness constraints that shape the order (from `validate_phase_paths` and its callers in
`scripts/orchestrate_increment.py`):

- A `notebook` increment may only change `reports/` and `notebooks/`, so it cannot add
  dependencies. Code increments may change `pyproject.toml` and `uv.lock` during planning.
- A `code` implementation may only change `reports/` and `src/`; an `environment`
  implementation may additionally change only `Dockerfile`, `pyproject.toml` and `uv.lock`.
  Neither may change `README.md` or a top-level `queries/` directory.
- Human decision (this revision): extend the harness path rules for INC-009 and INC-010
  rather than contort the slices. That change is prerequisite **HX-1** below.

## 3. Naming convention and layout (proposed, pending G1/G4)

Under `output/` (ignored by Git): `bronze/`, `silver/`, `gold/`, `quality/`, plus a
human-readable `quality/report.md`. Table names:

| Layer | Tables |
|-------|--------|
| bronze | `bronze_participants`, `bronze_device_metadata`, `bronze_wellness_survey`, `bronze_sleep`, `bronze_steps`, `bronze_heart_rate` (raw values plus source file and load metadata) |
| silver | `silver_participants`, `silver_wellness_survey`, `silver_sleep_sessions`, `silver_sleep_stages`, `silver_steps_minute`, `silver_heart_rate_minute` |
| quality | `quality_results` (rule, source, counts, outcome per run), `quality_rejects` (quarantined or excluded rows with reason and raw value) |
| gold | `gold_clinical_daily`, `gold_biostat_weekly`, `gold_chronotype_cohort` |

Keys and grains below are proposed and finalized at the named gates.

## 4. Ordered slice table

| ID | Kind | Outcome | Depends on | Verification |
|----|------|---------|-----------|--------------|
| INC-001 | notebook | Profiling notebook(s) plus findings document: observed defects with evidence, proposed rules, grain/key/timezone/week/cohort/freshness proposals; no pipeline code | none | Notebook runs top to bottom (nbclient); `data/` hashes unchanged; committed outputs aggregates only; every finding cites a query/count; human approval at G1 |
| INC-002 | code | Walking skeleton: one CLI command lands all raw exports in bronze, builds silver for the thin source(s) chosen at G1, writes quality tables and report, produces `gold_clinical_daily` with the first columns via MERGE; adds DuckDB | INC-001 (G1) | Tests for grain/keys, quality rows, rerun idempotency, raw unchanged, bronze completeness; run on real data |
| INC-003 | code | Sleep end to end: silver sleep sessions/stages, derived metrics, sleep columns in `gold_clinical_daily` | INC-002 | Day assignment, stage/duration rules, derived metrics, additive-column check, rerun and corrected-input tests |
| INC-004 | code | Steps end to end: silver per-minute steps, step columns in `gold_clinical_daily` | INC-002 | Grid/duplicate/range rules per approved policy, daily aggregates, additive-column check, rerun and corrected-input tests |
| INC-005 | code | Heart rate end to end: silver per-minute HR with timestamp normalization and stuck-sensor flagging, HR columns in `gold_clinical_daily` | INC-002 | Naive/UTC handling, stuck-run flagging with raw preserved, flagged rows excluded from aggregates, additive-column check, rerun and corrected-input tests |
| INC-006 | code | Incremental run mode and daily freshness for `gold_clinical_daily`: process a newly arrived day, recompute only affected partitions | INC-003, INC-004, INC-005 (G4) | Incremental equals full rebuild on fixtures; new day appears alone; corrected input updates rows without duplicates; no-op rerun |
| INC-007 | code | `gold_biostat_weekly`: distinct schema, derived from silver, with its own rerun/correction and incremental support | INC-005, INC-006 (G2) | Week boundary, partial weeks, grain/key, schema differs from daily, consistency with daily totals, incremental equals full |
| INC-008 | code | `gold_chronotype_cohort` using sleep and activity, with its own rerun/correction and incremental support | INC-003, INC-004, INC-006 (G3) | Cohort assignment, participants join, metric correctness on fixtures, incremental equals full |
| HX-1 | human-owned harness change (not an orchestrated slice) | Extend both `validate_phase_paths` and `validate_repair_paths` so `environment` increments may also change `.dockerignore` and `docker-compose.yml`, and `code` increments may also change `README.md` and `queries/`; update `tests/test_orchestrator.py` | none; must be merged before INC-009 | `python -m pytest -q` passes including new path-rule tests for both functions; other paths still rejected |
| INC-009 | environment | Containerized end-to-end run from a clean checkout; two runs reproduce identical outputs; full vs incremental equivalence across all gold tables | INC-007, INC-008, HX-1 | Harness: image build and in-container `pytest -q`. Human-run evidence in the implementation report: one-command real-data run, read-only raw mount, CLI compare command on two runs and full vs incremental |
| INC-010 | code | Documentation and query output: README run guide, diagram, `queries/` SQL file with a verifying test, captured output, AI disclosure | INC-009, HX-1 | Test runs the SQL on a fixture gold dataset and matches expected results; real-data output recorded and rerun by a human; README commands verified; diagram labels reviewed |

## 5. Slice scope, contracts and boundaries

Contract lines list inputs, outputs, and key/grain, all "proposed, pending gates".

### INC-001: Data profiling notebook (first slice)
- Scope: profile every source for row counts, keys, duplicates, nulls, formats, ranges,
  timestamp formats/timezones, grid gaps, cross-file consistency (stage durations vs
  timestamps, device/participant linkage), stuck sensors, orphans. The findings document
  proposes rules and a quality-failure behavior per defect class (fail / quarantine /
  flag-and-keep / exclude), timezone, day assignment, daily grain, week-boundary and
  weekly-schema options, cohort definitions, freshness options, and recommends the
  walking-skeleton source (including whether the survey is daily-grain per participant).
- Constraint: no new dependencies; profile with the standard library and installed dev
  packages, because the notebook kind cannot change `pyproject.toml`. DuckDB is added in
  INC-002 and is available to INC-002 onwards.
- Contract: in `data/` (read-only) -> out `notebooks/*.ipynb`, `reports/INC-001/` findings.
  Committed outputs contain aggregates only, no raw participant rows.
- Out of scope: pipeline code, deciding any rule, CLI.

### INC-002: Walking skeleton
- Scope: CLI (single command, full-rebuild mode) introduced here with behavior; DuckDB
  runtime dependency added and justified (embedded SQL engine with MERGE, adequate for the
  data size); bronze landing of all six raw sources including `device_metadata` (no mutation
  of raw); silver for the source(s) chosen at G1; `quality_results`, `quality_rejects`, and
  report; `gold_clinical_daily` with the first columns via MERGE on
  (`participant_id`, `local_date`). If the survey is not confirmed daily-grain at G1, the
  thin source is another one chosen at G1. Storage format selected (G4).
- Contract: `data/**` -> `output/bronze|silver|quality|gold`; key proposed
  (`participant_id`, `local_date`) in `gold_clinical_daily`.
- Out of scope: other sources' silver, weekly/cohort, incremental mode, device metadata
  silver/gold.

### INC-003 / INC-004 / INC-005: Sleep, steps, heart rate
- Each adds one source's silver table(s) and its columns to `gold_clinical_daily`, with its
  quality rules and flagged/quarantined rows.
- `gold_clinical_daily` growth contract: it is rebuilt from per-source daily aggregates
  from silver, so each slice adds one source's aggregate and its columns. Existing key and
  existing columns are unchanged; days with no data for the new source hold NULL. Each
  slice tests that prior columns and row keys are unchanged, and the MERGE updates only
  that source's columns on matched keys.
- Each slice owns the rerun, corrected-input and idempotency tests for its own silver and
  gold columns.
- Contracts: INC-003 `bronze_sleep` -> `silver_sleep_sessions` (key participant, wake
  date), `silver_sleep_stages`; INC-004 `bronze_steps` -> `silver_steps_minute` (key
  participant, minute timestamp); INC-005 `bronze_heart_rate` -> `silver_heart_rate_minute`
  (same key, after timestamp normalization).
- Out of scope: other sources, weekly/cohort, incremental mode, cross-source derived
  metrics beyond those approved at G1.

### INC-006: Incremental mode and daily freshness
- Scope: incremental run mode (CLI flag) for the daily path, plus a CLI compare command
  that checks two output sets are identical (used for two-run and full-vs-incremental
  equivalence, and reused by INC-009); maps the "daily freshness"
  requirement to: a newly arrived day for any participant is processed on its own and
  reaches `gold_clinical_daily`. The partition unit follows the G4 decision. Owns the shared
  incremental framework; later gold slices plug into it.
- Contract: raw files with a new/corrected day -> updated affected `gold_clinical_daily`
  keys only.
- Out of scope: weekly/cohort incrementality (owned by INC-007/008), scheduling, streaming.

### INC-007: Weekly biostatistics
- Scope: weekly table derived from silver (not from `gold_clinical_daily` unless G2
  approves), approved grain, boundary, partial-week handling and metrics; distinct schema
  and naming; MERGE upsert, rerun/correction tests and incremental support through INC-006.
- Contract: silver -> `gold_biostat_weekly`, key proposed (`participant_id`, week) pending G2.
- Out of scope: cohort analysis, container, docs.

### INC-008: Chronotype cohort analysis
- Scope: cohort table joining participants' chronotype to sleep and activity metrics;
  approved metrics only; MERGE, rerun/correction tests and incremental support.
- Contract: `silver_participants` + silver sleep/steps -> `gold_chronotype_cohort`; grain
  proposed per cohort (and optionally participant) pending G3.
- Out of scope: statistical modeling/ML, README output.

### HX-1: Harness path-rule extension (prerequisite, human-owned)
- Scope: small, additive change to `scripts/orchestrate_increment.py` and
  `tests/test_orchestrator.py`, as approved by the human, covering **both** path
  allowlists: `validate_phase_paths` (implementation phase) and `validate_repair_paths`
  (recovery phases, including the environment and implementation repair allowances).
  Allow `.dockerignore` and `docker-compose.yml` for `environment` increments, and
  `README.md` and `queries/` for `code` increments, in both functions. Each function gets
  a test for the new paths and a test that other paths remain rejected. Authored and
  committed by the human outside the slicing workflow, since the harness runner cannot
  modify itself; no rule changes beyond these allowances.
- Out of scope: any pipeline behavior, other harness redesign.

### INC-009: Containerized end-to-end run
- Scope: the pipeline entry point is the existing CLI in `src/` (INC-002/006); the compare
  command that checks two runs and full vs incremental equivalence is introduced in INC-006.
  INC-009 changes only `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `pyproject.toml`
  and `uv.lock`, so that the image runs the full pipeline against mounted `data/`
  (read-only) and writes to mounted `output/`; README run text is written in INC-010.
- Checks: the harness enforces only the image build and `pytest -q` inside the container
  (`run_docker_tests`). The real-data container run, the read-only mount and the two-run
  and full-vs-incremental compare are human-run evidence, recorded in the INC-009
  implementation report with the commands and outcomes.
- Out of scope: cloud deployment, image publishing, new pipeline behavior, README edits.

### INC-010: Documentation and query output
- Kind: `code` (requires HX-1 so `README.md` and `queries/` are authorized paths).
- Scope: README sections required by the intent; architecture diagram of layers, data flow,
  quality handling and local-to-cloud mapping labelled implemented vs proposed; `queries/`
  SQL against gold only, with captured output (>=3 metrics incl. a chronotype comparison);
  DuckDB vs Spark and rejected alternatives; Iceberg as cloud proposal; AI disclosure
  reflecting actual use. The SQL is verified by a test under `tests/` that builds a small
  gold fixture (since `output/` is Git-ignored and absent in a clean checkout), reruns the
  SQL on it and compares with expected results. The output captured from the real data in
  the README is human-verified by rerunning the SQL on a local pipeline run, recorded in
  the implementation report.
- Out of scope: new pipeline behavior.

## 6. Recommended first slice

INC-001, the profiling notebook, as the intent requires. It is the smallest increment that
proves value: it needs no dependency change, is verified by executing top to bottom with
`data/` unchanged, and produces the evidence that gate G1 needs before any schema or rule
is fixed. DuckDB is deliberately deferred to INC-002 because the notebook kind cannot edit
dependency files. If the human wants DuckDB available for profiling, that is a deviation
requiring a separate environment increment before INC-001, to be approved explicitly.

## 7. Risks, unresolved questions, and deferred work

Risks:
- Profiling may change grain or require splitting a slice (for example INC-005); revisit
  the plan at G1.
- INC-006 plus INC-007/008 incremental support share one framework; if INC-006 grows, split
  by partition type.
- Per-minute JSON parsing speed is unmeasured; INC-001 should record it.
- Harness path ownership: HX-1 must land before INC-009/INC-010, otherwise the
  orchestrator stops on unauthorized paths. INC-001's findings go to `reports/INC-001/`,
  the work-item report directory the orchestrator allows for notebook planning and
  implementation; to be confirmed when the human writes that intent.

Unresolved (human decisions): quality-failure policy and run-failing thresholds; timezone
and day assignment; daily grain and walking-skeleton source; daily-freshness mechanism
and partition unit; week boundary and weekly schema; cohort definition; storage format;
final confirmation that INC-010 is `code` kind. Decided by the human: extend harness
path rules (HX-1) for INC-009 and INC-010.

Deferred / out of scope for the initiative: `gold_participant_devices`, AWS deployment,
streaming, BI dashboards, ML modeling, Spark (documented as a future option only).
