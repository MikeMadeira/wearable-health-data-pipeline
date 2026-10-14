# Planning Review Report: INC-009

Status: APPROVED
Inputs:
- intents/INC-009-containerized-run.md
- specs/INC-009.md
- tests/test_container.py
- reports/INC-009/planner.md
- .github/skills/agent-communication/SKILL.md
- scripts/orchestrate_increment.py (`run_tests`, `environment-red`)
- src/wearable_pipeline/cli.py, pipeline.py, incremental.py (read-only check of D9)
- `data/` was not read.

Outputs:
- reports/INC-009/planning-review.md

Checks:
- Spec alignment with intent (goal, requirements, constraints, scope exclusions): PASS
- Real-data evidence items 1-8 all present in spec section 10: PASS
- Failure behavior (missing mount, unwritable output, exit-code propagation, previous output unchanged): PASS
- Contract completeness (image, command form, UID, mounts, ignore rules, deps, gates, decisions D1-D9): PASS
- No `pytest.raises(NotImplementedError)` in tests/test_container.py: PASS
- `python -m pytest -q tests/test_container.py`: PASS (`1 failed, 7 passed, 37 skipped`; the one failure is `test_environment_smoke` with `ModuleNotFoundError: No module named 'yaml'`; `pyyaml` is the spec-declared dev dependency)
- Full suite `python -m pytest -q`: NOT RUN (reviewer relied on the planner's captured result of `1 failed, 937 passed, 37 skipped`, which is consistent with the 45 tests collected here)
- Skipped tests not silently dropped after implementation: PASS (spec section 9 requires "no `test_container.py` static test skipped"; the smoke test fails rather than skips if PyYAML or the compose file is missing)
- D9 claims verified against source: PASS (cli.py catches only `PipelineError` and prints `error: ...`, returns 1; pipeline.py and incremental.py wrap `duckdb.Error` as `database write failed` and re-raise other exceptions, so a `PermissionError` outside that path is unhandled as the spec says; 7 CLI-behavior tests pin the rest)
- No Dockerfile/.dockerignore/docker-compose.yml/pyproject.toml/uv.lock/src changes: PASS (`git status` shows only untracked `reports/INC-009/`, `specs/INC-009.md`, `tests/test_container.py`; `git diff HEAD` on the protected paths is empty; `docker-compose.yml` does not exist)
- Harness environment-red regex against real `pytest -q` output: FAIL (see Findings)

Findings:
- HARNESS DEFECT, for human decision (not fixed here): `run_tests(..., "environment-red")` requires `(?m)^=+\s+1 failed(?:,|\s)`. `pytest -q` prints the summary line `1 failed, 7 passed, 37 skipped in ...` with no `=` banner, so the regex does not match and the orchestrator will raise "Planning test gate requires only test_environment_smoke to fail..." although the baseline is correct. I confirmed the regex and the `-q` output. Suggested options: relax the regex to `(?m)^(?:=+\s+)?1 failed(?:,|\s)` in `scripts/orchestrate_increment.py`, or run the gate without `-q`. The human decides and should also add a test using real `-q` output, because `tests/test_orchestrator.py` only uses a fake banner line.
- The red baseline is genuine and minimal: exactly one failure, caused by a missing declared dependency, and the other 44 tests pass or skip.
- The spec and tests are strong on the static contract, but the compose and Docker behavior tests skip at planning and the Docker tests are opt-in (D7). The implementation gate must show 0 skipped static tests and a recorded run of `WEARABLE_DOCKER_TESTS=1`.
- Minor: spec status is "Draft" pending ratification of D1-D9 (notably D5 pyyaml as a dev dependency, and D9 no `src/` change with accepted late and unhandled-`PermissionError` residuals). The human ratifies at this gate.
- The pre-existing `Dockerfile` and `.dockerignore` are unmodified; the implementation must rewrite them to satisfy the tests.

Next action:
Human: decide on the harness regex defect, ratify D1-D9, then approve the baseline commit. Orchestrator: do not run the automated environment-red gate until the regex is resolved; after the human approves the baseline, route to the implementer.
