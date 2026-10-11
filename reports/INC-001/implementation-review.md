# Implementation Review Report: INC-001

Status: REJECTED
Blocker-Type: IMPLEMENTATION
Inputs:
- intents/INC-001-data-profiling-notebooks.md (controlling scope)
- reports/INC-001/planner.md (acceptance checklist)
- reports/INC-001/planning-review.md
- reports/INC-001/implementation.md
- reports/INC-001/profiling-findings.md
- notebooks/INC-001-tabular.ipynb, INC-001-sleep.ipynb, INC-001-steps-heart-rate.ipynb, INC-001-cross-source.ipynb
- AGENTS.md
- .github/skills/agent-communication/SKILL.md
- scripts/orchestrate_increment.py (`validate_notebook_document`, `run_notebooks`)

Outputs:
- reports/INC-001/implementation-review.md

Checks:
- Notebook JSON valid, `cells` array, `metadata.language` (python/markdown) correct, unique non-empty ids, source shape: PASS (all four notebooks)
- No stored outputs and null `execution_count` on every code cell: FAIL (see Findings 1)
- Kernelspec is `wearable-health-data-pipeline`: FAIL (see Findings 2)
- Execute each notebook top to bottom with nbclient on COPIES in a temp dir (outputs stripped, kernel `wearable-health-data-pipeline`): PASS (cross-source 11.4 s, sleep 0.7 s, steps-heart-rate 20.8 s, tabular 0.8 s; limit 600 s)
- sha256 of all 154 files under `data/` before vs after execution: PASS (identical)
- `python -m pytest -q`: PASS (29 passed)
- `git status`: PASS (only untracked `notebooks/`, `reports/INC-001/implementation.md`, `reports/INC-001/profiling-findings.md`; `planner.md` and `planning-review.md` untouched; nothing under `data/`, `src/`, `tests/`, `specs/`, `pyproject.toml`, `uv.lock`)
- Imports limited to stdlib (csv, json, datetime, pathlib): PASS
- No file writes (only read-mode `open` in csv notebooks; `json.load` per participant file, one participant at a time): PASS
- No raw measurement rows printed by cell sources or in fresh output: PASS (aggregates, counts, participant ids, date/minute identifiers only)
- Spot-check of findings vs fresh output (15+ items): PASS (see below)
- Planner checklist, findings content items: PASS (see below)
- Docker build: NOT RUN (not required for notebook increments)

Findings:
1. BLOCKING. The committed notebooks contain stored outputs and non-null execution counts. Measured on disk: cross-source has 7 code cells with `execution_count` 1-7 and 6 outputs; sleep 6 cells (6 outputs); steps-heart-rate 9 cells (9 outputs); tabular 7 cells (7 outputs). The file mtimes (20:40-20:41) are later than the implementation report, which says outputs were not stored; the notebooks were apparently re-executed in place (possibly by an editor/IDE) after the report. This violates the intent ("Notebooks store no outputs"), the planner checklist item "No cell outputs or execution counts are stored", and the review expectation. Action: reset every code cell to `outputs: []` and `execution_count: null` in all four notebooks, and re-check that nothing else rewrites them. Stored outputs also have to be re-inspected for raw rows, though none appear in fresh output.
2. BLOCKING (checklist). `metadata.kernelspec.name` is `python3` (display name "wearable-health-data-pipeline (3.13.13)"; `language_info` 3.13.13) in all four notebooks. The planner checklist requires the kernel `wearable-health-data-pipeline`, which is the name the Dockerfile registers. Under the harness, `python3` resolves to whatever default kernel exists (here a pyenv 3.12.6 env), so execution is not pinned to the declared kernel. Action: set `kernelspec.name` to `wearable-health-data-pipeline` (display name "Python (wearable-health-data-pipeline)") and make `language_info.version` consistent.
3. Non-blocking. A few markdown cells carry both top-level `id` and `metadata.id`; this is fine. Reported runtimes in the findings (e.g. tabular 0.11 s, steps-hr 20.3 s, cross 10.9 s) agree with fresh runs within noise (0.14 s, 20.1 s, 10.7 s). Stuck-run, parse-time and read-time figures are within run-to-run variation.
4. Non-blocking observation. `implementation.md` claims "no stored outputs ... PASS (checked by script)"; that claim is not true of the files as they exist now. After correction the implementer should re-run the stored-output check last, after any execution.

Spot-check of findings against fresh executed output (all matched):
- M-01 `_unit` `steps_per_hour`: 120,963 records; P005 40,323 / P033 40,320 / P038 40,320; medians 60/300/420 (`shr-11`); 84 participant-days (`xs-10`).
- M-05 UTC `Z`: heart rate only, 2,880 records, P018 and P044 at 1,440 each; 600 change date; as-label 2,280 fill / 600 collide; converted 2,880 fill / 0 collide (`shr-08`, `xs-08`).
- M-07 duplicate day: steps and heart rate 5,790 excess slots in 8 participants; P010/P019/P022/P025 duplicate 2026-01-10 and miss 2026-01-08; conflicting values steps 921/899/856/909, heart rate 1,297/1,300/1,290/1,289 (`shr-06`, `shr-10`).
- M-04 stuck runs: 2 runs of 180 min, P003 2026-01-09T04:44 and P042 2026-01-24T09:16, 0.018% of records (`shr-15`).
- M-03/M-02: 4 out-of-range heart-rate records (2 < 40, 2 > 200; min -10, max 999); steps min -1, 95,902 > 30, 56,389 > 200, 5,141 > 1000, max 1,000,000 (`shr-13`).
- M-06/M-08: record histogram 42/5/3; gaps 6,840 (steps) and 9,720 (heart rate); P020, P028, P041 partial 360 (`shr-06`, `shr-10`).
- M-09/M-10/M-11: 28 non-zero-second records in 5 participants; 4 out-of-order; 48 of 50 identical sequences.
- T-01/T-02/T-03 survey and firmware: 1,414 survey rows, P999 14 orphan rows, 1,400 enrolled; 15 blank `firmware_version`, all 15 parse to 2.1.8 (`tab-04`, `tab-06`, `tab-10`).
- Survey grain: one row per (participant, date), 0 duplicate keys, 28 days for each of the 50 enrolled participants (`tab-12`, `xs-06`).
- S-01 to S-07: 4 duration mismatches, 3 spans > 12 h, 2 < 3 h, 218 efficiency mismatches, 1,132 of 1,400 efficiency == 100, 1,379 non-zero-second onsets, 155 after-midnight onsets (`slp-08`, `slp-10`).
- X-02 / G1 items: 108 flagged participant-days, 1,285 complete and flag-free; S1-S4 collisions 0/133/7/0; cohort 43 (A 23 / B 20) with all 28 days; week blocks 7777 / 47773 / 34774 (`xs-10`, `xs-08`, `xs-12`).

Planner checklist and content review (aside from findings 1 and 2):
- Constraints: stdlib only, per-participant streaming, no writes, aggregates only, parse time and total runtime recorded in findings section 1: met.
- Tabular, sleep, steps-heart-rate and cross-source coverage items: all met; checks that found nothing are in findings section 3.
- Findings document: each finding has source, check, count, cell reference, example identifiers, severity and a proposal; observed defects (section 2) are separate from assumptions (section 5); every handling is worded as a proposal; survey grain stated (section 4); G1 list with the five required items plus week boundary, weekly schema, cohort and freshness, all marked UNRESOLVED; no decision is made on the human's behalf.

Next action:
Route to the implementer to correct findings 1 and 2 in the four `notebooks/INC-001-*.ipynb` files only (strip outputs, null execution counts, correct kernelspec) and update `implementation.md`; do not change `profiling-findings.md` content unless a count changes. Then re-run the implementation review.
