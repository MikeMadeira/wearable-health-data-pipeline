# Implementation Report: INC-009

Status: READY
Inputs:
- intents/INC-009-containerized-run.md
- specs/INC-009.md
- tests/test_container.py
- reports/INC-009/planner.md
- reports/INC-009/planning-review.md

Outputs:
- Dockerfile (rewritten)
- docker-compose.yml (new)
- .dockerignore (added `*.duckdb`, `*.duckdb.wal`, `.ipynb_checkpoints`)
- pyproject.toml (`pyyaml>=6.0` added to the `dev` extra only)
- uv.lock (re-locked with `uv lock`; only `pyyaml 6.0.3` added, no existing pin changed, DuckDB stays 1.5.6)
- reports/INC-009/implementer.md

Checks:
- `python -m pytest -q` (local, PyYAML installed): PASS, 969 passed, 7 skipped, 0 failed (313 s). The 7 skips are the opt-in Docker tests only; no static container test skipped; `test_environment_smoke` passes.
- `docker build -t wearable-inc009 .`: PASS
- `docker run --rm wearable-inc009 python -m pytest -q`: PASS, 969 passed, 7 skipped (the Docker tests, which are opt-in and not run inside the container) (300 s, run as non-root `app`)
- `WEARABLE_DOCKER_TESTS=1 python -m pytest -q tests/test_container.py`: PASS, 45 passed, 0 skipped
- `docker compose config -q`: PASS
- Edits confined to allowed paths (`src/`, `tests/`, `specs/` untouched; D9): PASS
- Real-data pipeline run: NOT RUN (`data/` not read, as instructed)

Findings:
- Dockerfile: `python:3.12.6-slim-bookworm` plus `COPY --from=ghcr.io/astral-sh/uv:0.11.9`. Dependencies come from `uv sync --frozen --extra dev --no-install-project` into `/opt/venv`, with `PYTHONPATH=/app/src` for the code. Build args `APP_UID`/`APP_GID` default to 1000. The final `USER` is `app`, `HOME=/home/app`, `PYTHONUNBUFFERED=1`. The venv and `/app` are world-readable. It has `CMD` only, no `ENTRYPOINT`, and the existing ipykernel registration is kept for the notebook tests.
- Compose: service `pipeline`, `build: .`, `user: "${HOST_UID:-1000}:${HOST_GID:-1000}"`, volumes `./data:/data:ro` and `./output:/output`. Nothing else is set.
- Commands for INC-010 docs: `docker compose run --rm pipeline`. With a non-1000 host user, prefix it with `HOST_UID=$(id -u) HOST_GID=$(id -g)`. Incremental: `docker compose run --rm pipeline python -m wearable_pipeline run --mode incremental --data-dir /data --output-dir /output`. Tests: `docker compose run --rm pipeline python -m pytest -q`.
- Local environment note: `uv sync --extra dev` also removed an unused `pyspark` from the local `.venv`, because it was not in the lock. Nothing in the repo imports it. The tests were run with the pyenv `python`, into which `pyyaml` was pip-installed.
- Run as an arbitrary UID, pytest in the container emits harmless warnings (unwritable `.pytest_cache`, IPython dir). They do not affect results.
- Peak memory and timings on real data were not measured. The synthetic suite takes about 5 min in the container.
- The 8 real-data evidence items of spec section 10 remain human-run: compose full run, read-only mount with data SHA-256 unchanged, reproducible second run with `compare`, incremental no-op and incremental vs full, missing mount and unwritable output errors, non-root write with host-readable output, timings and peak memory, and the image-contains-no-data scan.

Next action:
Route to the implementation reviewer. The human then runs the 8 real-data evidence items.

## Real-data evidence (run by the orchestrator at the human's request, 2026-10-14)

Setup: a copy of the repo working tree plus a copy of `data/` under `/tmp/inc009` (deleted afterwards), `docker compose build`, `HOST_UID`/`HOST_GID` set to the host user (1000). The real `data/` tree was never mounted writable. `data/` SHA-256 over sorted per-file hashes: `3f16dc19…ab47` before and after, unchanged. The corrected-data check used an edited copy only.

1. One-command full run: `docker compose run --rm pipeline` succeeded, `run_id=a826c8397cf5ad8e` (same as the INC-008 local run), `wearable.duckdb` (19.4 MB) and `quality/report.md` written, about 71 s wall clock including container start. PASS (the first attempt failed only because my temp copy lacked `output/`, see F1).
2. Read-only mount: `touch /data/x` and appending to `participants.csv` both fail with "Read-only file system". `data/` hash unchanged. PASS.
3. Reproducibility: a second full run into the same output gave the same `run_id`; `compare` of run 1 against run 2 reports `identical`, and `quality/report.md` is byte-identical. PASS.
4. Incremental: a no-op incremental run reports `mode=noop`, about 1.1 s wall clock, output `identical` to the full run. On a corrected copy (P001 `steps` at `2026-01-20T12:00`, 16 to 53), incremental took about 3.1 s and a full rebuild about 70 s; both give `run_id=4317734d4c2f4599` and `compare` reports `identical`. Against the pre-change run, `compare` shows only `raw_manifest` and `silver_steps_minute` differing by one row and the report, as expected. PASS.
5. Failures, each exit code 1 with a clear message:
   - no data mount: `error: data directory not found: /data`;
   - empty data mount: `error: required input missing: participants/participants.csv`;
   - read-only output: `error: database write failed: IO Error: Cannot open file "/output/wearable.duckdb": Read-only file system`;
   - root-owned output directory, run as UID 1000: same message with `Permission denied`.
   PASS. As accepted in D9, the output failure appears after the transform has run (about 40 s on real data).
6. Non-root: the container runs as `uid=1000(app)`. `output/` content is owned by the host user (mode 644 files, 775 directory) and readable on the host. PASS.
7. Timings (wall clock, includes container start): full 70-71 s, no-op incremental 1.1 s, one-record incremental 3.1 s. Peak memory (cgroup `memory.peak` of the container, full run): 5,252,268,032 bytes (about 4.9 GiB). It may include page cache. No container memory limit is set (D8).
8. Image contents: `/data` and `/output` do not exist in the image. `docker history` shows only `COPY . .`, `COPY pyproject.toml uv.lock`, the `uv` copy and the base. A scan of every layer of `docker save` found no `wearable_events`, `participants.csv`, `health_summaries`, `*.duckdb`, `data/` or `output/` entries (the scan was validated by finding the expected `app/src/wearable_pipeline` entries). PASS.

Findings:
- F1 (resolved, test-setup artifact, no amendment needed): the first run failed because my `/tmp` copy of the tree left out `output/`, so compose created it as root and the non-root user could not write (`database write failed ... Permission denied`, after the full transform). In a real checkout `output/.gitkeep` is tracked (`.gitignore` has `output/*` and `!output/.gitkeep`), so `output/` exists and is owned by the cloning user, and the one-command run works. Residual limit: if someone deletes `output/` before the first run, compose recreates it as root and the run fails the same way; the fix is `mkdir -p output`, to be mentioned in the INC-010 README troubleshooting.
- F2: the first measurement of peak memory by sampling `docker stats` was too coarse (it showed about 740 MiB); the cgroup value above is the one to use.

Cleanup: the temp tree, images, the `alpine` image and the compose network were removed.
