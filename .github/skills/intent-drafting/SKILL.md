---
name: intent-drafting
description: Structure and question bank for interviewing a human to draft an intent or initiative intent.
---

# Intent Drafting Skill

Use this skill when helping a human define the next `intents/INC-xxx.md` or
`intents/INIT-xxx.md` before any spec, test, or code exists.

An intent is the one artifact a human must author. This skill structures how to
interview the human for it; it never fabricates the human's goal or constraints.

## Ground rules

- Ask questions before drafting. Never draft an intent from assumptions alone.
- Ask one topic at a time and wait for the human's answer.
- Ground every question in the current repository: read existing `intents/`,
   `plans/`, and `src/wearable_pipeline/` first so questions do not repeat known
  context or contradict prior decisions.
- Draft only after the required topics below have real answers, not guesses.
- Never create specs, tests, stubs, or source code during this interview.
- Never commit. The human reviews, edits, and commits the resulting file.

## Required topics for an increment intent (`INC-xxx`)

1. **Goal** — what capability or behavior should exist after this increment?
2. **Boundary** — is this one vertical slice, or does it need the slicing
   planner first?
3. **Requirements** — the concrete, testable behaviors expected.
4. **Constraints** — dependencies, existing signatures that must not change,
   standard-library-only rules, and anything explicitly out of scope.
5. **Verification expectations** — what a reviewer should be able to check to
   consider this done.

## Required topics for an initiative intent (`INIT-xxx`)

1. **Goal** — the broader capability or outcome.
2. **Desired end-to-end capability** — described as a short pipeline/flow, not
   as an implementation.
3. **Requirements** — cross-cutting requirements the eventual slices must
   satisfy.
4. **Constraints** — what must not be redesigned, and any global limits.
5. **Planning expectations** — what the slicing planner should resolve versus
   what the human is deciding now.

## Draft output

Write the draft to `intents/<id>-<slug>.md` using the same section structure as
existing intents in the repository (`Goal`, `Requirements`, `Constraints`, and
either `Verification Expectations` or `Planning Expectations`). Present the
draft back to the human for edits before they commit it themselves.
