"""Behavior tests for INC-007 `gold_biostat_weekly` (see specs/INC-007.md). Synthetic fixtures."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from tests.test_incremental import (
    HR, SLEEP, STEPS, alter_type, drop_table, events_path, hr, read_events, st,
    state, swap_first_two_columns, write_events,
)
from tests.test_sleep import session
from tests.test_walking_skeleton import (
    P001, P002, PARTICIPANT_COLUMNS, START, make_data, survey_row, tree_hashes, write_csv,
)
from wearable_pipeline import PipelineError, incremental, main, run_pipeline
from wearable_pipeline.incremental import (
    OUTCOME_FULL, OUTCOME_INCREMENTAL, OUTCOME_NOOP, run_incremental,
)
from wearable_pipeline.weekly import (
    MIN_VALID_DAYS, TABLE_NAME, WEEK_LENGTH_DAYS, WEEKLY, WEEKLY_COLUMNS, WeeklyBiostat,
    week_bounds, week_count, week_of,
)

DAYS = 14
D = [START + timedelta(days=i) for i in range(40)]
P003 = ["P003", "FBT-0003-CCCCCCCC", "40", "170", "female", "C", "160"]

KEY = ("participant_id", "week_id")


# ---------------------------------------------------------------- helpers

def full(data: Path, out: Path, days: int = DAYS) -> None:
    run_pipeline(data, out, study_start=START, study_days=days)


def inc(data: Path, out: Path, days: int = DAYS):
    return run_incremental(data, out, study_start=START, study_days=days)


def refresh(out: Path, affected=None) -> None:
    """Run the weekly refresh directly (this is what reaches the stub while it is a stub)."""
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        WEEKLY.refresh(con, affected)
    finally:
        con.close()


def edit_db(out: Path, *statements: str) -> None:
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()


def table_rows(out: Path, sql: str) -> tuple[list[str], list[tuple]]:
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        cursor = con.execute(sql)
        return [c[0] for c in cursor.description], cursor.fetchall()
    finally:
        con.close()


def weekly_rows(out: Path) -> dict[tuple[str, int], dict]:
    names, rows = table_rows(out, f"SELECT * FROM {TABLE_NAME}")
    result = {(r[0], r[1]): dict(zip(names, r)) for r in rows}
    assert len(result) == len(rows), "duplicate (participant_id, week_id) keys"
    return result


def weekly_columns(out: Path) -> list[tuple[str, str]]:
    _, rows = table_rows(out, f"DESCRIBE {TABLE_NAME}")
    return [(r[0], r[1]) for r in rows]


def daily_rows(out: Path) -> list[dict]:
    names, rows = table_rows(out, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    return [dict(zip(names, r)) for r in rows]


def changed_keys(before: dict, after: dict) -> set:
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


def approx(value):
    return pytest.approx(value, rel=1e-9, abs=1e-9)


def fresh_full(data: Path, tmp_path: Path, days: int = DAYS) -> Path:
    out = tmp_path / f"fresh-{len(list(tmp_path.glob('fresh-*')))}"
    full(data, out, days)
    return out


def assert_equals_full(data: Path, out: Path, tmp_path: Path, days: int = DAYS) -> None:
    expected = state(fresh_full(data, tmp_path, days))
    actual = state(out)
    assert TABLE_NAME in expected["tables"]
    assert actual["columns"] == expected["columns"]
    for name, rows in expected["tables"].items():
        assert actual["tables"][name] == rows, name
    assert actual["tables"].keys() == expected["tables"].keys()
    assert actual["report"] == expected["report"]


def write_sessions(data: Path, pid: str, sessions: list) -> None:
    write_events(data, pid, SLEEP, sessions)


def stages(light: int, deep: int, rem: int, awake: int) -> list[tuple[str, int]]:
    return [("light", light), ("deep", deep), ("rem", rem), ("awake", awake)]


def fixture_a(root: Path) -> Path:
    """Two participants, a 14-day window (weeks 1 and 2), hand-computed in the tests below."""
    survey = [
        survey_row("P001", D[0].isoformat(), ["1", "2", "10", "3"]),
        survey_row("P001", D[1].isoformat(), ["2", "x", "8", "4"]),       # invalid stress
        survey_row("P001", D[2].isoformat(), ["3", "3", "6", "2"]),       # duplicate day:
        survey_row("P001", D[2].isoformat(), ["3", "3", "6", "5"]),       # quality differs
        survey_row("P001", D[3].isoformat(), ["5", "4", "0", "1"]),
        survey_row("P001", D[20].isoformat(), ["5", "5", "5", "5"]),      # out of window
        survey_row("P002", D[0].isoformat(), ["2", "4", "6", "5"]),
        survey_row("P002", D[1].isoformat(), ["2", "4", "6", "5"]),
        survey_row("P999", D[0].isoformat(), ["5", "5", "5", "5"]),       # orphan
    ]
    data = make_data(root, survey=survey)
    write_events(data, "P001", HR, [
        hr(D[0], 0, 60), hr(D[0], 1, 70), hr(D[1], 0, 80), hr(D[1], 5, 300),
        hr(D[2], 0, 90), hr(D[2], 0, 90), hr(D[2], 1, 100), hr(D[2], 2, 110), hr(D[3], 0, 50),
        hr(D[7], 0, 70), hr(D[8], 0, 70), hr(D[9], 0, 70), hr(D[20], 0, 70)])
    write_events(data, "P001", STEPS, [
        st(D[0], 0, 10), st(D[0], 1, 20), st(D[1], 0, 100), st(D[2], 0, 5), st(D[3], 0, 0),
        st(D[4], 0, "abc"), st(D[5], 0, 7), st(D[5], 0, 8), st(D[7], 0, 10), st(D[8], 0, 20)])
    write_sessions(data, "P001", [
        session("P001", D[0], stages(200, 100, 100, 100)),
        session("P001", D[1], stages(240, 120, 120, 20)),
        session("P001", D[2], stages(150, 50, 100, 100)),
        session("P001", D[3], stages(200, 100, 100, 100)),                # duplicate day,
        session("P001", D[3], stages(100, 100, 100, 100)),                # conflicting metrics
        session("P001", D[4], stages(300, 60, 60, 0)),                    # invalid awake stage
        session("P001", D[5], [("bogus", 100)])])                         # invalid stage name
    write_sessions(data, "P002", [session("P002", D[i], stages(200, 100, 100, 100))
                                  for i in range(4)])
    write_events(data, "P999", HR, [hr(D[0], 0, 99)])
    return data


def built(tmp_path: Path) -> tuple[Path, Path]:
    """Fixture A, full run, then the weekly refresh executed directly."""
    data = fixture_a(tmp_path)
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    return data, out


def prepared(tmp_path: Path) -> tuple[Path, Path]:
    """Fixture A after a plain full run (what an incremental run would start from)."""
    data = fixture_a(tmp_path)
    out = tmp_path / "out"
    full(data, out)
    assert len(weekly_rows(out)) == 4
    return data, out


# ---------------------------------------------------------------- pure functions

def test_constants_and_registration():
    assert TABLE_NAME == "gold_biostat_weekly"
    assert WEEK_LENGTH_DAYS == 7
    assert MIN_VALID_DAYS == 4
    assert isinstance(WEEKLY, WeeklyBiostat)
    assert WEEKLY.name == TABLE_NAME
    assert [t.name for t in incremental.DERIVED_TABLES] == [TABLE_NAME]
    assert incremental.DERIVED_TABLES[0] is WEEKLY


def test_weekly_columns_are_the_specified_schema():
    names = [n for n, _ in WEEKLY_COLUMNS]
    assert names[:5] == ["participant_id", "week_id", "period_start_date", "period_end_date",
                         "valid_days"]
    assert dict(WEEKLY_COLUMNS)["week_id"] == "INTEGER"
    assert dict(WEEKLY_COLUMNS)["period_start_date"] == "DATE"
    assert len(names) == len(set(names)) == 29


@pytest.mark.parametrize("days, weeks", [(0, 0), (1, 0), (6, 0), (7, 1), (10, 1), (13, 1),
                                         (14, 2), (27, 3), (28, 4), (29, 4), (35, 5)])
def test_week_count_is_whole_weeks_only(days, weeks):
    assert week_count(days) == weeks


@pytest.mark.parametrize("week_id, first, last", [
    (1, date(2026, 1, 8), date(2026, 1, 14)), (2, date(2026, 1, 15), date(2026, 1, 21)),
    (3, date(2026, 1, 22), date(2026, 1, 28)), (4, date(2026, 1, 29), date(2026, 2, 4))])
def test_week_bounds_of_the_default_window(week_id, first, last):
    assert week_bounds(START, week_id) == (first, last)


@pytest.mark.parametrize("week_id", [0, -1])
def test_week_bounds_rejects_ids_below_one(week_id):
    with pytest.raises(ValueError):
        week_bounds(START, week_id)


@pytest.mark.parametrize("offset, expected", [
    (-1, None), (0, 1), (6, 1), (7, 2), (13, 2), (14, 3), (27, 4), (28, None), (100, None)])
def test_week_of_on_the_default_window(offset, expected):
    assert week_of(START, 28, START + timedelta(days=offset)) == expected


@pytest.mark.parametrize("offset, expected", [(6, 1), (7, None), (9, None), (13, None)])
def test_week_of_ignores_the_trailing_partial_block(offset, expected):
    assert week_of(START, 10, START + timedelta(days=offset)) == expected


def test_week_of_is_none_for_a_window_below_one_week():
    assert week_of(START, 6, START) is None


# ---------------------------------------------------------------- grain, keys, schema

def test_default_window_gives_200_rows_with_the_four_week_keys(tmp_path):
    participants = [[f"P{i:03d}", f"FBT-{i:04d}-XXXXXXXX", "30", "170", "male", "A", "180"]
                    for i in range(1, 51)]
    data = make_data(tmp_path, participants=participants, survey=[])
    out = tmp_path / "out"
    run_pipeline(data, out)
    rows = weekly_rows(out)
    assert len(rows) == 200
    assert {k[1] for k in rows} == {1, 2, 3, 4}
    assert {k[0] for k in rows} == {p[0] for p in participants}
    expected = {1: ("2026-01-08", "2026-01-14"), 2: ("2026-01-15", "2026-01-21"),
                3: ("2026-01-22", "2026-01-28"), 4: ("2026-01-29", "2026-02-04")}
    for (pid, week), row in rows.items():
        assert (row["period_start_date"].isoformat(),
                row["period_end_date"].isoformat()) == expected[week]
    assert table_rows(out, f"SELECT count(DISTINCT (participant_id, week_id)) FROM "
                           f"{TABLE_NAME}")[1] == [(200,)]


def test_pipeline_writes_the_table_on_every_run_with_the_specified_columns(tmp_path):
    data, out = prepared(tmp_path)
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)
    assert len(weekly_rows(out)) == 4


def test_unenrolled_participants_get_no_rows_and_enrolled_ones_always_do(tmp_path):
    data, out = prepared(tmp_path)
    assert {k for k in weekly_rows(out)} == {("P001", 1), ("P001", 2), ("P002", 1), ("P002", 2)}


def test_duplicate_participant_listing_gives_one_set_of_rows(tmp_path):
    data = make_data(tmp_path, participants=[P001, P001, P002], survey=[])
    out = tmp_path / "out"
    full(data, out)
    assert set(weekly_rows(out)) == {("P001", 1), ("P001", 2), ("P002", 1), ("P002", 2)}


@pytest.mark.parametrize("days, weeks", [(1, 0), (6, 0), (7, 1), (10, 1), (14, 2), (27, 3),
                                         (28, 4)])
def test_window_that_is_not_a_multiple_of_seven_emits_whole_weeks_only(tmp_path, days, weeks):
    data = make_data(tmp_path, survey=[])
    write_events(data, "P001", HR, [hr(D[i], 0, 70) for i in range(min(days, 40))])
    out = tmp_path / "out"
    full(data, out, days)
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)  # table exists even when empty
    rows = weekly_rows(out)
    assert set(rows) == {(p, w) for p in ("P001", "P002") for w in range(1, weeks + 1)}
    if weeks:
        # the trailing partial block never contributes to the last whole week
        assert rows[("P001", weeks)]["hr_valid_days"] == 7


def test_ten_day_window_ignores_days_eight_to_ten(tmp_path):
    data = make_data(tmp_path, survey=[])
    write_events(data, "P001", HR, [hr(D[i], 0, 70) for i in range(10)])
    out = tmp_path / "out"
    full(data, out, 10)
    row = weekly_rows(out)[("P001", 1)]
    assert (row["hr_valid_days"], row["hr_valid_minutes"], row["valid_days"]) == (7, 7, 7)


def test_weekly_schema_differs_from_the_daily_schema(tmp_path):
    data, out = prepared(tmp_path)
    weekly = {n for n, _ in weekly_columns(out)}
    _, rows = table_rows(out, "DESCRIBE gold_clinical_daily")
    daily = {r[0] for r in rows}
    assert weekly & daily == {"participant_id"}
    assert "local_date" not in weekly


# ---------------------------------------------------------------- hand-computed values

P001_W1 = {
    "valid_days": 5, "sleep_valid_days": 4, "sleep_stage_valid_days": 3, "steps_valid_days": 4,
    "steps_valid_minutes": 5, "hr_valid_days": 4, "hr_valid_minutes": 7,
    "fatigue_valid_days": 4, "stress_valid_days": 3, "readiness_valid_days": 4,
    "sleep_quality_valid_days": 3,
    "sleep_total_min_mean": 400.0, "deep_sleep_pct_mean": None, "sleep_efficiency_pct_mean": None,
    "steps_total": 135, "steps_daily_mean": 33.75, "steps_rest_to_moderate_minutes": 4,
    "steps_burst_minutes": 1,
    "hr_weekly_mean_bpm": 80.0, "hr_weekly_min_bpm": 50, "hr_weekly_max_bpm": 110,
    "fatigue_score_mean": 2.75, "stress_score_mean": None, "readiness_score_mean": 6.0,
    "sleep_quality_score_mean": None,
}
P001_W2 = {
    "valid_days": 3, "sleep_valid_days": 0, "sleep_stage_valid_days": 0, "steps_valid_days": 2,
    "steps_valid_minutes": 2, "hr_valid_days": 3, "hr_valid_minutes": 3,
    "fatigue_valid_days": 0, "stress_valid_days": 0, "readiness_valid_days": 0,
    "sleep_quality_valid_days": 0,
    "sleep_total_min_mean": None, "deep_sleep_pct_mean": None, "sleep_efficiency_pct_mean": None,
    "steps_total": None, "steps_daily_mean": None, "steps_rest_to_moderate_minutes": None,
    "steps_burst_minutes": None,
    "hr_weekly_mean_bpm": None, "hr_weekly_min_bpm": None, "hr_weekly_max_bpm": None,
    "fatigue_score_mean": None, "stress_score_mean": None, "readiness_score_mean": None,
    "sleep_quality_score_mean": None,
}
P002_W1 = {
    "valid_days": 4, "sleep_valid_days": 4, "sleep_stage_valid_days": 4, "steps_valid_days": 0,
    "steps_valid_minutes": 0, "hr_valid_days": 0, "hr_valid_minutes": 0,
    "fatigue_valid_days": 2, "stress_valid_days": 2, "readiness_valid_days": 2,
    "sleep_quality_valid_days": 2,
    "sleep_total_min_mean": 400.0, "deep_sleep_pct_mean": 25.0, "sleep_efficiency_pct_mean": 80.0,
    "steps_total": None, "steps_daily_mean": None, "steps_rest_to_moderate_minutes": None,
    "steps_burst_minutes": None,
    "hr_weekly_mean_bpm": None, "hr_weekly_min_bpm": None, "hr_weekly_max_bpm": None,
    "fatigue_score_mean": None, "stress_score_mean": None, "readiness_score_mean": None,
    "sleep_quality_score_mean": None,
}
P002_W2 = {name: None for name in P001_W2}
P002_W2.update({name: 0 for name in P001_W2 if name.endswith("_valid_days")})
P002_W2.update({"valid_days": 0, "steps_valid_minutes": 0, "hr_valid_minutes": 0})


def assert_row(row: dict, expected: dict) -> None:
    for name, value in expected.items():
        if isinstance(value, float):
            assert row[name] == approx(value), name
        else:
            assert row[name] == value, name


def test_hand_computed_participant_weeks(tmp_path):
    data, out = built(tmp_path)
    rows = weekly_rows(out)
    assert_row(rows[("P001", 1)], P001_W1)
    assert_row(rows[("P001", 2)], P001_W2)
    assert_row(rows[("P002", 1)], P002_W1)
    assert_row(rows[("P002", 2)], P002_W2)
    assert rows[("P001", 1)]["period_start_date"] == D[0]
    assert rows[("P001", 2)]["period_start_date"] == D[7]
    assert rows[("P001", 2)]["period_end_date"] == D[13]


def test_pipeline_run_gives_the_same_rows_as_a_direct_refresh(tmp_path):
    data, out = built(tmp_path)
    after_run = weekly_rows(out)
    assert_row(after_run[("P001", 1)], P001_W1)
    other = tmp_path / "plain"
    full(data, other)
    assert weekly_rows(other) == after_run


def test_weekly_heart_rate_mean_is_weighted_by_valid_minutes(tmp_path):
    data, out = built(tmp_path)
    row = weekly_rows(out)[("P001", 1)]
    assert row["hr_weekly_mean_bpm"] == approx(80.0)
    assert row["hr_weekly_mean_bpm"] != approx(73.75)  # not the mean of daily means


def test_coverage_threshold_three_days_unavailable_four_available(tmp_path):
    data, out = built(tmp_path)
    rows = weekly_rows(out)
    assert MIN_VALID_DAYS == 4
    # heart rate: 4 valid days available, 3 valid days NULL, counts still reported
    assert rows[("P001", 1)]["hr_weekly_mean_bpm"] is not None
    assert (rows[("P001", 2)]["hr_valid_days"], rows[("P001", 2)]["hr_valid_minutes"]) == (3, 3)
    assert rows[("P001", 2)]["hr_weekly_mean_bpm"] is None
    # steps: 4 days available, 2 days NULL
    assert rows[("P001", 1)]["steps_total"] == 135
    assert rows[("P001", 2)]["steps_total"] is None and rows[("P001", 2)]["steps_valid_days"] == 2
    # per-measure gates: survey stress has 3 valid days, fatigue 4
    assert rows[("P001", 1)]["stress_score_mean"] is None
    assert rows[("P001", 1)]["fatigue_score_mean"] == approx(2.75)
    # deep share has its own count (3) although total sleep has 4 valid days
    assert rows[("P001", 1)]["sleep_total_min_mean"] == approx(400.0)
    assert rows[("P001", 1)]["deep_sleep_pct_mean"] is None


def test_partial_missing_duplicate_and_flagged_days_are_never_imputed(tmp_path):
    data, out = built(tmp_path)
    rows = weekly_rows(out)
    w1 = rows[("P001", 1)]
    assert w1["valid_days"] == 5 < 7              # days 6 and 7 of the week have no data
    assert w1["steps_valid_minutes"] == 5         # conflicting duplicate slot counts as missing
    assert w1["hr_valid_minutes"] == 7            # exact duplicate counted once, 300 bpm withheld
    assert w1["steps_valid_days"] == 4            # invalid "abc" day and conflict day not valid
    assert w1["sleep_valid_days"] == 4            # duplicate-day and bogus-stage days not valid
    empty = rows[("P002", 2)]
    assert empty["valid_days"] == 0
    for name in ("steps_total", "sleep_total_min_mean", "hr_weekly_mean_bpm", "fatigue_score_mean"):
        assert empty[name] is None                 # missing is NULL, never 0


def test_a_zero_step_day_is_a_valid_day_and_a_missing_day_is_not(tmp_path):
    data, out = built(tmp_path)
    w1 = weekly_rows(out)[("P001", 1)]
    assert w1["steps_valid_days"] == 4 and w1["steps_total"] == 135  # day 4 steps == 0 counted


def test_flagged_but_valid_days_still_count(tmp_path):
    data = make_data(tmp_path, survey=[])
    # a not-minute-aligned timestamp is flagged but the value is kept
    write_events(data, "P001", HR, [{"timestamp": f"{D[i].isoformat()}T00:00:30",
                                     "heart_rate_bpm": 70} for i in range(4)])
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    row = weekly_rows(out)[("P001", 1)]
    assert row["hr_valid_days"] == 4 and row["hr_weekly_mean_bpm"] == approx(70.0)


def test_survey_only_week_reports_survey_coverage_but_zero_valid_days(tmp_path):
    data, out = built(tmp_path)
    p002 = weekly_rows(out)[("P002", 1)]
    assert p002["fatigue_valid_days"] == 2 and p002["fatigue_score_mean"] is None
    survey_only = make_data(tmp_path / "s", participants=[P001], survey=[
        survey_row("P001", D[i].isoformat(), ["3", "3", "5", "3"]) for i in range(4)])
    # make_data always writes one steps record (0 steps on 2026-01-08); a recorded zero is a
    # valid day, so remove the file to get a genuinely survey-only participant.
    (survey_only / "wearable_events" / "P001" / "steps.json").unlink()
    out2 = tmp_path / "s-out"
    full(survey_only, out2)
    refresh(out2)
    row = weekly_rows(out2)[("P001", 1)]
    assert row["valid_days"] == 0
    assert row["fatigue_valid_days"] == 4 and row["fatigue_score_mean"] == approx(3.0)
    assert row["readiness_score_mean"] == approx(5.0)


# ---------------------------------------------------------------- reconciliation with daily

def reconcile(out: Path) -> int:
    """Check every weekly row against the gold_clinical_daily rows of the same days."""
    daily = daily_rows(out)
    checked = 0
    for (pid, week), row in weekly_rows(out).items():
        first, last = row["period_start_date"], row["period_end_date"]
        days = [d for d in daily if d["participant_id"] == pid and first <= d["local_date"] <= last]
        gate = MIN_VALID_DAYS

        def vals(name):
            return [d[name] for d in days if d[name] is not None]

        def mean_or_none(values, count):
            return sum(values) / len(values) if count >= gate and values else None

        steps = vals("total_steps")
        assert row["steps_valid_days"] == len(steps)
        assert row["steps_valid_minutes"] == sum(d["steps_valid_minute_count"] for d in days)
        assert row["steps_total"] == (sum(steps) if len(steps) >= gate else None)
        assert row["steps_daily_mean"] == (approx(mean_or_none(steps, len(steps)))
                                           if len(steps) >= gate else None)
        for name, daily_name in (("steps_rest_to_moderate_minutes", "minutes_rest_to_moderate"),
                                 ("steps_burst_minutes", "minutes_burst")):
            valid_days = [d for d in days if d["total_steps"] is not None]
            assert row[name] == (sum(d[daily_name] for d in valid_days)
                                 if len(steps) >= gate else None)
        sleep = vals("total_sleep_min")
        deep = vals("deep_sleep_pct")
        eff = vals("sleep_efficiency_pct")
        assert row["sleep_valid_days"] == len(sleep)
        assert row["sleep_stage_valid_days"] == len(deep) == len(eff)
        for name, values in (("sleep_total_min_mean", sleep), ("deep_sleep_pct_mean", deep),
                             ("sleep_efficiency_pct_mean", eff)):
            expected = mean_or_none(values, len(values))
            assert row[name] == (approx(expected) if expected is not None else None)
        hr_days = [d for d in days if d["hr_valid_minute_count"] > 0]
        minutes = sum(d["hr_valid_minute_count"] for d in hr_days)
        assert row["hr_valid_days"] == len(hr_days)
        assert row["hr_valid_minutes"] == minutes
        if len(hr_days) >= gate:
            weighted = sum(d["hr_mean_bpm"] * d["hr_valid_minute_count"] for d in hr_days)
            assert row["hr_weekly_mean_bpm"] == approx(weighted / minutes)
            assert row["hr_weekly_min_bpm"] == min(d["hr_min_bpm"] for d in hr_days)
            assert row["hr_weekly_max_bpm"] == max(d["hr_max_bpm"] for d in hr_days)
        else:
            assert row["hr_weekly_mean_bpm"] is None
            assert row["hr_weekly_min_bpm"] is None and row["hr_weekly_max_bpm"] is None
        for score, count in (("fatigue", "fatigue"), ("stress", "stress"),
                             ("readiness", "readiness"), ("sleep_quality", "sleep_quality")):
            values = vals(f"{score}_score")
            assert row[f"{count}_valid_days"] == len(values)
            expected = mean_or_none(values, len(values))
            assert row[f"{score}_score_mean"] == (approx(expected) if expected is not None
                                                  else None)
        wearable = [d for d in days if d["total_sleep_min"] is not None
                    or d["total_steps"] is not None or d["hr_valid_minute_count"] > 0]
        assert row["valid_days"] == len(wearable)
        checked += 1
    return checked


def test_weekly_values_reconcile_with_the_daily_table(tmp_path):
    data, out = built(tmp_path)
    assert reconcile(out) == 4


def test_weekly_values_still_reconcile_after_an_incremental_run(tmp_path):
    data, out = prepared(tmp_path)
    records = read_events(data, "P001", HR)
    records[6]["heart_rate_bpm"] = 120  # D[2] minute 1
    write_events(data, "P001", HR, records)
    assert inc(data, out).outcome == OUTCOME_INCREMENTAL
    assert reconcile(out) == 4


def test_weekly_is_derived_from_silver_not_from_gold(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    edit_db(out, "UPDATE gold_clinical_daily SET total_steps = 999999, hr_mean_bpm = 1.0, "
                 "total_sleep_min = 1, fatigue_score = 5")
    refresh(out)
    assert weekly_rows(out) == before


PRE_START = START - timedelta(days=1)
AFTER_END = D[DAYS]  # first day after the 14-day window


def mismatch_hr(day: date, minute: int, bpm: int, pid: str = "P002") -> dict:
    return {"participant_id": pid, "timestamp": f"{day.isoformat()}T00:{minute:02d}:00",
            "heart_rate_bpm": bpm}


def mismatch_steps(day: date, minute: int, steps: int, pid: str = "P002") -> dict:
    return {"participant_id": pid, "timestamp": f"{day.isoformat()}T00:{minute:02d}:00",
            "steps": steps}


def test_identical_duplicate_sleep_sessions_give_one_value_set_not_a_sum(tmp_path):
    data = make_data(tmp_path, survey=[])
    twice = []
    for i in range(4):  # four days, each recorded by two identical sessions
        twice += [session("P001", D[i], stages(200, 100, 100, 100)),
                  session("P001", D[i], stages(200, 100, 100, 100))]
    # fifth day: identical sessions where one has an unparseable onset (flagged, still counts)
    twice += [session("P001", D[4], stages(200, 100, 100, 100)),
              dict(session("P001", D[4], stages(200, 100, 100, 100)), sleep_onset="bad")]
    write_sessions(data, "P001", twice)
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    daily = [d for d in daily_rows(out) if d["participant_id"] == "P001"
             and d["total_sleep_min"] is not None]
    assert len(daily) == 5
    for d in daily:  # real pipeline: one value set per date
        assert (d["total_sleep_min"], d["deep_sleep_pct"], d["sleep_efficiency_pct"]) == (
            400, 25.0, 80.0)
    row = weekly_rows(out)[("P001", 1)]
    assert row["sleep_valid_days"] == 5 and row["sleep_stage_valid_days"] == 5
    assert row["sleep_total_min_mean"] == approx(400.0)  # not 800 (all stages of the date)
    assert row["deep_sleep_pct_mean"] == approx(25.0)
    assert row["sleep_efficiency_pct_mean"] == approx(80.0)
    assert reconcile(out) == 4


def test_identical_duplicate_sessions_do_not_hide_a_conflicting_session(tmp_path):
    data = make_data(tmp_path, survey=[])
    sessions = []
    for i in range(4):
        sessions += [session("P001", D[i], stages(200, 100, 100, 100)),
                     session("P001", D[i], stages(200, 100, 100, 100))]
    sessions += [session("P001", D[4], stages(200, 100, 100, 100)),
                 session("P001", D[4], stages(200, 100, 100, 100)),
                 session("P001", D[4], stages(100, 100, 100, 100))]  # a conflicting third
    write_sessions(data, "P001", sessions)
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    row = weekly_rows(out)[("P001", 1)]
    assert row["sleep_valid_days"] == 4 and row["sleep_total_min_mean"] == approx(400.0)
    assert reconcile(out) == 4


def test_folder_mismatch_steps_and_heart_rate_rows_do_not_contribute(tmp_path):
    data = make_data(tmp_path, survey=[])
    write_events(data, "P001", HR, [hr(D[i], 0, 70) for i in range(4)]
                 + [mismatch_hr(D[0], 5, 120), mismatch_hr(D[4], 0, 99)])
    write_events(data, "P001", STEPS, [st(D[i], 0, 10) for i in range(4)]
                 + [mismatch_steps(D[0], 5, 500), mismatch_steps(D[4], 0, 500)])
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    _, flagged = table_rows(out, "SELECT count(*) FROM silver_heart_rate_minute WHERE "
                                 "quality_flags LIKE '%HR_PARTICIPANT_FOLDER_MISMATCH%'")
    assert flagged == [(2,)]
    rows = weekly_rows(out)
    w1 = rows[("P001", 1)]
    assert (w1["hr_valid_days"], w1["hr_valid_minutes"], w1["valid_days"]) == (4, 4, 4)
    assert w1["hr_weekly_mean_bpm"] == approx(70.0)
    assert (w1["hr_weekly_min_bpm"], w1["hr_weekly_max_bpm"]) == (70, 70)
    assert (w1["steps_valid_days"], w1["steps_valid_minutes"], w1["steps_total"]) == (4, 4, 40)
    assert w1["steps_daily_mean"] == approx(10.0)
    assert w1["steps_burst_minutes"] == 0 and w1["steps_rest_to_moderate_minutes"] == 4
    # the mismatched rows name P002 but P002 gets nothing from them either
    p2 = rows[("P002", 1)]
    assert (p2["hr_valid_days"], p2["hr_valid_minutes"], p2["steps_valid_days"],
            p2["steps_valid_minutes"], p2["valid_days"]) == (0, 0, 0, 0, 0)
    assert p2["hr_weekly_mean_bpm"] is None and p2["steps_total"] is None
    assert reconcile(out) == 4


def test_rows_outside_the_window_and_orphans_never_reach_a_week(tmp_path):
    inside = (0, 1, 2, 7, 8, 9)  # three valid days in each of the two whole weeks
    survey = [survey_row("P001", D[i].isoformat(), ["3", "3", "3", "3"]) for i in inside]
    survey += [survey_row("P001", day.isoformat(), ["1", "1", "1", "1"])
               for day in (PRE_START, AFTER_END)]
    data = make_data(tmp_path, survey=survey)
    outside = (PRE_START, AFTER_END)
    write_events(data, "P001", HR, [hr(D[i], 0, 70) for i in inside]
                 + [hr(day, 0, 70) for day in outside])
    write_events(data, "P001", STEPS, [st(D[i], 0, 10) for i in inside]
                 + [st(day, 0, 10) for day in outside])
    write_sessions(data, "P001", [session("P001", D[i], stages(200, 100, 100, 100))
                                  for i in inside]
                   + [session("P001", day, stages(200, 100, 100, 100)) for day in outside])
    write_events(data, "P999", HR, [hr(D[i], 0, 99) for i in range(7)])
    write_events(data, "P999", STEPS, [st(D[i], 0, 99) for i in range(7)])
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    _, flagged = table_rows(out, "SELECT count(*) FROM silver_heart_rate_minute WHERE "
                                 "is_out_of_window")
    assert flagged == [(2,)]
    rows = weekly_rows(out)
    assert set(rows) == {(p, w) for p in ("P001", "P002") for w in (1, 2)}
    for week in (1, 2):
        row = rows[("P001", week)]
        assert row["valid_days"] == 3  # 3 in-window days; the neighbours would make 4
        for name in ("hr_valid_days", "steps_valid_days", "sleep_valid_days",
                     "sleep_stage_valid_days", "fatigue_valid_days"):
            assert row[name] == 3, name
        assert (row["hr_valid_minutes"], row["steps_valid_minutes"]) == (3, 3)
        for name in ("hr_weekly_mean_bpm", "steps_total", "sleep_total_min_mean",
                     "fatigue_score_mean"):
            assert row[name] is None, name
    assert reconcile(out) == 4


def test_second_study_start_gives_matching_week_bounds():
    start = date(2026, 3, 2)
    assert week_bounds(start, 1) == (date(2026, 3, 2), date(2026, 3, 8))
    assert week_bounds(start, 3) == (date(2026, 3, 16), date(2026, 3, 22))
    assert week_of(start, 21, date(2026, 3, 22)) == 3
    assert week_of(start, 21, date(2026, 3, 23)) is None
    assert week_of(start, 21, date(2026, 3, 1)) is None


# ---------------------------------------------------------------- refresh semantics and MERGE

def test_refresh_with_affected_on_a_missing_table_recomputes_every_week(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    edit_db(out, f"DROP TABLE {TABLE_NAME}")
    refresh(out, {"P001": frozenset({D[2]})})
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)
    assert weekly_rows(out) == before  # all four rows, not only the affected P001 week 1


def test_refresh_with_affected_on_a_wrong_layout_recomputes_every_week(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    edit_db(out, f"ALTER TABLE {TABLE_NAME} DROP COLUMN hr_weekly_max_bpm")
    refresh(out, {"P002": frozenset({D[8]})})
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)
    assert weekly_rows(out) == before

def test_refresh_creates_a_missing_table_with_the_key(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    edit_db(out, f"DROP TABLE {TABLE_NAME}")
    refresh(out)
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)
    assert weekly_rows(out) == before
    with pytest.raises(duckdb.ConstraintException):
        edit_db(out, f"INSERT INTO {TABLE_NAME} SELECT * FROM {TABLE_NAME} LIMIT 1")


def test_refresh_rebuilds_a_table_with_a_different_layout(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    edit_db(out, f"ALTER TABLE {TABLE_NAME} DROP COLUMN hr_weekly_max_bpm")
    refresh(out)
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)
    assert weekly_rows(out) == before


def test_merge_rerun_is_idempotent_and_never_duplicates(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    for _ in range(3):
        refresh(out)
    assert weekly_rows(out) == before
    assert table_rows(out, f"SELECT count(*) FROM {TABLE_NAME}")[1] == [(4,)]


def test_full_refresh_repairs_values_and_removes_stale_keys(tmp_path):
    data, out = built(tmp_path)
    before = weekly_rows(out)
    edit_db(out, f"UPDATE {TABLE_NAME} SET valid_days = 99",
            f"INSERT INTO {TABLE_NAME} SELECT * REPLACE ('P999' AS participant_id) "
            f"FROM {TABLE_NAME} WHERE participant_id = 'P001' AND week_id = 1")
    refresh(out)
    assert weekly_rows(out) == before


def test_refresh_with_affected_days_recomputes_only_those_participant_weeks(tmp_path):
    data, out = built(tmp_path)
    good = weekly_rows(out)
    edit_db(out, f"UPDATE {TABLE_NAME} SET valid_days = 99")
    refresh(out, {"P001": frozenset({D[2]})})
    rows = weekly_rows(out)
    assert rows[("P001", 1)] == good[("P001", 1)]
    for key in (("P001", 2), ("P002", 1), ("P002", 2)):
        assert rows[key]["valid_days"] == 99
    edit_db(out, f"UPDATE {TABLE_NAME} SET valid_days = 99")
    refresh(out, {"P001": frozenset({D[1], D[9]}), "P002": frozenset({D[8]})})
    rows = weekly_rows(out)
    assert {k for k, r in rows.items() if r["valid_days"] != 99} == {
        ("P001", 1), ("P001", 2), ("P002", 2)}


def test_refresh_with_affected_days_outside_whole_weeks_or_unenrolled_changes_nothing(tmp_path):
    data = make_data(tmp_path, survey=[])
    write_events(data, "P001", HR, [hr(D[i], 0, 70) for i in range(10)])
    out = tmp_path / "out"
    full(data, out, 10)
    refresh(out)
    before = weekly_rows(out)
    refresh(out, {"P001": frozenset({D[8], D[9]}), "P777": frozenset({D[0]}), "P002": frozenset()})
    assert weekly_rows(out) == before
    refresh(out, {})
    assert weekly_rows(out) == before


def test_output_is_deterministic_across_independent_full_runs(tmp_path):
    data = fixture_a(tmp_path)
    first, second = tmp_path / "a", tmp_path / "b"
    full(data, first)
    full(data, second)
    assert state(first)["tables"][TABLE_NAME] == state(second)["tables"][TABLE_NAME]
    assert len(state(first)["tables"][TABLE_NAME]) == 4
    refresh(first)
    assert state(first)["tables"][TABLE_NAME] == state(second)["tables"][TABLE_NAME]


# ---------------------------------------------------------------- incremental

def test_corrected_record_changes_exactly_one_weekly_row(tmp_path):
    data, out = prepared(tmp_path)
    before = weekly_rows(out)
    records = read_events(data, "P001", HR)
    records[6]["heart_rate_bpm"] = 120  # day 3 (D[2]) minute 1: 100 -> 120
    write_events(data, "P001", HR, records)
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    after = weekly_rows(out)
    assert changed_keys(before, after) == {("P001", 1)}
    assert after[("P001", 1)]["hr_weekly_mean_bpm"] == approx(580 / 7)
    assert_equals_full(data, out, tmp_path)


def test_new_day_changes_exactly_one_weekly_row(tmp_path):
    data, out = prepared(tmp_path)
    before = weekly_rows(out)
    write_events(data, "P002", HR, [hr(D[8], 0, 65)])
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    after = weekly_rows(out)
    assert changed_keys(before, after) == {("P002", 2)}
    assert after[("P002", 2)]["hr_valid_days"] == 1 and after[("P002", 2)]["valid_days"] == 1
    assert_equals_full(data, out, tmp_path)


def test_removed_file_changes_only_the_weeks_it_fed(tmp_path):
    data, out = prepared(tmp_path)
    before = weekly_rows(out)
    events_path(data, "P001", STEPS).unlink()
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    after = weekly_rows(out)
    assert changed_keys(before, after) == {("P001", 1), ("P001", 2)}
    assert after[("P001", 1)]["steps_valid_days"] == 0 and after[("P001", 1)]["steps_total"] is None
    assert_equals_full(data, out, tmp_path)


def test_removed_record_equals_full_rebuild(tmp_path):
    data, out = prepared(tmp_path)
    write_events(data, "P001", HR, read_events(data, "P001", HR)[:-2])
    assert inc(data, out).outcome == OUTCOME_INCREMENTAL
    assert_equals_full(data, out, tmp_path)


def test_noop_incremental_changes_nothing(tmp_path):
    data, out = prepared(tmp_path)
    before_state, before_tree = state(out), tree_hashes(out)
    result = inc(data, out)
    assert result.outcome == OUTCOME_NOOP
    assert state(out) == before_state and tree_hashes(out) == before_tree


def test_incremental_refreshes_the_weekly_table_through_the_extension_point(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    calls = []
    real = WEEKLY.refresh

    class Spy:
        name = TABLE_NAME

        def refresh(self, con, affected):
            calls.append(affected)
            real(con, affected)

    monkeypatch.setattr(incremental, "DERIVED_TABLES", [Spy()])
    records = read_events(data, "P001", HR)
    records[0]["heart_rate_bpm"] = 61
    write_events(data, "P001", HR, records)
    inc(data, out)
    assert calls == [{"P001": frozenset({D[0]})}]


def test_shared_input_change_follows_the_full_rebuild_path(tmp_path):
    data, out = prepared(tmp_path)
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
              [P001, P002, P003])
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "shared_input_changed")
    assert {k[0] for k in weekly_rows(out)} == {"P001", "P002", "P003"}
    assert_equals_full(data, out, tmp_path)


def test_removing_a_participant_prunes_its_weekly_rows(tmp_path):
    data, out = prepared(tmp_path)
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS, [P001])
    result = inc(data, out)
    assert result.fallback_reason == "shared_input_changed"
    assert {k[0] for k in weekly_rows(out)} == {"P001"}
    assert_equals_full(data, out, tmp_path)


def test_survey_change_follows_the_full_rebuild_path(tmp_path):
    data, out = prepared(tmp_path)
    write_csv(data / "health_summaries" / "wellness_survey.csv",
              ["participant_id", "date", "fatigue_score", "stress_score", "readiness_score",
               "sleep_quality_score"],
              [survey_row("P002", D[i].isoformat(), ["3", "3", "3", "3"]) for i in range(5)])
    result = inc(data, out)
    assert result.fallback_reason == "shared_input_changed"
    assert weekly_rows(out)[("P002", 1)]["fatigue_score_mean"] == approx(3.0)
    assert_equals_full(data, out, tmp_path)


@pytest.mark.parametrize("days", [21, 10, 6, 28, 13])
def test_window_change_follows_the_full_rebuild_path(tmp_path, days):
    data, out = prepared(tmp_path)
    result = inc(data, out, days)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "window_changed")
    assert {k[1] for k in weekly_rows(out)} == set(range(1, week_count(days) + 1))
    assert_equals_full(data, out, tmp_path, days)


def recreate_with_key_type_changed(table: str, column: str, new_type: str):
    """Rebuild the table with one key column cast to another type (the key cannot be ALTERed)."""
    def degrade(con):
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
            "ORDER BY ordinal_position", [table]).fetchall()]
        select = ", ".join(f"CAST({c} AS {new_type}) AS {c}" if c == column else c for c in cols)
        con.execute(f"CREATE TABLE {table}_retyped AS SELECT {select} FROM {table}")
        con.execute(f"DROP TABLE {table}")
        con.execute(f"ALTER TABLE {table}_retyped RENAME TO {table}")
    return degrade


DEGRADE = {
    "weekly_table_missing": drop_table(TABLE_NAME),
    "weekly_type_changed": alter_type(TABLE_NAME, "period_start_date", "TIMESTAMP"),
    "weekly_key_type_changed": recreate_with_key_type_changed(TABLE_NAME, "week_id", "BIGINT"),
    "weekly_columns_reordered": swap_first_two_columns(TABLE_NAME),
    "weekly_column_missing": lambda con: con.execute(
        f"ALTER TABLE {TABLE_NAME} DROP COLUMN hr_weekly_max_bpm"),
    "weekly_measure_type_changed": alter_type(TABLE_NAME, "steps_total", "DOUBLE"),
}


@pytest.mark.parametrize("name", sorted(DEGRADE))
def test_old_weekly_schema_falls_back_to_a_full_rebuild(tmp_path, name):
    data, out = prepared(tmp_path)
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        DEGRADE[name](con)
    finally:
        con.close()
    records = read_events(data, "P001", HR)
    records[0]["heart_rate_bpm"] = 61
    write_events(data, "P001", HR, records)
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "old_schema")
    assert weekly_columns(out) == list(WEEKLY_COLUMNS)
    assert_equals_full(data, out, tmp_path)


def test_failed_incremental_run_leaves_the_weekly_output_unchanged(tmp_path):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    assert len(weekly_rows(out)) == 4
    events_path(data, "P001", HR).write_text("{not json", encoding="utf-8")
    with pytest.raises(PipelineError):
        inc(data, out)
    assert tree_hashes(out) == before


def test_failing_weekly_refresh_rolls_the_whole_run_back(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    assert len(weekly_rows(out)) == 4

    class Boom:
        name = TABLE_NAME

        def refresh(self, con, affected):
            WEEKLY.refresh(con, affected)
            raise RuntimeError("injected")

    monkeypatch.setattr(incremental, "DERIVED_TABLES", [Boom()])
    records = read_events(data, "P001", HR)
    records[0]["heart_rate_bpm"] = 61
    write_events(data, "P001", HR, records)
    with pytest.raises(RuntimeError):
        inc(data, out)
    assert tree_hashes(out) == before


# ---------------------------------------------------------------- quality outputs, compare

def test_quality_outputs_and_daily_table_are_unchanged(tmp_path):
    from wearable_pipeline.pipeline import ALL_GOLD_COLUMNS, RULES

    data, out = prepared(tmp_path)
    _, rows = table_rows(out, "SELECT DISTINCT rule_id FROM quality_results")
    assert {r[0] for r in rows} == set(RULES)
    assert not [r for r in rows if "WEEK" in r[0].upper() or "BIOSTAT" in r[0].upper()]
    _, rejects = table_rows(out, "SELECT DISTINCT rule_id FROM quality_rejects")
    assert not [r for r in rejects if "WEEK" in r[0].upper()]
    assert "biostat" not in (out / "quality" / "report.md").read_text(encoding="utf-8").lower()
    _, daily = table_rows(out, "DESCRIBE gold_clinical_daily")
    assert [(r[0], r[1]) for r in daily] == list(ALL_GOLD_COLUMNS)
    _, runs = table_rows(out, "SELECT DISTINCT run_id FROM quality_results")
    assert len(runs) == 1 and len(runs[0][0]) == 16


def test_compare_command_covers_the_weekly_table_without_code_changes(tmp_path, capsys):
    data = fixture_a(tmp_path)
    left, right = tmp_path / "l", tmp_path / "r"
    full(data, left)
    full(data, right)
    assert main(["compare", str(left), str(right)]) == 0
    assert capsys.readouterr().out == "identical\n"
    edit_db(right, f"UPDATE {TABLE_NAME} SET hr_weekly_min_bpm = 1 "
                   "WHERE participant_id = 'P001' AND week_id = 1")
    assert main(["compare", str(left), str(right)]) == 1
    out = capsys.readouterr().out
    assert f"table {TABLE_NAME}: rows differ: left_rows=4 right_rows=4 left_only=1 right_only=1" in out
    assert "70" not in out and "P001" not in out  # counts only, never row values
