---
name: sdd-tdd
description: Guidelines and conventions for deriving formal specifications and red-failing TDD tests from intent files in wearable-health-data-pipeline.
---

# Spec-Driven TDD Skill for Wearable Data

This skill guides the translation of a wearable-pipeline increment intent into a formal contract and executable tests.

## 1. Spec Derivation Rules (`specs/INC-xxx.md`)
When deriving a specification from an intent file (`intents/INC-xxx.md`):
- **Document Structure**:
  - `Title & Status`: Identifier, status (`Draft` or `Approved`), source intent link.
  - `Data Contract`: State each input/output grain, keys, schema, date/time semantics, and expected cardinalities.
  - `Quality Rules`: Define measurable checks, severity, and whether affected data is rejected, quarantined, flagged, or excluded.
  - `Operational Contract`: Define deterministic rerun behavior, affected partitions, and how failures are surfaced.
  - `Examples`: Include representative records or compact tabular examples without copying participant data into tests.

## 2. Test-Driven Development (TDD) Rules (`tests/test_*.py`)
- **Strict Red Baseline (CRITICAL)**:
  - Tests MUST assert against expected final return values (e.g. `assert function(inputs) == expected_output`).
  - **NEVER** write `with pytest.raises(NotImplementedError)` in test assertions.
  - For stub-based increments, running `pytest -q` MUST fail with `NotImplementedError` directly from the stub call. For other increments, establish a meaningful failing baseline tied to the missing behavior.
  - When the function is later implemented, the tests turn green without modifying the test file.
- **Coverage Requirements**:
  - Positive cases and relevant malformed, missing, duplicate, boundary, and out-of-window records.
  - Join cardinality and output-grain checks for aggregations.
  - Timezone, local-date, and minute-boundary cases when timestamps are in scope.
  - Quality outcomes and raw-data preservation when records are invalid or suspect.
  - Idempotent reruns and deterministic outputs when writing or replacing data.

## 3. Code Stubs (`src/wearable_pipeline/*.py`)
- Define the smallest useful contract with accurate type hints and a docstring.
- Export public names from the package when appropriate.
- A stub body MUST only be:
  ```python
  raise NotImplementedError
  ```
