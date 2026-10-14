# Planner Report: INC-009

Status: READY
Inputs:
- intents/INC-009-containerized-run.md
- scripts/orchestrate_increment.py (`environment` planning prompt, `run_tests` `environment-red` gate, `run_docker_tests`, `validate_phase_paths`)
- src/wearable_pipeline/cli.py, pipeline.py, incremental.py (fail-fast behavior), Dockerfile, .dockerignore, pyproject.toml
- `data/` was not read; only synthetic fixtures (`tests/test_walking_skeleton.py::make_data`).

Outputs:
- specs/INC-009.md (Draft; decisions D1-D9 in section 11)
- tests/test_container.py (45 tests: 1 smoke, 37 skip-gated static/Docker contract tests, 7 always-run CLI behavior tests; no `pytest.raises(NotImplementedError)`)
- reports/INC-009/planner.md
- No Dockerfile, .dockerignore, docker-compose.yml, pyproject.toml, uv.lock or `src/` change.

Checks:
- `python -m pytest -q` (repo root, `PYTHONPATH=$PWD`, 332 s): `1 failed, 937 passed, 37 skipped`. The only failure is `tests/test_container.py::test_environment_smoke` with `ModuleNotFoundError: No module named 'yaml'`. Baseline before this increment: 0 failed, 930 passed.
- Harness check on that captured output: `test_environment_smoke` present: PASS; `No module named` present: PASS; regex `(?m)^=+\s+1 failed(?:,|\s)`: FAIL (see Findings, harness defect).
- Existing tests: none amended, none changed or weakened.

Findings:
- Decisions for human ratification (full text in specs/INC-009.md section 11):
  1. D1 Base image: `python:3.12.<patch>-slim-<codename>` exact tag, `uv` from a pinned `ghcr.io/astral-sh/uv:<x.y.z>`; never `latest`.
  2. D2 User/UID: image user UID/GID 1000 (non-root); compose `user: "${HOST_UID:-1000}:${HOST_GID:-1000}"`; arbitrary UID must work.
  3. D3 `CMD` only, no `ENTRYPOINT` (an entrypoint would break the harness `docker run image python -m pytest -q`); args pass through by replacing the command.
  4. D4 Service `pipeline`; mounts `./data:/data:ro`, `./output:/output`.
  5. D5 Red gate: add `pyyaml` as a DEV dependency (really needed to parse the compose file in tests). `test_environment_smoke` imports `yaml` and fails with `No module named 'yaml'` until the implementer declares and installs it. Other container tests skip until PyYAML and `docker-compose.yml` exist, so the baseline has exactly one failure. Runtime dependencies stay `["duckdb>=1.4"]`; no existing lock pin may change. Rejected: fake dependency, nonexistent `src` module import.
  6. D6 One command: `docker compose run --rm pipeline`.
  7. D7 Docker-based tests are opt-in (`WEARABLE_DOCKER_TESTS=1`), not part of the harness gate; the implementer runs them on the host. They were not run at planning (no image exists yet).
  8. D8 No resource limits, restart policy or ports.
  9. D9 No `src/` change: reading the code shows missing data, empty data, unreadable files and an unwritable existing output dir already exit 1 with `error: ...` and no traceback (pinned by 7 passing tests, full and incremental). Accepted residuals: an unwritable output is detected late (after transform); a missing output dir under an unwritable parent gives an unhandled `PermissionError` traceback (still non-zero). Alternative: a small preflight in `src/` (needs human approval).
- HARNESS DEFECT, needs human attention before the gate runs: `run_tests(..., "environment-red")` requires `(?m)^=+\s+1 failed`, but `pytest -q` prints the summary without the `=` banner (`1 failed, 937 passed, 37 skipped in 332.79s`), so real output fails the regex although the content is exactly right. `tests/test_orchestrator.py` tests the gate only with a fake banner line. Options: (a) the human relaxes the regex to `(?m)^(?:=+\s+)?1 failed(?:,|\s)` in `scripts/orchestrate_increment.py` (outside planner scope); (b) change pytest verbosity config so `-q` nets to normal output (affects all runs; not done).
- Implementation notes: pytest in the container runs non-root, so `/app` caches may be unwritable (warning only). `.dockerignore` must keep `tests`, `scripts`, `specs`, `intents`, `reports`, `Dockerfile`, `docker-compose.yml`, `uv.lock` in the image because the suite and smoke test read them. `tomllib` needs Python 3.11+ (image is 3.12).
- Human real-data evidence (8 items) is in specs/INC-009.md section 10; it is not automatable here.

Next action:
Decisions D1-D9 RATIFIED 2026-10-14 by the human, unchanged (full text in specs/INC-009.md section 11). The human also approved fixing the harness `environment-red` regex to `(?m)^(?:=+\s+)?1 failed(?:,|\s)`, with a test using real `pytest -q` output; this was done outside the planner's scope.
Planning review: APPROVED (reports/INC-009/planning-review.md). Route to the implementer after the planning commit.
