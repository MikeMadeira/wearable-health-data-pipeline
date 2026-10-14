"""Weekly biostatistics table ``gold_biostat_weekly`` (contract: specs/INC-007.md)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import duckdb

from wearable_pipeline.sleep import SleepMetrics, compute_sleep_metrics

TABLE_NAME = "gold_biostat_weekly"
WEEK_LENGTH_DAYS = 7
MIN_VALID_DAYS = 4  # a measure is NULL below this many valid days of the week

WEEKLY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("participant_id", "VARCHAR"), ("week_id", "INTEGER"),
    ("period_start_date", "DATE"), ("period_end_date", "DATE"),
    ("valid_days", "INTEGER"), ("sleep_valid_days", "INTEGER"),
    ("sleep_stage_valid_days", "INTEGER"), ("steps_valid_days", "INTEGER"),
    ("steps_valid_minutes", "BIGINT"), ("hr_valid_days", "INTEGER"),
    ("hr_valid_minutes", "BIGINT"), ("fatigue_valid_days", "INTEGER"),
    ("stress_valid_days", "INTEGER"), ("readiness_valid_days", "INTEGER"),
    ("sleep_quality_valid_days", "INTEGER"), ("sleep_total_min_mean", "DOUBLE"),
    ("deep_sleep_pct_mean", "DOUBLE"), ("sleep_efficiency_pct_mean", "DOUBLE"),
    ("steps_total", "BIGINT"), ("steps_daily_mean", "DOUBLE"),
    ("steps_rest_to_moderate_minutes", "BIGINT"), ("steps_burst_minutes", "BIGINT"),
    ("hr_weekly_mean_bpm", "DOUBLE"), ("hr_weekly_min_bpm", "INTEGER"),
    ("hr_weekly_max_bpm", "INTEGER"), ("fatigue_score_mean", "DOUBLE"),
    ("stress_score_mean", "DOUBLE"), ("readiness_score_mean", "DOUBLE"),
    ("sleep_quality_score_mean", "DOUBLE"),
)


_COLUMN_NAMES = tuple(name for name, _ in WEEKLY_COLUMNS)
_REST_MODERATE_MAX = 30  # steps_per_minute <= 30 is at-rest-to-moderate (src/.../steps.py)
_SCORES = ("fatigue", "stress", "readiness", "sleep_quality")
_NO_SLEEP = SleepMetrics(None, None, None)


def week_count(study_days: int) -> int:
    """Number of whole seven-day weeks in a window of ``study_days`` days (never negative)."""
    return max(study_days, 0) // WEEK_LENGTH_DAYS


def week_bounds(study_start: date, week_id: int) -> tuple[date, date]:
    """First and last date (inclusive) of 1-based ``week_id``; ``ValueError`` if below 1."""
    if week_id < 1:
        raise ValueError(f"week_id must be at least 1, got {week_id}")
    first = study_start + timedelta(days=WEEK_LENGTH_DAYS * (week_id - 1))
    return first, first + timedelta(days=WEEK_LENGTH_DAYS - 1)


def week_of(study_start: date, study_days: int, day: date) -> int | None:
    """Week id of ``day``, or ``None`` before the start or outside the whole weeks."""
    offset = (day - study_start).days
    if offset < 0:
        return None
    week = offset // WEEK_LENGTH_DAYS + 1
    return week if week <= week_count(study_days) else None


def _in_scope(con: duckdb.DuckDBPyConnection, table: str, rule: str, alias: str) -> str:
    """SQL predicate: the silver row of ``table`` is eligible for a daily value."""
    del con, table
    return (f"NOT {alias}.is_orphan AND {alias}.local_date IS NOT NULL "
            f"AND NOT {alias}.is_out_of_window "
            f"AND NOT list_contains(string_split(coalesce({alias}.quality_flags, ''), ','), "
            f"'{rule}')")


_KEY_JOIN = ("JOIN weekly_keys k ON k.participant_id = {a}.participant_id "
             "AND {a}.local_date BETWEEN k.period_start_date AND k.period_end_date")


def _daily_steps(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, date], tuple[int, ...]]:
    """(total, valid minutes, rest-to-moderate minutes, burst minutes) per day with a value."""
    rows = con.execute(
        "WITH slots AS (SELECT s.participant_id AS pid, s.local_date AS d, "
        "date_trunc('minute', s.minute_ts) AS slot, min(s.steps_per_minute) AS v "
        "FROM silver_steps_minute s " + _KEY_JOIN.format(a="s")
        + " WHERE " + _in_scope(con, "silver_steps_minute", "STEPS_PARTICIPANT_FOLDER_MISMATCH", "s")
        + " GROUP BY 1, 2, 3) "
        "SELECT pid, d, CAST(sum(v) AS BIGINT), CAST(count(v) AS BIGINT), "
        f"CAST(count(*) FILTER (WHERE v <= {_REST_MODERATE_MAX}) AS BIGINT), "
        f"CAST(count(*) FILTER (WHERE v > {_REST_MODERATE_MAX}) AS BIGINT) "
        "FROM slots GROUP BY pid, d HAVING count(v) > 0").fetchall()
    return {(r[0], r[1]): tuple(r[2:]) for r in rows}


def _daily_heart_rate(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, date], tuple[int, ...]]:
    """(sum, valid minutes, min, max) of the valid minute values per day with a value."""
    rows = con.execute(
        "WITH slots AS (SELECT s.participant_id AS pid, s.local_date AS d, "
        "date_trunc('minute', s.minute_ts) AS slot, min(s.heart_rate_bpm_normalized) AS v "
        "FROM silver_heart_rate_minute s " + _KEY_JOIN.format(a="s")
        + " WHERE " + _in_scope(con, "silver_heart_rate_minute", "HR_PARTICIPANT_FOLDER_MISMATCH", "s")
        + " GROUP BY 1, 2, 3) "
        "SELECT pid, d, CAST(sum(v) AS BIGINT), CAST(count(v) AS BIGINT), min(v), max(v) "
        "FROM slots GROUP BY pid, d HAVING count(v) > 0").fetchall()
    return {(r[0], r[1]): tuple(r[2:]) for r in rows}


def _daily_survey(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, date], tuple[int | None, ...]]:
    """Per day the four scores (distinct non-NULL value over the contributing rows, else NULL)."""
    columns = ", ".join(
        f"CASE WHEN count(DISTINCT s.{n}_score) = 1 THEN min(s.{n}_score) END" for n in _SCORES)
    rows = con.execute(
        f"SELECT s.participant_id, s.local_date, {columns} FROM silver_wellness_survey s "
        + _KEY_JOIN.format(a="s")
        + " WHERE NOT s.is_orphan AND s.local_date IS NOT NULL AND NOT s.is_out_of_window "
        "GROUP BY 1, 2").fetchall()
    return {(r[0], r[1]): tuple(r[2:]) for r in rows}


def _daily_sleep(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, date], SleepMetrics]:
    """Day metrics as the sleep builder derives them (specs/INC-007.md section 4, D10)."""
    sessions = {
        (r[0], r[1]): r[2] for r in con.execute(
            "SELECT s.participant_id, s.local_date, count(*) FROM silver_sleep_sessions s "
            + _KEY_JOIN.format(a="s")
            + " WHERE NOT s.is_orphan AND s.local_date IS NOT NULL AND NOT s.is_out_of_window "
            "GROUP BY 1, 2").fetchall()}
    stage_rows = con.execute(
        "SELECT s.participant_id, s.local_date, s.stage_index, s.stage, s.duration_min, "
        "s.start_time IS NOT NULL AND s.end_time IS NOT NULL "
        "FROM silver_sleep_stages s " + _KEY_JOIN.format(a="s") + " ORDER BY s.rowid").fetchall()
    # a new session starts at every stage row whose stage_index is 1
    pairs: dict[tuple[str, date], list[list[tuple[str, int | None]]]] = {}
    for pid, day, index, stage, duration, timed in stage_rows:
        groups = pairs.setdefault((pid, day), [])
        if index == 1 or not groups:
            groups.append([])
        groups[-1].append((stage if stage is not None else "", duration if timed else None))
    daily: dict[tuple[str, date], SleepMetrics] = {}
    for key, count in sessions.items():
        groups = pairs.get(key, [])
        distinct = {compute_sleep_metrics(group) for group in groups}
        if count > len(groups):
            distinct.add(_NO_SLEEP)  # a session without stage rows has no metrics
        daily[key] = next(iter(distinct)) if len(distinct) == 1 else _NO_SLEEP
    return daily


def _mean(total: int | float, count: int) -> float:
    return total / count


def _week_row(pid: str, week: int, start: date,
              days: list[date],
              sleep: dict[tuple[str, date], SleepMetrics],
              steps: dict[tuple[str, date], tuple[int, ...]],
              hr: dict[tuple[str, date], tuple[int, ...]],
              survey: dict[tuple[str, date], tuple[int | None, ...]]) -> tuple[Any, ...]:
    """One weekly row; days are visited in ascending order so DOUBLE sums are reproducible."""
    first, last = week_bounds(start, week)
    sleep_vals = [sleep[(pid, d)] for d in days if (pid, d) in sleep]
    total_vals = [m.total_sleep_min for m in sleep_vals if m.total_sleep_min is not None]
    deep_vals = [m for m in sleep_vals if m.deep_sleep_pct is not None]
    step_days = [steps[(pid, d)] for d in days if (pid, d) in steps]
    hr_days = [hr[(pid, d)] for d in days if (pid, d) in hr]
    score_vals = [[survey[(pid, d)][i] for d in days
                   if (pid, d) in survey and survey[(pid, d)][i] is not None]
                  for i in range(len(_SCORES))]
    valid_days = sum(
        1 for d in days
        if ((pid, d) in sleep and sleep[(pid, d)].total_sleep_min is not None)
        or (pid, d) in steps or (pid, d) in hr)

    enough = MIN_VALID_DAYS
    sleep_mean = _mean(sum(total_vals), len(total_vals)) if len(total_vals) >= enough else None
    deep_mean = eff_mean = None
    if len(deep_vals) >= enough:
        deep_mean = _mean(sum(m.deep_sleep_pct for m in deep_vals), len(deep_vals))
        eff_mean = _mean(sum(m.sleep_efficiency_pct for m in deep_vals), len(deep_vals))
    steps_total = steps_mean = rest = burst = None
    if len(step_days) >= enough:
        steps_total = sum(s[0] for s in step_days)
        steps_mean = _mean(steps_total, len(step_days))
        rest = sum(s[2] for s in step_days)
        burst = sum(s[3] for s in step_days)
    hr_minutes = sum(h[1] for h in hr_days)
    hr_mean = hr_min = hr_max = None
    if len(hr_days) >= enough:
        hr_mean = _mean(sum(h[0] for h in hr_days), hr_minutes)
        hr_min = min(h[2] for h in hr_days)
        hr_max = max(h[3] for h in hr_days)
    score_means = [_mean(sum(v), len(v)) if len(v) >= enough else None for v in score_vals]
    return (
        pid, week, first, last, valid_days, len(total_vals), len(deep_vals), len(step_days),
        sum(s[1] for s in step_days), len(hr_days), hr_minutes,
        *(len(v) for v in score_vals),
        sleep_mean, deep_mean, eff_mean, steps_total, steps_mean, rest, burst,
        hr_mean, hr_min, hr_max, *score_means,
    )


def _compute(con: duckdb.DuckDBPyConnection, start: date,
             keys: list[tuple[str, int]]) -> list[tuple[Any, ...]]:
    """Weekly rows of the given participant-weeks (the one code path of every refresh)."""
    if not keys:
        return []
    con.execute("CREATE OR REPLACE TEMP TABLE weekly_keys (participant_id VARCHAR, "
                "week_id INTEGER, period_start_date DATE, period_end_date DATE)")
    con.executemany("INSERT INTO weekly_keys VALUES (?, ?, ?, ?)",
                    [(pid, week, *week_bounds(start, week)) for pid, week in keys])
    try:
        sleep, steps = _daily_sleep(con), _daily_steps(con)
        hr, survey = _daily_heart_rate(con), _daily_survey(con)
    finally:
        con.execute("DROP TABLE IF EXISTS weekly_keys")
    rows = []
    for pid, week in sorted(keys):
        first = week_bounds(start, week)[0]
        days = [first + timedelta(days=i) for i in range(WEEK_LENGTH_DAYS)]
        rows.append(_week_row(pid, week, start, days, sleep, steps, hr, survey))
    return rows


def _layout_matches(con: duckdb.DuckDBPyConnection) -> bool:
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = ?", [TABLE_NAME]).fetchone()[0]
    if not exists:
        return False
    described = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {TABLE_NAME}").fetchall()]
    return described == list(WEEKLY_COLUMNS)


def _create(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    con.execute(f"CREATE TABLE {TABLE_NAME} ("
                + ", ".join(f"{name} {kind}" for name, kind in WEEKLY_COLUMNS)
                + ", PRIMARY KEY (participant_id, week_id))")


def _merge(con: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]], prune: bool) -> None:
    con.execute(f"CREATE OR REPLACE TEMP TABLE weekly_stage AS SELECT * FROM {TABLE_NAME} LIMIT 0")
    if rows:
        marks = ",".join("?" * len(_COLUMN_NAMES))
        con.executemany(f"INSERT INTO weekly_stage VALUES ({marks})", rows)
    updates = ", ".join(f"{name} = s.{name}" for name in _COLUMN_NAMES[2:])
    con.execute(
        f"MERGE INTO {TABLE_NAME} AS g USING weekly_stage AS s "
        "ON g.participant_id = s.participant_id AND g.week_id = s.week_id "
        f"WHEN MATCHED THEN UPDATE SET {updates} "
        "WHEN NOT MATCHED THEN INSERT BY NAME")
    if prune:
        con.execute(f"DELETE FROM {TABLE_NAME} WHERE (participant_id, week_id) NOT IN "
                    "(SELECT participant_id, week_id FROM weekly_stage)")
    con.execute("DROP TABLE weekly_stage")


class WeeklyBiostat:
    """``incremental.DerivedTable`` that maintains ``gold_biostat_weekly`` from silver."""

    name: str = TABLE_NAME

    def refresh(
        self,
        con: duckdb.DuckDBPyConnection,
        affected: Mapping[str, frozenset[date]] | None,
    ) -> None:
        """Create or update the table inside the caller's transaction (specs/INC-007.md 7)."""
        rebuilt = not _layout_matches(con)
        if rebuilt:
            _create(con)
        start, days = con.execute("SELECT study_start, study_days FROM run_window").fetchone()
        enrolled = [r[0] for r in con.execute(
            "SELECT DISTINCT participant_id FROM silver_participants "
            "ORDER BY participant_id").fetchall()]
        weeks = week_count(days)
        if affected is None or rebuilt:
            keys = [(pid, week) for pid in enrolled for week in range(1, weeks + 1)]
            _merge(con, _compute(con, start, keys), prune=True)
            return
        enrolled_set = set(enrolled)
        keys_set = set()
        for pid, dates in affected.items():
            if pid not in enrolled_set:
                continue
            for day in dates:
                week = week_of(start, days, day)
                if week is not None:
                    keys_set.add((pid, week))
        if keys_set:
            _merge(con, _compute(con, start, sorted(keys_set)), prune=False)


WEEKLY = WeeklyBiostat()
