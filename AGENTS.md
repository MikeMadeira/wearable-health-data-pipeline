# Repository Brief

This repository implements the Meridian wearable-health data engineering challenge.
The raw study data is in `data/` locally and must remain immutable and out of Git.

## Working rules

- Implement one reviewed vertical increment at a time; broad capabilities go through
  the slicing planner before individual increments.
- Keep production code in `src/wearable_pipeline/`, tests in `tests/`, and executable
  contracts in `specs/`.
- Profile actual source records before asserting data-quality rules. Distinguish
  observed defects from assumptions, and preserve raw values when flagging or
  excluding suspicious observations.
- Tests must cover the grain and keys of each output, joins, date/time handling,
  quality rules, and idempotent reruns as relevant to the increment.
- Runtime dependencies are allowed only when justified in the intent/spec and added
  during planning. Keep transformations deterministic and make I/O boundaries clear.
- Run `python -m pytest -q` after each increment. Do not weaken tests to obtain a pass.
- Never commit raw participant data, generated outputs, or credentials. Harness reports
  are durable workflow artifacts and may be committed by the runner after gate checks.

## Agent orchestration

- `scripts/orchestrate_increment.py` coordinates planner, planning reviewer,
  implementer, and implementation reviewer phases.
- Pass `--kind code`, `--kind environment`, or `--kind notebook` to select the
  matching planning, ownership, and verification gates. The default is `code`.
- Agents communicate through `reports/<artifact-id>/` Markdown artifacts and follow
  `.github/skills/agent-communication/SKILL.md`.
- Human-authored intents under `intents/` are the source of scope. The intent
  interviewer may help draft them but must not invent or commit one.
- The human creates the initial baseline commit and reviews each phase artifact. The
  automation runner creates phase commits only after validated gates; it never pushes.
- Run automated increments only from a dedicated branch or worktree with a clean
  Git working tree. Do not skip a phase gate.
- Broad work must first produce a reviewed vertical-slice proposal under `plans/`.

## Challenge outcomes

The eventual pipeline must provide daily clinical analytics, a distinct weekly
biostatistics schema, chronotype cohort analysis, and `gold_participant_devices` with
one row per participant. It must be containerized, reproducible, and idempotent. The
architecture should show local components and corresponding cloud services.