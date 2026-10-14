# INC-010: Documentation and Query Output

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `code`. Follows INC-009
(`intents/INC-009-containerized-run.md`) and the committed harness change HX-1, which
lets a `code` increment change `README.md` and `queries/`. This is the last increment of
the initiative.

## Goal

A technical reviewer can understand, run and assess the whole pipeline from `README.md`
alone. After this increment the repository contains: end-to-end run instructions, an
architecture description with diagrams, tested analytical SQL with captured output from a
real run, the key decisions and assumptions with their trade-offs, and an AI usage
disclosure. These are the delivery and acceptance criteria of the client brief
(`requirements/business_pipeline_requirements.md`) that earlier increments did not cover.

## Boundary

One `code` slice, kept together as the initiative plan proposed. It adds documentation,
the SQL under `queries/` and a small read-only query runner, with the tests that verify
them. No pipeline behavior changes.

## Requirements

### Run instructions

- The README explains how to run the pipeline end to end: the one-command compose run,
  incremental mode, `compare`, and the test suite. The commands are exactly the ones
  verified in the INC-009 evidence (`reports/INC-009/implementer.md`), including the
  note that `output/` must exist and be owned by the user.
- Existing setup and agent-workflow sections are preserved unless a fact requires a
  correction.

### Architecture diagrams and mapping

- Two Mermaid diagrams in the README. One shows the implemented local components and data
  flow. The other shows the proposed cloud equivalents and is labelled "proposed, not
  implemented".
- The local diagram shows: raw, bronze (`raw_manifest`), silver, and every named gold
  table; the flow to the daily clinical, weekly biostatistics and chronotype cohort
  outputs; quality flags, exclusions, run-failing checks and the quality report; full and
  incremental modes with run state; and the container's read-only data and writable
  output mounts.
- Each local component is paired with its proposed cloud equivalent (a mapping table or
  diagram labels). The proposed mapping is: raw/bronze/silver/gold to S3 prefixes and
  proposed Iceberg tables; the container run to an AWS Batch task; catalog and SQL access
  to Glue Catalog and Athena; scheduling to EventBridge and Step Functions. Every cloud
  component is labelled "proposed, not implemented".
- Iceberg is briefly justified, and Parquet without Iceberg and ECS are noted as
  alternatives. A legend distinguishes implemented from proposed.

### Analytical queries and captured output

- Each analytical query is a separate SQL file under `queries/`. All of them read only
  `gold_*` tables.
- **Chronotype comparison.** Rows for cohort A, cohort B and A minus B. For sleep total,
  sleep efficiency, deep-sleep percentage, steps and heart rate it reports the
  participant-level mean, the participant count and the standard deviation across
  participants, and includes valid-day counts. The difference is calculated from the two
  cohort means. No significance claim is made.
- **Study-wide metrics** from `gold_clinical_daily`: mean steps, total sleep and heart
  rate, each calculated as the mean of participant-level averages, with the participant
  count for each.
- The README combines the captured results of all queries in one section, with the
  `run_id` and the exact command next to the output. Captured output is never typed by
  hand.
- A small module `src/wearable_pipeline/query_runner.py`, run as
  `python -m wearable_pipeline.query_runner`, executes one SQL file or a directory of SQL
  files against `output/wearable.duckdb` and prints the results. It opens the database
  read-only and is separate from the pipeline commands.

### Decisions, assumptions and scale

- Document these decisions and their rejected alternatives: DuckDB versus Spark; local
  DuckDB storage versus proposed Iceberg/Parquet; the current CLI and compose versus
  proposed cloud orchestration; a shared silver layer feeding the gold outputs; the
  quality-failure behavior; and the reproducibility mechanisms (`run_id`, `compare`,
  pinned image).
- List each assumption as observed, implemented or assumed. A-02 (`_unit` semantics) and
  the provisional thresholds (the stuck-run threshold, the weekly and cohort coverage
  thresholds) are marked as assumptions. Timezone and week rules, quality handling and
  incremental corrections are described as implemented behavior.
- `gold_participant_devices` is recorded as a client requirement that the pipeline does not
  produce. The brief lists it only as a secondary "also produce" item, not among its main
  outputs, and INIT-001 placed it out of scope, so it was not implemented (nothing was
  delivered in INC-002).
- Scaling is discussed qualitatively and grounded in measured numbers: the full-run time,
  peak memory and dataset size recorded in the INC-009 evidence. No thresholds or
  extrapolated performance estimates are invented.

### AI usage disclosure

- A short README section states only what the repository supports. GitHub Copilot CLI
  agents ran through `scripts/orchestrate_increment.py` in the interviewer, planner,
  reviewer and implementer roles, for drafting and refining code, debugging suggestions,
  design and architecture wording, and review feedback. ChatGPT was used for design
  discussion.
- Model names were not recorded in the reports. The disclosure says so and does not name
  models the repository cannot show.
- The author wrote the intents, made or ratified decisions at the review gates, reviewed
  the phase artifacts and the real-data evidence, and made the commits after gate review.
  AI-assisted output was not accepted without human review.
- Verification controls are listed: red/green tests, adversarial review gates, and human
  reruns on real data.
- Known slips are disclosed, including the heredoc appends to the INC-006 and INC-007
  evidence reports, which were not disclosed in those reports, and the other tool-use
  deviations that are recorded in the reports.

## Constraints

- No change to pipeline behavior, table schemas, `run_id`, or the existing CLI commands
  (`run`, `run --mode incremental`, `compare`).
- The query runner is read-only: it opens the database read-only and creates no tables.
- No new runtime dependency. `pyproject.toml` and `uv.lock` are unchanged.
- Analytical SQL reads only `gold_*` tables.
- The README labels cloud components as proposed and never presents them as built or
  tested.
- No raw participant data, credentials or generated outputs are committed.
- Phase edits are confined to `README.md`, `queries/`, `src/`, `tests/`, `specs/` and
  `reports/INC-010/`, per the `code` kind rules.

## Planning Decisions

Left to the planner and ratified by the human at the planning gate:

- File names and exact SQL of each query, and how valid-day counts are chosen for the
  study-wide daily metrics so they stay consistent with the cohort table.
- The standard deviation definition (sample or population), rounding, column names and
  the sign convention of A minus B.
- The runner's arguments and exit behavior for a missing database, a missing SQL file or
  an invalid query.
- The gold fixture design and how the README table and column names are cross-checked
  against the code.
- The README structure and the exact diagram layout.

## Verification Expectations

- `python -m pytest -q` passes, including the fixture-based query tests: exact expected
  results on a small hand-computed gold fixture, result columns and grain (cohort A,
  cohort B and A minus B rows), gold-only reads, NULL handling, and read-only,
  deterministic output with stable ordering.
- The README commands match the verified INC-009 commands.
- Every table and column named in the README and diagrams exists, cross-checked by tests.
- Implemented versus proposed labelling is reviewed, with no cloud component implied as
  built or tested.
- Real-data evidence, run by the human on a local run and recorded in the implementation
  report: the captured outputs match a rerun of the SQL, with `run_id` and command
  recorded.
- The Mermaid diagrams render in a preview or on GitHub.
- The AI disclosure matches repository facts and avoids unverifiable claims.
- `data/` is unchanged, and no raw data, credentials or generated outputs are committed.

Human steps after implementation: review the evidence and commit.
