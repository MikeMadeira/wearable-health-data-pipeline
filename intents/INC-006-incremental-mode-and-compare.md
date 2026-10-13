# INC-006: Incremental Mode and Output Compare

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-005
(`intents/INC-005-heart-rate-silver-and-gold.md`); uses the approved G4 decision in
`reports/INC-001/profiling-findings.md` (Section 7, item 9): incremental processing by
affected participant-day with an idempotent MERGE on (`participant_id`, `local_date`),
detecting changed source inputs by file manifest and checksum.

## Goal

The pipeline can run in an incremental mode that reprocesses only what changed in the raw
inputs and brings `gold_clinical_daily` to exactly the state a full rebuild on the same
inputs would give. A newly arrived or corrected day for any participant is processed on its
own and reaches gold. A compare command checks that two output directories are logically
identical, so incremental versus full and run versus rerun equivalence can be verified.

## Boundary

One code slice: the incremental mode for the daily path, the shared incremental framework
and the compare command. Weekly and cohort tables, the container and the documentation are
separate increments.

## Requirements

### Incremental mode

- The existing `run` command gains an incremental mode selected by a flag (for example
  `--mode incremental`, default `full`). Existing arguments and the full-mode behavior are
  unchanged. The planner chooses the final flag name and states it in the spec.
- Change detection: the current `raw_manifest` (relative path and SHA-256) is compared with
  the manifest stored in the existing output database. Each path is new, changed, removed or
  unchanged.
- Silver recompute: only changed or new raw files are re-parsed; unchanged files are not
  re-read. The silver rows of the affected participants for the affected source are
  replaced with the freshly parsed rows. Removed files remove their silver rows.
- Gold recompute: `gold_clinical_daily` is recomputed and MERGEd only for participant-days
  whose silver rows changed, found by comparing the previous and the new silver rows per
  participant-day. A corrected single record changes exactly one gold row. A day that
  disappears with a removed file or removed record gets the documented values for a day
  without records (NULL aggregates, counts 0, presence false, NULL flags). The staged row
  set stays enrolled participants x study-window days.
- Changed shared inputs: a change to `participants.csv`, the wellness survey, device
  metadata or any other input that feeds all days triggers a recompute of everything it
  affects; the simplest correct approach (for example a full rebuild of the affected
  layers) is acceptable.
- Quality outputs: after an incremental run `quality_results`, `quality_rejects`, the
  quality report and `run_id` equal what a full rebuild on the same inputs produces. Counts
  are global, not per delta.
- No prior output: if there is no output database, or it has no stored manifest or an older
  schema, the run falls back to a full rebuild and says so.
- A failed incremental run leaves the previous output unchanged (a single transaction).
- A no-op incremental run (nothing changed) changes no table, no report byte and no run id.
- The framework has a documented extension point so the later gold tables (INC-007 and
  INC-008) can plug into it. They are not implemented here.

### Compare command

- A new subcommand takes two output directories. It exits 0 when they are identical and 1
  when they differ or one cannot be read.
- Identical means: every table has the same rows (as a multiset, independent of order) and
  the same columns, and the quality report files are byte-identical.
- It compares all tables of the two databases (`raw_manifest`, `silver_*`, `quality_*`,
  `gold_*`), discovered dynamically so later tables are covered without code changes. It is
  read-only and never modifies either output.
- It prints which tables and columns differ with row counts only, and never prints raw
  participant values.

## Constraints

- Do not change: existing tables' columns and values, `gold_clinical_daily` key and MERGE
  behavior, `run_id` derivation, the existing `run` arguments and default (full) behavior,
  public function signatures, the harness (`scripts/`, `.github/`), `data/`.
- No new runtime dependency: standard library plus DuckDB.
- Existing tests and spec text that assert the CLI surface or a full-rebuild-only behavior
  are updated by this increment. No other test is weakened.
- Out of scope:
  - weekly and cohort incrementality;
  - scheduling, streaming and file watchers;
  - container and documentation changes;
  - new quality rules or new gold columns;
  - detecting late arrivals from the data (no file carries arrival timestamps);
  - a partition unit other than the participant-day;
  - performance targets beyond recording timings.

## Verification Expectations

Automated tests on small synthetic fixtures cover:

- incremental output equal to a full rebuild after: a new day appended, one record
  corrected, a record or a file removed, a change to the participants or survey input, and a
  new participant file;
- unchanged files not being re-read;
- only the affected gold rows changing, and a corrected record changing exactly one gold row;
- a no-op incremental rerun leaving every table, the report and the run id unchanged;
- fallback to a full rebuild with no prior database and with an old-schema database;
- a failed incremental run leaving the previous output unchanged;
- the default mode remaining full and the existing arguments unchanged;
- the compare command: identical outputs, differing rows, differing columns, differing
  report bytes, a missing table, an unreadable directory, the exit codes, no modification of
  either output, and no raw values printed;
- deterministic outputs.

Harness gates:

- `python -m pytest -q` passes.
- Changes made by the orchestrated phases are confined to `src/`, `tests/`, `specs/` and
  `reports/INC-006/`.

Real-data evidence, run by the human on a copy of `data/` (never on `data/` itself) and
recorded in the implementation report:

- a full run, then an incremental run with no change, which is a no-op;
- a copy with one participant's file changed (one record corrected): the incremental
  output compared with a full rebuild of the same copy using the compare command is
  identical, and only that day's gold row differs from the baseline;
- a copy with an extra day or a new file: the same comparison is identical;
- timings of the full and the incremental runs are recorded;
- `data/` SHA-256 unchanged.

Human step after implementation: a new review notebook (`notebooks/INC-006-review.ipynb`)
is created and committed with its outputs cleared, as for `notebooks/INC-003-review.ipynb`
to `notebooks/INC-005-review.ipynb`. The orchestrator's implementation phase may not edit
`notebooks/`.
