"""Chronotype cohort table ``gold_chronotype_cohort`` (contract: specs/INC-008.md)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import duckdb

TABLE_NAME = "gold_chronotype_cohort"
MIN_VALID_DAYS = 14  # a participant mean is NULL below this many valid days (specs/INC-008.md 6)
VALID_COHORTS: tuple[str, ...] = ("A", "B")
FLAG_MISSING = "CHRONOTYPE_MISSING"
FLAG_UNEXPECTED = "CHRONOTYPE_UNEXPECTED"
FLAG_CONFLICT = "CHRONOTYPE_CONFLICT"

COHORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("participant_id", "VARCHAR"),
    ("chronotype_cohort", "VARCHAR"),
    ("cohort_flag", "VARCHAR"),
    ("window_days", "INTEGER"),
    ("sleep_valid_days", "INTEGER"),
    ("sleep_stage_valid_days", "INTEGER"),
    ("steps_valid_days", "INTEGER"),
    ("hr_valid_days", "INTEGER"),
    ("sleep_total_min_mean", "DOUBLE"),
    ("sleep_efficiency_pct_mean", "DOUBLE"),
    ("deep_sleep_pct_mean", "DOUBLE"),
    ("steps_daily_mean", "DOUBLE"),
    ("hr_daily_mean_bpm", "DOUBLE"),
)


def classify_cohort(chronotypes: Sequence[str | None]) -> tuple[str | None, str | None]:
    """Cohort and flag of one participant from the chronotype values of all its listings.

    Returns ``(cohort, flag)``: ``("A" | "B", None)`` or ``(None, FLAG_*)`` (specs/INC-008.md 3).
    """
    raise NotImplementedError


def mean_if_covered(values: Sequence[float], minimum: int = MIN_VALID_DAYS) -> float | None:
    """Mean of the daily ``values`` (one per valid day), or ``None`` below ``minimum`` values."""
    raise NotImplementedError


class ChronotypeCohort:
    """``incremental.DerivedTable`` that maintains ``gold_chronotype_cohort`` from silver."""

    name: str = TABLE_NAME

    def refresh(
        self,
        con: duckdb.DuckDBPyConnection,
        affected: Mapping[str, frozenset[date]] | None,
    ) -> None:
        """Create or update the table inside the caller's transaction (specs/INC-008.md 7)."""
        raise NotImplementedError


COHORT = ChronotypeCohort()
