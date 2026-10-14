"""Chronotype cohort table ``gold_chronotype_cohort`` (contract: specs/INC-008.md)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import duckdb

from wearable_pipeline.weekly import _daily_heart_rate, _daily_sleep, _daily_steps

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
    present = {v for v in chronotypes if v is not None and v.strip() != ""}
    if not present:
        return None, FLAG_MISSING
    if len(present) > 1:
        return None, FLAG_CONFLICT
    (value,) = present
    if value in VALID_COHORTS:
        return value, None
    return None, FLAG_UNEXPECTED


def mean_if_covered(values: Sequence[float], minimum: int = MIN_VALID_DAYS) -> float | None:
    """Mean of the daily ``values`` (one per valid day), or ``None`` below ``minimum`` values."""
    if len(values) < minimum:
        return None
    return sum(values) / len(values)


_COLUMN_NAMES = tuple(name for name, _ in COHORT_COLUMNS)


def _layout_matches(con: duckdb.DuckDBPyConnection) -> bool:
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = ?", [TABLE_NAME]).fetchone()[0]
    if not exists:
        return False
    described = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {TABLE_NAME}").fetchall()]
    return described == list(COHORT_COLUMNS)


def _create(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    con.execute(f"CREATE TABLE {TABLE_NAME} ("
                + ", ".join(f"{name} {kind}" for name, kind in COHORT_COLUMNS)
                + ", PRIMARY KEY (participant_id))")


def _compute(con: duckdb.DuckDBPyConnection, start: date, study_days: int,
             pids: list[str]) -> list[tuple[Any, ...]]:
    """Cohort rows of the given enrolled participants (the one code path of every refresh)."""
    if not pids:
        return []
    end = start + timedelta(days=study_days - 1)
    # the per-day helpers of weekly.py restrict their silver reads through this key table
    con.execute("CREATE OR REPLACE TEMP TABLE weekly_keys (participant_id VARCHAR, "
                "week_id INTEGER, period_start_date DATE, period_end_date DATE)")
    con.executemany("INSERT INTO weekly_keys VALUES (?, 1, ?, ?)",
                    [(pid, start, end) for pid in pids])
    try:
        sleep, steps = _daily_sleep(con), _daily_steps(con)
        hr = _daily_heart_rate(con)
        chronotypes: dict[str, list[str | None]] = {}
        for pid, chronotype in con.execute(
                "SELECT participant_id, chronotype FROM silver_participants "
                "ORDER BY rowid").fetchall():
            chronotypes.setdefault(pid, []).append(chronotype)
    finally:
        con.execute("DROP TABLE IF EXISTS weekly_keys")
    days = [start + timedelta(days=i) for i in range(max(study_days, 0))]
    rows = []
    for pid in sorted(pids):
        cohort, flag = classify_cohort(chronotypes.get(pid, []))
        metrics = [sleep[(pid, d)] for d in days if (pid, d) in sleep]
        totals = [m.total_sleep_min for m in metrics if m.total_sleep_min is not None]
        staged = [m for m in metrics if m.deep_sleep_pct is not None]
        step_totals = [steps[(pid, d)][0] for d in days if (pid, d) in steps]
        hr_means = [hr[(pid, d)][0] / hr[(pid, d)][1] for d in days if (pid, d) in hr]
        rows.append((
            pid, cohort, flag, study_days,
            len(totals), len(staged), len(step_totals), len(hr_means),
            mean_if_covered(totals),
            mean_if_covered([m.sleep_efficiency_pct for m in staged]),
            mean_if_covered([m.deep_sleep_pct for m in staged]),
            mean_if_covered(step_totals),
            mean_if_covered(hr_means),
        ))
    return rows


def _merge(con: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]], prune: bool) -> None:
    con.execute(f"CREATE OR REPLACE TEMP TABLE cohort_stage AS SELECT * FROM {TABLE_NAME} LIMIT 0")
    if rows:
        marks = ",".join("?" * len(_COLUMN_NAMES))
        con.executemany(f"INSERT INTO cohort_stage VALUES ({marks})", rows)
    updates = ", ".join(f"{name} = s.{name}" for name in _COLUMN_NAMES[1:])
    con.execute(
        f"MERGE INTO {TABLE_NAME} AS g USING cohort_stage AS s "
        "ON g.participant_id = s.participant_id "
        f"WHEN MATCHED THEN UPDATE SET {updates} "
        "WHEN NOT MATCHED THEN INSERT BY NAME")
    if prune:
        con.execute(f"DELETE FROM {TABLE_NAME} WHERE participant_id NOT IN "
                    "(SELECT participant_id FROM cohort_stage)")
    con.execute("DROP TABLE cohort_stage")


class ChronotypeCohort:
    """``incremental.DerivedTable`` that maintains ``gold_chronotype_cohort`` from silver."""

    name: str = TABLE_NAME

    def refresh(
        self,
        con: duckdb.DuckDBPyConnection,
        affected: Mapping[str, frozenset[date]] | None,
    ) -> None:
        """Create or update the table inside the caller's transaction (specs/INC-008.md 7)."""
        rebuilt = not _layout_matches(con)
        if rebuilt:
            _create(con)
        start, days = con.execute("SELECT study_start, study_days FROM run_window").fetchone()
        enrolled = [r[0] for r in con.execute(
            "SELECT DISTINCT participant_id FROM silver_participants "
            "ORDER BY participant_id").fetchall()]
        if affected is None or rebuilt:
            _merge(con, _compute(con, start, days, enrolled), prune=True)
            return
        enrolled_set = set(enrolled)
        last = start + timedelta(days=days - 1)
        pids = sorted(pid for pid, dates in affected.items()
                      if pid in enrolled_set and any(start <= d <= last for d in dates))
        if pids:
            _merge(con, _compute(con, start, days, pids), prune=False)


COHORT = ChronotypeCohort()
