# INIT-001: Wearable Health Data Pipeline

## Goal

Deliver a locally runnable, containerized, reproducible and idempotent pipeline for the
Meridian wearable-health study. It turns the immutable raw exports (participants,
device metadata, wellness survey, and per-participant sleep, steps and heart rate) into
trustworthy, analysis-ready tables for two consumers:

- the clinical analytics team, who need daily-fresh data;
- the biostatistics team, who work in weekly batches and expect a distinct schema.

It also provides chronotype cohort analysis using sleep and activity. Both consumer
paths derive from one shared, validated source of truth.

The initiative covers every challenge deliverable except those listed under Out of
Scope: pipeline code, a README that explains end-to-end execution, an architecture
diagram, analytical query output, and an AI usage disclosure. Success means a reviewer
can run the pipeline from a clean checkout, rerun it without duplicating or corrupting
data, and follow the justified design trade-offs, including rejected alternatives.

## Desired End-to-End Capability

1. Read the immutable raw exports from `data/`; they are never modified or committed.
2. Land them in a bronze layer, then validate and normalize them into a silver layer
   that is the single validated source of truth.
3. Persist data-quality outcomes as queryable tables plus a human-readable report.
4. Derive gold outputs from silver only: daily clinical analytics, a distinct weekly
   biostatistics schema, and chronotype cohort analysis using sleep and activity.
5. Run with a single command in two modes: a deterministic full rebuild of derived
   layers from raw, and an incremental mode. Reruns produce identical results with no
   duplicates.
6. Show documented SQL against the gold layer, with captured output of at least three
   metrics including a chronotype cohort comparison.

## Requirements

- Profile the actual raw records before any quality rule or schema is fixed. A
  notebook-based profiling slice precedes all other slices. It finds, evidences and
  proposes how to correct and guard against data problems, and informs the schemas,
  grains and join keys that serve both consumers. Distinguish observed defects from
  assumptions.
- Use layers bronze (raw landed, immutable), silver (validated source of truth) and
  gold (daily clinical, weekly biostatistics, chronotype cohort). Daily and weekly
  outputs both derive from silver.
- Give the daily and weekly outputs different schemas. Each output has a documented,
  tested grain and key.
- Meet the daily-freshness need: data that participants produce daily must be able to
  reach the clinical analytics tables each day. The mechanism is for the planner to
  propose and the human to approve.
- Apply late and corrected data as an idempotent upsert (MERGE) on each gold table's
  natural key, for example `participant_id` + `local_date` for the daily table. A rerun
  updates matched rows and inserts new ones, with no duplicates.
- Flag, quarantine or exclude suspect observations according to approved rules while
  preserving raw values. Failure behavior per defect class is decided after profiling.
- Persist quality results as queryable tables and a human-readable report. Document
  where quality is handled in the architecture.
- Give tests coverage of the grain and keys of each output, joins, date/time handling,
  quality rules, and idempotent reruns, including incremental versus full rebuild
  equivalence.
- Containerize the pipeline so it runs locally. The README shows end-to-end execution.
- The README includes an architecture diagram that maps implemented local components to
  a plausible cloud deployment, clearly labelling what is implemented and what is
  proposed. It also documents measured data issues, quality rules, table grains,
  idempotency, operational and scale considerations, and key decisions with rejected
  alternatives.
- Provide analytical query output with at least three metrics, including a chronotype
  cohort comparison, run against gold only.
- Include an AI usage disclosure section in the README.

## Constraints

- Raw source data remains immutable and out of Git, along with generated outputs and
  credentials.
- Local compute is Python with DuckDB. This is decided; document it against Spark and
  record Spark as a potential improvement if volume and runtime grow.
- Prefer the local storage format closest to Iceberg semantics (transactional,
  idempotent upserts), and justify the choice. Parquet files versus DuckDB tables is
  open to the planner. Iceberg is a cloud proposal only.
- The cloud mapping (for example AWS Batch, S3, Glue Catalog and Athena, optionally
  EventBridge and Step Functions) is a documented proposal only. Nothing is deployed to
  AWS, and implemented versus proposed must be clearly labelled.
- Runtime dependencies must be justified in the plan or spec. DuckDB is expected;
  notebooks are development-only; nothing else is added without planner justification.
- Do not redesign the existing harness (`scripts/`, `.github/`), the `uv.lock`/pyenv
  setup, the `src/wearable_pipeline/` layout or the README structure; extend them.
- Business rules, quality-failure policy, grains, week boundaries and timezone policy
  are not decided in this intent. They are deferred to profiling and approved by the
  human.
- Out of scope: the `gold_participant_devices` table, actual AWS deployment,
  real-time streaming, BI dashboards and ML modeling.

## Planning Expectations

The slicing planner must produce a reviewed vertical-slice proposal under `plans/`
before any increment intent is created.

Decided now by the human:
- classification as an initiative;
- the layers, run modes, upsert semantics for late and corrected data, and persisted
  quality outputs;
- Python with DuckDB, and the cloud-proposal-only scope;
- the exclusions listed above.

Reserved for the human at review gates, with the planner proposing options and evidence
and not finalizing:
- business rules and the quality-failure policy, based on profiling and with help from
  the intent interviewer and planning agents;
- grain, day-assignment, week-boundary and timezone choices;
- the weekly biostatistics schema;
- the daily-freshness mechanism;
- the final stack and storage trade-offs.

Resolved by the planner:
- slice boundaries and order;
- table and layer naming;
- slice kinds (`code`, `environment`, `notebook`);
- Docker, README, architecture diagram and AI-disclosure slices;
- test strategy per slice.

Ordering and slice expectations:
- The first slice is a profiling notebook slice (`--kind notebook`). Later slices
  depend on its human-approved findings.
- Each slice is vertical and independently verifiable, with idempotent-rerun tests.
- The final slice assembles the README, architecture diagram, analytical query output
  and AI usage disclosure.
