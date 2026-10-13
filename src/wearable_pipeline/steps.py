"""Steps silver layer, rules and daily totals (pure; the pipeline owns all I/O)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

# rule_id -> (source, handling, severity, reason)
STEPS_RULES: dict[str, tuple[str, str, str, str]] = {
    "STEPS_ORPHAN_PARTICIPANT": (
        "steps", "exclude_from_output", "medium",
        "steps participant_id is not enrolled in participants.csv"),
    "STEPS_PARTICIPANT_FOLDER_MISMATCH": (
        "steps", "exclude_from_output", "medium",
        "steps participant_id differs from its participant folder"),
    "STEPS_TIMESTAMP_UNPARSEABLE": (
        "steps", "exclude_from_output", "medium",
        "steps timestamp is missing or not a valid ISO 8601 timestamp"),
    "STEPS_TIMESTAMP_OUT_OF_WINDOW": (
        "steps", "exclude_from_output", "medium",
        "steps timestamp is outside the study window"),
    "STEPS_VALUE_INVALID": (
        "steps", "withhold_field", "medium",
        "steps is missing, not an integer, or outside the 32-bit integer range"),
    "STEPS_NEGATIVE": (
        "steps", "withhold_field", "high",
        "steps is negative"),
    "STEPS_UNIT_CONVERTED": (
        "steps", "convert", "low",
        "steps_per_hour value converted to steps per minute"),
    "STEPS_UNIT_UNSUPPORTED": (
        "steps", "withhold_field", "high",
        "steps _unit is not steps_per_hour or the hourly value is not divisible by 60"),
    "STEPS_DUPLICATE_MINUTE_CONFLICT": (
        "steps", "withhold_field", "high",
        "records for one participant-minute have different values"),
    "STEPS_DUPLICATE_MINUTE_EXACT": (
        "steps", "flag", "medium",
        "records for one participant-minute have identical values"),
    "STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED": (
        "steps", "flag", "medium",
        "steps timestamp is not aligned to a whole minute"),
}


@dataclass(frozen=True)
class StepsDay:
    """Gold step columns for one participant-day with at least one in-scope record."""

    total_steps: int | None
    steps_valid_minute_count: int
    minutes_rest_to_moderate: int
    minutes_burst: int
    steps_quality_flags: str | None


@dataclass(frozen=True)
class StepsLayer:
    """Rows of the steps silver table, rejects, rule counts and per-day gold values."""

    rows: list[tuple[Any, ...]]
    rejects: list[tuple[str, str, int, Any]]
    failed: Counter[str]
    checked: int
    daily: dict[tuple[str, date], StepsDay]


def build_steps_layer(
    files: Mapping[str, Sequence[Any]],
    enrolled: Collection[str],
    window: Sequence[date],
) -> StepsLayer:
    """Build steps silver rows, quality outcomes and daily totals from parsed steps files.

    ``files`` maps relative path to the parsed JSON array; see ``specs/INC-004.md``.
    """
    raise NotImplementedError
