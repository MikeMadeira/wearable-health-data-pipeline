---
name: vertical-slicing
description: Derive small, independently verifiable vertical slices from a broader data-engineering initiative.
---

# Vertical Slicing Skill

Use this skill when an intent describes a capability that is too broad for one
specification-and-implementation increment.

## Proposal rules

- Read the complete initiative intent and the current repository capabilities.
- Propose thin vertical slices, not horizontal technical layers.
- Each slice must deliver a testable result across the relevant boundary, even if
  later slices extend it.
- Give every slice one clear outcome, scope boundary, input/output contract, and
  verification approach.
- State dependencies explicitly and recommend an execution order.
- Prefer the smallest slice that can prove useful behavior.
- Preserve existing public APIs unless a slice explicitly requires a compatible
  extension.
- Identify work that is intentionally out of scope for each slice.
- Do not write source code, tests, formal specs, or stubs during slicing.

## Quality checks

Reject a proposal when a slice:

- is only a layer such as "build the validation module" with no usable outcome;
- depends on several future slices before it can be tested;
- combines unrelated user outcomes;
- has no concrete verification criteria;
- duplicates an existing pipeline capability;
- hides a breaking API or data contract change.

## Required proposal structure

Write `plans/<initiative-id>/vertical-slices.md` with:

1. Initiative summary and assumptions.
2. Current capabilities and identified gaps.
3. Ordered slice table with ID, outcome, dependencies, and verification.
4. Detailed scope and boundaries for every slice.
5. Recommended first slice and rationale.
6. Risks, unresolved questions, and explicitly deferred work.