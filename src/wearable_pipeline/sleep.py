"""Sleep silver layer, rules and daily metrics (pure; the pipeline owns all I/O)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

# rule_id -> (source, handling, severity, reason)
SLEEP_RULES: dict[str, tuple[str, str, str, str]] = {
    "SLEEP_ORPHAN_PARTICIPANT": (
        "sleep", "exclude_from_output", "medium",
        "sleep participant_id is not enrolled in participants.csv"),
    "SLEEP_DUPLICATE_PARTICIPANT_DAY": (
        "sleep", "withhold_field", "high",
        "participant_id and date occur in more than one sleep session"),
    "SLEEP_DATE_UNPARSEABLE": (
        "sleep", "exclude_from_output", "medium",
        "sleep date is not a valid YYYY-MM-DD calendar date"),
    "SLEEP_DATE_OUT_OF_WINDOW": (
        "sleep", "exclude_from_output", "medium",
        "sleep date is outside the study window"),
    "SLEEP_SESSION_FIELD_INVALID": (
        "sleep", "withhold_field", "medium",
        "sleep_onset, sleep_end, efficiency_pct or restlessness is missing or invalid"),
    "SLEEP_STAGE_INVALID": (
        "sleep", "withhold_field", "medium",
        "a sleep stage has an unknown name, an invalid duration or an unparseable time"),
    "SLEEP_STAGE_DURATION_MISMATCH": (
        "sleep", "flag", "medium",
        "a sleep stage duration differs from its timestamp interval"),
    "SLEEP_STAGE_GAP_OR_OVERLAP": (
        "sleep", "flag", "medium",
        "consecutive sleep stages have a gap or overlap"),
    "SLEEP_SPAN_OUTLIER": (
        "sleep", "flag", "medium",
        "sleep session span is longer than 12 hours"),
    "SLEEP_FINAL_STAGE_MISALIGNED": (
        "sleep", "flag", "medium",
        "final sleep stage does not start at sleep_end"),
    "SLEEP_EFFICIENCY_MISMATCH": (
        "sleep", "flag", "medium",
        "supplied efficiency_pct differs from the derived efficiency by more than 1 point"),
    "SLEEP_ONSET_NOT_MINUTE_ALIGNED": (
        "sleep", "flag", "medium",
        "sleep_onset is not aligned to a whole minute"),
}


@dataclass(frozen=True)
class SleepMetrics:
    """Session or day level sleep metrics; ``None`` marks a withheld value."""

    total_sleep_min: int | None
    deep_sleep_pct: float | None
    sleep_efficiency_pct: float | None


@dataclass(frozen=True)
class SleepDay:
    """Gold sleep columns for one participant-day with at least one contributing session."""

    total_sleep_min: int | None
    deep_sleep_pct: float | None
    sleep_efficiency_pct: float | None
    sleep_session_count: int
    sleep_quality_flags: str | None


@dataclass(frozen=True)
class SleepLayer:
    """Rows of the sleep silver tables, rejects, rule counts and per-day gold values."""

    sessions: list[tuple[Any, ...]]
    stages: list[tuple[Any, ...]]
    rejects: list[tuple[str, str, int, Any]]
    failed: Counter[str]
    checked: int
    daily: dict[tuple[str, date], SleepDay]


def compute_sleep_metrics(stages: Sequence[tuple[str, int | None]]) -> SleepMetrics:
    """Derive total sleep minutes, deep-sleep share and efficiency from stage durations.

    Each pair is ``(stage name, duration or None when invalid)``; see ``specs/INC-003.md``
    section 5.
    """
    raise NotImplementedError


def build_sleep_layer(
    files: Mapping[str, Sequence[Any]],
    enrolled: Collection[str],
    window: Sequence[date],
) -> SleepLayer:
    """Build sleep silver rows, quality outcomes and daily metrics from parsed sleep files.

    ``files`` maps relative path to the parsed JSON array; see ``specs/INC-003.md``.
    """
    raise NotImplementedError
