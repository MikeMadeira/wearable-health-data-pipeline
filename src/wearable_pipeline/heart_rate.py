"""Heart-rate silver layer, rules and daily aggregates (pure; the pipeline owns all I/O)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

# rule_id -> (source, handling, severity, reason)
HEART_RATE_RULES: dict[str, tuple[str, str, str, str]] = {
    "HR_ORPHAN_PARTICIPANT": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate participant_id is not enrolled in participants.csv"),
    "HR_PARTICIPANT_FOLDER_MISMATCH": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate participant_id differs from its participant folder"),
    "HR_TIMESTAMP_UNPARSEABLE": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate timestamp is missing or not a valid ISO 8601 timestamp"),
    "HR_TIMESTAMP_OUT_OF_WINDOW": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate timestamp is outside the study window"),
    "HR_VALUE_INVALID": (
        "heart_rate", "withhold_field", "medium",
        "heart_rate_bpm is missing, not an integer, or outside the 32-bit integer range"),
    "HR_VALUE_OUT_OF_RANGE": (
        "heart_rate", "withhold_field", "high",
        "heart_rate_bpm is outside the range 40 to 200"),
    "HR_STUCK_SENSOR_RUN": (
        "heart_rate", "withhold_field", "medium",
        "heart_rate_bpm repeats for at least 60 consecutive minutes"),
    "HR_TIMESTAMP_CONVERTED": (
        "heart_rate", "convert", "low",
        "heart rate timestamp with Z or an offset converted to UTC-05:00"),
    "HR_DUPLICATE_MINUTE_CONFLICT": (
        "heart_rate", "withhold_field", "high",
        "records for one participant-minute have different values"),
    "HR_DUPLICATE_MINUTE_EXACT": (
        "heart_rate", "flag", "medium",
        "records for one participant-minute have identical values"),
    "HR_TIMESTAMP_NOT_MINUTE_ALIGNED": (
        "heart_rate", "flag", "medium",
        "heart rate timestamp is not aligned to a whole minute"),
}


@dataclass(frozen=True)
class HeartRateDay:
    """Gold heart-rate columns for one participant-day with at least one in-scope record."""

    hr_mean_bpm: float | None
    hr_min_bpm: int | None
    hr_max_bpm: int | None
    hr_valid_minute_count: int
    hr_quality_flags: str | None


@dataclass(frozen=True)
class HeartRateLayer:
    """Rows of the heart-rate silver table, rejects, rule counts and per-day gold values."""

    rows: list[tuple[Any, ...]]
    rejects: list[tuple[str, str, int, Any]]
    failed: Counter[str]
    checked: int
    daily: dict[tuple[str, date], HeartRateDay]


def build_heart_rate_layer(
    files: Mapping[str, Sequence[Any]],
    enrolled: Collection[str],
    window: Sequence[date],
) -> HeartRateLayer:
    """Build heart-rate silver rows, quality outcomes and daily aggregates.

    ``files`` maps relative path to the parsed JSON array; see ``specs/INC-005.md``.
    """
    raise NotImplementedError
