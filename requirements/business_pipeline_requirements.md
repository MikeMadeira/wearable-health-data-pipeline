# Meridian Health Analytics: Wearable Health Data Pipeline Requirements

## Purpose

Meridian Health Analytics collects wearable activity, heart-rate, and sleep data,
alongside daily self-reported wellness surveys, from participants in a research
study. Source exports arrive from devices and companion applications in multiple
formats and at different grains. Meridian needs a reliable data pipeline that
turns these raw exports into analysis-ready data for its clinical analytics and
biostatistics teams.

The study dataset covers 50 participants over a 28-day period. Raw study data is
provided in an S3 location, together with a data dictionary.

## Business Requirements

### Data Trustworthiness

- Profile the actual source data, identify and evidence data-quality issues, and
  define appropriate handling for each source.
- Enforce approved quality rules so downstream consumers can rely on the data.
- Make data-quality outcomes verifiable and document how suspect records are
  handled.

### Consumer Data Products

- Provide daily-fresh data for the clinical analytics team.
- Provide a distinct schema suited to the biostatistics team's weekly workflow.
- Support analysis that compares participant cohorts by chronotype using sleep
  and activity data.
- Derive these consumer products from a shared, validated source of truth.
- Also produce a Gold-layer table named `gold_participant_devices`, with one row
  per participant linking `participant_id` to `device_id`.

### Operational Quality

- Ensure correctness, reproducibility, and idempotency. Re-running the pipeline
  must not duplicate or corrupt data.
- Design for maintainable testing and operation, and consider scale and cost.
- Document the design decisions, including the trade-offs considered and
  alternatives rejected.

## Design Expectations

Meridian specifies business outcomes rather than a complete technical blueprint.
The implementation should justify its choices for data layers, schemas, storage,
data-quality handling, and orchestration. No specific technology stack is
required by the source brief.

## Delivery and Acceptance Criteria

- **Runnable repository:** Provide the pipeline code and a README with end-to-end
  execution instructions.
- **Architecture documentation:** Include a diagram of the primary layers, data
  flow, and quality-handling points. The pipeline must run locally in a container;
  the diagram must also show a plausible cloud deployment and clearly map local
  components to corresponding cloud services.
- **Analytical evidence:** Include the output of a query against the analytical
  layer showing at least three metrics, including a chronotype cohort comparison.
- **AI usage disclosure:** Include a short README section describing any use of
  AI-assisted tools for code generation, debugging, or design suggestions.
- **Technical review readiness:** Document key architectural decisions and
  assumptions for discussion, including how the solution could evolve as the
  study scales.

## Scope Note

This document reformats the business brief supplied by Meridian Health Analytics.
Implementation scope and decisions are governed by the reviewed initiative intent
and slice plan. In particular, the client brief includes `gold_participant_devices`,
while the current `INIT-001` intent lists that table as out of scope; it remains a
client requirement pending an explicit scope decision.