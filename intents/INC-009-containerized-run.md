# INC-009: Containerized End-to-End Run

Parent initiative: `INIT-001-wearable-health-pipeline`. Kind: `environment`. Follows
INC-008 (`intents/INC-008-chronotype-cohort.md`) and the committed harness change HX-1.
The pipeline entry point is the existing CLI (`run`, `run --mode incremental`, `compare`)
from INC-002 and INC-006.

## Goal

From a clean checkout, one documented command builds an image and runs the full pipeline
on the real data. The data is mounted read-only and the output is written to a mounted
directory. A second identical run reproduces the same outputs. The client brief requires
that the pipeline runs locally in a container, and that reruns are reproducible and
idempotent.

## Boundary

One `environment` slice: the image and its compose service. The README run guide, the
architecture diagram and the query output are INC-010. The pipeline behavior is not
changed here.

## Requirements

### Image and entry point

- One image serves both uses. Its default command runs the pipeline CLI. The test suite
  stays runnable by overriding the command (`python -m pytest -q`), which the harness
  gate relies on.
- Rebuilds install the locked dependency versions from `uv.lock`, so the same DuckDB
  version is used every time.
- The image never contains `data/` or any generated output.
- The container runs as a non-root user. Files it writes to the mounted output are usable
  by the host user.

### Compose service

- `docker-compose.yml` defines a service that mounts `./data` read-only and `./output`
  read-write, and runs the full pipeline with one command.
- The `run` (full and incremental) and `compare` commands are reachable through the same
  image and service by passing arguments. A plain `docker run` equivalent works.
- State between runs lives only in the host output directory (the DuckDB file, quality
  report and run state). A rerun or an incremental run against the same output works
  across container restarts. No named volumes are required.

### Failure and safety behavior

- A missing or unreadable data mount, or an unwritable output directory, makes the run
  fail fast with a non-zero exit code and a clear message.
- A pipeline failure propagates its non-zero exit code through compose.
- A failed run leaves the previous output unchanged (INC-006 behavior).
- The raw data is protected by the mount itself (`:ro`), not by convention.

### Reproducibility

- Two container runs on the same data give identical tables, quality report and `run_id`,
  checked with the `compare` command.
- Memory needs (about 4.9 GB peak on the real data) and run time are documented in the
  implementation report. No resource limit is imposed.

## Constraints

- Do not change: the pipeline behavior and outputs, table schemas, `run_id` derivation,
  the CLI surface, the specs and tests of INC-002 to INC-008, `data/`.
- Allowed changes: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `pyproject.toml`
  and `uv.lock`, and the minimum under `src/` only if the container needs it (for example
  a clear error for a missing mount). Any `src/` change is flagged for the human.
- No new runtime dependency unless the planner justifies it.
- The harness gates stay satisfied: image build and `pytest -q` inside the container.
- Out of scope:
  - the README text and run guide (INC-010);
  - the analytical query output (INC-010);
  - cloud deployment and image publishing;
  - continuous integration;
  - new pipeline behavior and scheduling.

## Planning Decisions

Left to the planner, ratified by the human at the planning gate:

- the base image and tag pinning;
- user and UID handling;
- entrypoint versus command form;
- the compose service name and the mount paths;
- how to satisfy the environment red-test gate without inventing a dependency (the
  harness expects `test_environment_smoke` to fail because a declared dependency is
  missing; INC-009 declares none);
- any change under `src/`.

## Verification Expectations

Harness gates:

- `python -m pytest -q` passes locally.
- The image builds, and `python -m pytest -q` passes inside the container.
- Changes made by the orchestrated phases are confined to the allowed paths above and
  `reports/INC-009/`.

Real-data evidence, run by the human from a clean checkout and recorded in the
implementation report with the commands and outcomes:

1. A one-command compose full run succeeds and produces the output tables and report.
2. The read-only mount is enforced: a write attempt to the data mount fails, and the
   `data/` SHA-256 is unchanged.
3. A second run reproduces identical tables, report and `run_id` (compare command).
4. An incremental run with no change is a no-op, and incremental versus full on a
   corrected copy of the data is identical.
5. A missing data mount and an unwritable output directory each exit non-zero with a
   clear message.
6. The non-root container user can write the mounted output, and the host user can read
   it.
7. Timings and peak memory are recorded.
8. The image contains no data or outputs.

Human steps after implementation: review the evidence and commit. The orchestrator's
implementation phase may not edit `notebooks/` or `README.md`.
