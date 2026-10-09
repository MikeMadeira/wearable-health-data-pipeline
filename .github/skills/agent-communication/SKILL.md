---
name: agent-communication
description: Communication protocol for the wearable-health-data-pipeline orchestrator and phase agents.
---

# Agent Communication Protocol

The orchestrator is the hub. Phase agents do not delegate directly to one another and
do not treat chat output as durable state. Every handoff is a short Markdown report in
`reports/<increment-id>/`.

## Artifact ownership

- The human owns `intents/` and approves phase gates.
- The slicing planner owns `plans/<initiative-id>/vertical-slices.md`.
- The slicing reviewer is read-only and owns `slicing-review.md`.
- The planner owns the generated `specs/`, red tests, and stub.
- The planner reviewer is read-only and owns `planning-review.md`.
- The implementer owns implementation changes under `src/` and `implementation.md`.
- The implementation reviewer is read-only and owns `implementation-review.md`.
- The orchestrator owns routing, phase state, and the final status summary.

One agent must not overwrite another agent's report. Agents may read all prior reports
needed for their phase, but only the assigned writer edits an artifact.

## Handoff report format

Every report must contain:

```markdown
# <Phase> Report: <increment-id>

Status: READY | BLOCKED | APPROVED | REJECTED
Blocker-Type: IMPLEMENTATION | TESTS | SPECIFICATION | SLICING | ENVIRONMENT | TOOLING
Inputs:
- <path>

Outputs:
- <path>

Checks:
- <command or review check>: PASS | FAIL | NOT RUN

Findings:
- <short, actionable finding>

Next action:
<exact action for the orchestrator>
```

Reports should describe facts and paths, not repeat the full specification or test
output. A blocked or rejected report must name the failing check and the artifact that
needs correction, and include exactly one `Blocker-Type` from the list above. Omit
`Blocker-Type` for `READY` and `APPROVED` reports. Use `TESTS` for incorrect test
fixtures/assertions, `SPECIFICATION` for contract defects, `IMPLEMENTATION` for code
defects, and `ENVIRONMENT` or `TOOLING` when human/platform intervention is needed.
Use `SLICING` for defects in a vertical-slice proposal.

## Bounded recovery

- The runner routes `IMPLEMENTATION` to `implementer`, and `TESTS` or
	`SPECIFICATION` to `planner`.
- The runner routes `SLICING` to `slicing-planner`.
- `ENVIRONMENT`, `TOOLING`, missing classifications, and unknown values stop the
	workflow for human attention.
- Each repair attempt receives a numbered report and consumes one retry from the
	configured per-phase retry budget.
- The runner stops when the retry budget is exhausted. Agents must not create their
	own recursive retry chains or invoke one another directly.

## Phase transitions

The orchestrator may route work only when the required report has the expected status:

1. `slicing-planner.md` with `READY` -> slicing reviewer.
2. `slicing-review.md` with `APPROVED` -> proposal commit.
3. `planner.md` with `READY` -> planner reviewer.
4. `planning-review.md` with `APPROVED` -> human review and baseline commit.
5. Human-approved baseline -> implementer.
6. `implementation.md` with `READY` -> implementation reviewer.
7. `implementation-review.md` with `APPROVED` -> human review and final commit.

The orchestrator must stop on `BLOCKED` or `REJECTED`. It must not silently repair a
review finding, skip a phase gate, or commit on behalf of the human.