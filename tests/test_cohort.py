"""Behavior tests for INC-008 `gold_chronotype_cohort` (see specs/INC-008.md). Synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from tests.test_incremental import (
    HR, SLEEP, STEPS, alter_type, drop_table, events_path, hr, read_events, st,
    state, swap_first_two_columns, write_events,
)
from tests.test_sleep import session
from tests.test_walking_skeleton import (
    P001, P002, PARTICIPANT_COLUMNS, START, make_data, tree_hashes, write_csv,
)
from tests.test_weekly import (
    D, approx, changed_keys, daily_rows, edit_db, stages, table_rows, write_sessions,
)
from wearable_pipeline import PipelineError, incremental, main, run_pipeline
from wearable_pipeline.cohort import (
    COHORT, COHORT_COLUMNS, FLAG_CONFLICT, FLAG_MISSING, FLAG_UNEXPECTED, MIN_VALID_DAYS,
    TABLE_NAME, VALID_COHORTS, ChronotypeCohort, classify_cohort, mean_if_covered,
)
from wearable_pipeline.incremental import (
    OUTCOME_FULL, OUTCOME_INCREMENTAL, OUTCOME_NOOP, run_incremental,
)

DAYS = 28
P003 = ["P003", "FBT-0003-CCCCCCCC", "40", "170", "female", "Z", "160"]
P004 = ["P004", "FBT-0004-DDDDDDDD", "29", "165", "female", "", "170"]
P005_A = ["P005", "FBT-0005-EEEEEEEE", "33", "170", "male", "A", "150"]
P005_B = ["P005", "FBT-0005-EEEEEEEE", "33", "170", "male", "B", "150"]
P006_A = ["P006", "FBT-0006-FFFFFFFF", "35", "172", "male", "A", "155"]
P006_BLANK = ["P006", "FBT-0006-FFFFFFFF", "35", "172", "male", "", "155"]
ENROLLED = ["P001", "P002", "P003", "P004", "P005", "P006"]
COHORTS = {"P001": ("B", None), "P002": ("A", None), "P003": (None, FLAG_UNEXPECTED),
           "P004": (None, FLAG_MISSING), "P005": (None, FLAG_CONFLICT), "P006": ("A", None)}


# ---------------------------------------------------------------- helpers

def full(data: Path, out: Path, days: int = DAYS) -> None:
    run_pipeline(data, out, study_start=START, study_days=days)


def inc(data: Path, out: Path, days: int = DAYS):
    return run_incremental(data, out, study_start=START, study_days=days)


def refresh(out: Path, affected=None) -> None:
    """Run the cohort refresh directly (this is what reaches the stub while it is a stub)."""
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        COHORT.refresh(con, affected)
    finally:
        con.close()


def cohort_rows(out: Path) -> dict[str, dict]:
    names, rows = table_rows(out, f"SELECT * FROM {TABLE_NAME}")
    result = {r[0]: dict(zip(names, r)) for r in rows}
    assert len(result) == len(rows), "duplicate participant_id keys"
    return result


def cohort_columns(out: Path) -> list[tuple[str, str]]:
    _, rows = table_rows(out, f"DESCRIBE {TABLE_NAME}")
    return [(r[0], r[1]) for r in rows]


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


def fixture_c(root: Path) -> Path:
    """Six enrolled participants over a 28-day window; every expected value is hand computed."""
    participants = [P001, P002, P003, P004, P005_A, P005_B, P006_A, P006_BLANK]
    data = make_data(root, participants=participants, survey=[])
    write_events(data, "P001", HR,
                 [hr(D[i], 0, 60 + 2 * (i % 2)) for i in range(15)]      # 15 valid days
                 + [hr(D[15], 0, 100), hr(D[15], 1, 110), hr(D[15], 2, 120)]  # 3 minutes, mean 110
                 + [hr(D[16], 0, 300)]                                   # out of range: withheld
                 + [hr(D[28], 0, 70)])                                   # outside the window
    write_events(data, "P001", STEPS,
                 [st(D[i], 0, 100 + i) for i in range(14)]
                 + [st(D[0], 0, 100)]                                    # exact duplicate minute
                 + [st(D[14], 0, 0)]                                     # zero steps: a valid day
                 + [st(D[15], 0, 10), st(D[15], 0, 20)]                  # conflicting: missing
                 + [st(D[16], 0, "abc")]                                 # invalid: missing
                 + [st(D[28], 0, 5000)])                                 # outside the window
    sleep = [session("P001", D[i], stages(200, 100, 100, 100) if i % 2 == 0
                     else stages(240, 120, 120, 20)) for i in range(14)]
    sleep += [session("P001", D[1], stages(240, 120, 120, 20))]          # identical duplicate
    sleep += [session("P001", D[14], stages(200, 100, 100, 100)),        # conflicting duplicate
              session("P001", D[14], stages(100, 100, 100, 100))]        # sessions: withheld
    sleep += [session("P001", D[28], stages(200, 100, 100, 100))]        # outside the window
    write_sessions(data, "P001", sleep)
    write_events(data, "P002", STEPS, [st(D[i], 0, 100) for i in range(13)])   # 13 valid days
    write_sessions(data, "P002", [session("P002", D[i], stages(200, 100, 100, 100))
                                  for i in range(13)])
    write_events(data, "P003", STEPS, [st(D[i], 0, 50) for i in range(14)])    # exactly 14
    write_events(data, "P999", HR, [hr(D[0], 0, 99)])                          # orphan
    return data


EXPECTED = {
    "P001": {"chronotype_cohort": "B", "cohort_flag": None, "window_days": 28,
             "sleep_valid_days": 14, "sleep_stage_valid_days": 14, "steps_valid_days": 15,
             "hr_valid_days": 16, "sleep_total_min_mean": 440.0,
             "sleep_efficiency_pct_mean": 88.0, "deep_sleep_pct_mean": 25.0,
             "steps_daily_mean": 99.4, "hr_daily_mean_bpm": 64.0},
    "P002": {"chronotype_cohort": "A", "cohort_flag": None, "window_days": 28,
             "sleep_valid_days": 13, "sleep_stage_valid_days": 13, "steps_valid_days": 13,
             "hr_valid_days": 0, "sleep_total_min_mean": None,
             "sleep_efficiency_pct_mean": None, "deep_sleep_pct_mean": None,
             "steps_daily_mean": None, "hr_daily_mean_bpm": None},
    "P003": {"chronotype_cohort": None, "cohort_flag": FLAG_UNEXPECTED, "window_days": 28,
             "sleep_valid_days": 0, "sleep_stage_valid_days": 0, "steps_valid_days": 14,
             "hr_valid_days": 0, "sleep_total_min_mean": None,
             "sleep_efficiency_pct_mean": None, "deep_sleep_pct_mean": None,
             "steps_daily_mean": 50.0, "hr_daily_mean_bpm": None},
}
NO_DATA = {"window_days": 28, "sleep_valid_days": 0, "sleep_stage_valid_days": 0,
           "steps_valid_days": 0, "hr_valid_days": 0, "sleep_total_min_mean": None,
           "sleep_efficiency_pct_mean": None, "deep_sleep_pct_mean": None,
           "steps_daily_mean": None, "hr_daily_mean_bpm": None}
EXPECTED["P004"] = {"chronotype_cohort": None, "cohort_flag": FLAG_MISSING, **NO_DATA}
EXPECTED["P005"] = {"chronotype_cohort": None, "cohort_flag": FLAG_CONFLICT, **NO_DATA}
EXPECTED["P006"] = {"chronotype_cohort": "A", "cohort_flag": None, **NO_DATA}


def built(tmp_path: Path) -> tuple[Path, Path]:
    """Fixture C, full run, then the cohort refresh executed directly."""
    data = fixture_c(tmp_path)
    out = tmp_path / "out"
    full(data, out)
    refresh(out)
    return data, out


def prepared(tmp_path: Path) -> tuple[Path, Path]:
    """Fixture C after a plain full run (what an incremental run would start from)."""
    data = fixture_c(tmp_path)
    out = tmp_path / "out"
    full(data, out)
    assert len(cohort_rows(out)) == 6
    return data, out


def assert_row(row: dict, expected: dict) -> None:
    for name, value in expected.items():
        if isinstance(value, float):
            assert row[name] == approx(value), name
        else:
            assert row[name] == value, name


# ---------------------------------------------------------------- pure functions

def test_constants_and_registration():
    assert TABLE_NAME == "gold_chronotype_cohort"
    assert MIN_VALID_DAYS == 14
    assert VALID_COHORTS == ("A", "B")
    assert (FLAG_MISSING, FLAG_UNEXPECTED, FLAG_CONFLICT) == (
        "CHRONOTYPE_MISSING", "CHRONOTYPE_UNEXPECTED", "CHRONOTYPE_CONFLICT")
    assert isinstance(COHORT, ChronotypeCohort)
    assert COHORT.name == TABLE_NAME
    assert [t.name for t in incremental.DERIVED_TABLES] == ["gold_biostat_weekly", TABLE_NAME]
    assert incremental.DERIVED_TABLES[1] is COHORT


def test_cohort_columns_are_the_specified_schema():
    assert [n for n, _ in COHORT_COLUMNS] == [
        "participant_id", "chronotype_cohort", "cohort_flag", "window_days",
        "sleep_valid_days", "sleep_stage_valid_days", "steps_valid_days", "hr_valid_days",
        "sleep_total_min_mean", "sleep_efficiency_pct_mean", "deep_sleep_pct_mean",
        "steps_daily_mean", "hr_daily_mean_bpm"]
    types = dict(COHORT_COLUMNS)
    assert types["participant_id"] == types["chronotype_cohort"] == types["cohort_flag"] == "VARCHAR"
    assert types["window_days"] == types["hr_valid_days"] == "INTEGER"
    assert types["steps_daily_mean"] == types["hr_daily_mean_bpm"] == "DOUBLE"


@pytest.mark.parametrize("values, expected", [
    (["A"], ("A", None)),
    (["B"], ("B", None)),
    (["A", "A"], ("A", None)),
    (["A", None], ("A", None)),            # a missing listing never overrides a value
    (["", "B"], ("B", None)),
    (["  ", "B"], ("B", None)),
    ([], (None, FLAG_MISSING)),
    ([None], (None, FLAG_MISSING)),
    ([""], (None, FLAG_MISSING)),
    (["   ", None], (None, FLAG_MISSING)),
    (["Z"], (None, FLAG_UNEXPECTED)),
    (["a"], (None, FLAG_UNEXPECTED)),      # no case folding
    ([" A"], (None, FLAG_UNEXPECTED)),     # no trimming of a present value
    (["AB"], (None, FLAG_UNEXPECTED)),
    (["A", "B"], (None, FLAG_CONFLICT)),
    (["B", "A"], (None, FLAG_CONFLICT)),
    (["A", "Z"], (None, FLAG_CONFLICT)),
    (["A", "B", None], (None, FLAG_CONFLICT)),
])
def test_classify_cohort(values, expected):
    assert classify_cohort(values) == expected


@pytest.mark.parametrize("count, available", [(0, False), (1, False), (13, False),
                                              (14, True), (15, True), (28, True)])
def test_mean_if_covered_threshold_boundary(count, available):
    values = [float(i) for i in range(count)]
    expected = sum(values) / count if available else None
    assert mean_if_covered(values) == expected


def test_mean_if_covered_uses_the_minimum_argument_and_returns_a_float():
    assert mean_if_covered([1, 2, 4], minimum=3) == pytest.approx(7 / 3)
    assert mean_if_covered([1, 2, 4], minimum=4) is None
    assert isinstance(mean_if_covered([2, 4], minimum=2), float)
    assert mean_if_covered([2, 4], minimum=2) == 3.0
    assert mean_if_covered([0, 0], minimum=2) == 0.0  # a zero mean is a value, not "no data"


# ---------------------------------------------------------------- grain, cohort, values

def test_pipeline_writes_the_table_on_every_run_with_the_specified_columns(tmp_path):
    data, out = prepared(tmp_path)
    assert cohort_columns(out) == list(COHORT_COLUMNS)
    _, keys = table_rows(
        out, "SELECT constraint_column_names FROM duckdb_constraints() "
             f"WHERE table_name = '{TABLE_NAME}' AND constraint_type = 'PRIMARY KEY'")
    assert [list(k[0]) for k in keys] == [["participant_id"]]


def test_one_row_per_enrolled_participant_and_orphans_get_none(tmp_path):
    data, out = prepared(tmp_path)
    assert sorted(cohort_rows(out)) == ENROLLED  # P005 and P006 listed twice -> one row each
    assert "P999" not in cohort_rows(out)


def test_cohort_attribute_and_flags_come_from_silver_participants(tmp_path):
    data, out = prepared(tmp_path)
    rows = cohort_rows(out)
    for pid, (cohort, flag) in COHORTS.items():
        assert (rows[pid]["chronotype_cohort"], rows[pid]["cohort_flag"]) == (cohort, flag), pid


def test_participants_without_a_cohort_keep_their_measures(tmp_path):
    data, out = prepared(tmp_path)
    row = cohort_rows(out)["P003"]
    assert row["chronotype_cohort"] is None and row["steps_valid_days"] == 14
    assert row["steps_daily_mean"] == approx(50.0)


def test_fifty_participants_give_fifty_rows_in_cohorts_of_28_and_22(tmp_path):
    rows = [[f"P{i:03d}", f"FBT-{i:04d}-AAAAAAAA", "30", "170", "male",
             "A" if i <= 28 else "B", "160"] for i in range(1, 51)]
    data = make_data(tmp_path, participants=rows, survey=[])
    out = tmp_path / "out"
    full(data, out)
    result = cohort_rows(out)
    assert len(result) == 50
    assert sum(r["chronotype_cohort"] == "A" for r in result.values()) == 28
    assert sum(r["chronotype_cohort"] == "B" for r in result.values()) == 22
    assert all(r["cohort_flag"] is None for r in result.values())


def test_hand_computed_participant_rows(tmp_path):
    data, out = built(tmp_path)
    rows = cohort_rows(out)
    assert set(rows) == set(EXPECTED)
    for pid, expected in EXPECTED.items():
        assert_row(rows[pid], expected)


def test_pipeline_run_gives_the_same_rows_as_a_direct_refresh(tmp_path):
    data, out = built(tmp_path)
    after_run = cohort_rows(out)
    assert_row(after_run["P001"], EXPECTED["P001"])
    other = tmp_path / "plain"
    full(data, other)
    assert cohort_rows(other) == after_run


def test_threshold_boundary_thirteen_days_unavailable_fourteen_available(tmp_path):
    data, out = built(tmp_path)
    rows = cohort_rows(out)
    p2, p3 = rows["P002"], rows["P003"]
    assert p2["steps_valid_days"] == 13 and p2["steps_daily_mean"] is None
    assert p2["sleep_valid_days"] == 13 and p2["sleep_total_min_mean"] is None
    assert p2["sleep_efficiency_pct_mean"] is None and p2["deep_sleep_pct_mean"] is None
    assert p3["steps_valid_days"] == 14 and p3["steps_daily_mean"] == approx(50.0)
    assert rows["P001"]["sleep_valid_days"] == 14
    assert rows["P001"]["sleep_total_min_mean"] == approx(440.0)


def test_nothing_is_imputed_and_coverage_counts_are_never_null(tmp_path):
    data, out = built(tmp_path)
    for row in cohort_rows(out).values():
        for name in ("window_days", "sleep_valid_days", "sleep_stage_valid_days",
                     "steps_valid_days", "hr_valid_days"):
            assert row[name] is not None, name
    empty = cohort_rows(out)["P004"]
    assert empty["steps_valid_days"] == 0 and empty["steps_daily_mean"] is None  # not 0.0
    assert empty["hr_valid_days"] == 0 and empty["hr_daily_mean_bpm"] is None
    p1 = cohort_rows(out)["P001"]
    # a zero-step day is a valid day (15 days: 14 x 100+i and one zero), missing days are not zero days
    assert p1["steps_valid_days"] == 15 and p1["steps_daily_mean"] == approx(1491 / 15)


def test_partial_duplicate_withheld_and_out_of_window_days_follow_the_daily_rules(tmp_path):
    data, out = built(tmp_path)
    p1 = cohort_rows(out)["P001"]
    assert p1["sleep_valid_days"] == 14       # identical duplicate counts once; conflict withheld
    assert p1["sleep_total_min_mean"] == approx(440.0)  # not a sum of two sessions
    assert p1["steps_valid_days"] == 15       # duplicate minute once; conflicting/invalid missing
    assert p1["hr_valid_days"] == 16          # 300 bpm withheld; day outside the window ignored


def test_heart_rate_mean_gives_every_valid_day_the_same_weight(tmp_path):
    data, out = built(tmp_path)
    value = cohort_rows(out)["P001"]["hr_daily_mean_bpm"]
    assert value == approx(1024 / 16)
    assert value != approx(1244 / 18)  # the minute-weighted mean is not used


def test_cohort_means_reconcile_with_the_daily_table(tmp_path):
    data, out = built(tmp_path)
    assert reconcile(out) == 6


def reconcile(out: Path) -> int:
    """Check every cohort row against the gold_clinical_daily rows of the same participant."""
    daily = daily_rows(out)
    checked = 0
    for pid, row in cohort_rows(out).items():
        days = [d for d in daily if d["participant_id"] == pid]

        def vals(name):
            return [d[name] for d in days if d[name] is not None]

        def expect(name, values):
            if len(values) >= MIN_VALID_DAYS:
                assert row[name] == approx(sum(values) / len(values)), (pid, name)
            else:
                assert row[name] is None, (pid, name)

        sleep, deep, eff, steps = (vals("total_sleep_min"), vals("deep_sleep_pct"),
                                   vals("sleep_efficiency_pct"), vals("total_steps"))
        hr_days = [d["hr_mean_bpm"] for d in days
                   if d["hr_valid_minute_count"] > 0 and d["hr_mean_bpm"] is not None]
        assert row["sleep_valid_days"] == len(sleep)
        assert row["sleep_stage_valid_days"] == len(deep) == len(eff)
        assert row["steps_valid_days"] == len(steps)
        assert row["hr_valid_days"] == len(hr_days) == sum(
            1 for d in days if d["hr_valid_minute_count"] > 0)
        expect("sleep_total_min_mean", sleep)
        expect("deep_sleep_pct_mean", deep)
        expect("sleep_efficiency_pct_mean", eff)
        expect("steps_daily_mean", steps)
        expect("hr_daily_mean_bpm", hr_days)
        assert row["window_days"] == DAYS
        checked += 1
    return checked


def test_cohort_values_still_reconcile_after_an_incremental_run(tmp_path):
    data, out = prepared(tmp_path)
    records = read_events(data, "P001", HR)
    records[1]["heart_rate_bpm"] = 64  # D[1] minute 0: 62 -> 64
    write_events(data, "P001", HR, records)
    assert inc(data, out).outcome == OUTCOME_INCREMENTAL
    assert reconcile(out) == 6


def test_table_shares_only_the_participant_key_with_the_daily_table(tmp_path):
    data, out = prepared(tmp_path)
    _, daily = table_rows(out, "DESCRIBE gold_clinical_daily")
    assert {n for n, _ in COHORT_COLUMNS} & {r[0] for r in daily} == {"participant_id"}


def test_cohort_is_derived_from_silver_not_from_gold(tmp_path):
    data, out = built(tmp_path)
    before = cohort_rows(out)
    edit_db(out, "UPDATE gold_clinical_daily SET total_steps = 999999, hr_mean_bpm = 1.0, "
                 "total_sleep_min = 1, deep_sleep_pct = 1.0, sleep_efficiency_pct = 1.0")
    refresh(out)
    assert cohort_rows(out) == before


@pytest.mark.parametrize("days", [15, 10, 20])
def test_summary_covers_the_whole_window_including_a_trailing_partial_block(tmp_path, days):
    data = fixture_c(tmp_path)
    out = tmp_path / "out"
    full(data, out, days)
    rows = cohort_rows(out)
    assert sorted(rows) == ENROLLED
    assert {r["window_days"] for r in rows.values()} == {days}
    p1 = rows["P001"]
    assert p1["steps_valid_days"] == min(days, 15)
    if days == 15:  # whole seven-day weeks would stop at day 14; the cohort keeps day 15
        assert p1["steps_daily_mean"] == approx(1491 / 15)
        assert p1["hr_valid_days"] == 15 and p1["hr_daily_mean_bpm"] == approx(914 / 15)
    if days == 10:  # fewer than 14 valid days: counts exposed, means NULL
        assert p1["steps_valid_days"] == 10 and p1["steps_daily_mean"] is None


# ---------------------------------------------------------------- refresh, MERGE

def test_refresh_creates_a_missing_table_and_recomputes_every_row_even_with_affected(tmp_path):
    data, out = built(tmp_path)
    expected = cohort_rows(out)
    edit_db(out, f"DROP TABLE {TABLE_NAME}")
    refresh(out, {"P001": frozenset({D[0]})})
    assert cohort_columns(out) == list(COHORT_COLUMNS)
    assert cohort_rows(out) == expected


def test_refresh_rebuilds_a_wrong_layout_and_recomputes_every_row(tmp_path):
    data, out = built(tmp_path)
    expected = cohort_rows(out)
    edit_db(out, f"ALTER TABLE {TABLE_NAME} ALTER COLUMN window_days TYPE BIGINT")
    refresh(out, {"P001": frozenset({D[0]})})
    assert cohort_columns(out) == list(COHORT_COLUMNS)
    assert cohort_rows(out) == expected


def test_merge_rerun_is_idempotent_and_never_duplicates(tmp_path):
    data, out = built(tmp_path)
    before = cohort_rows(out)
    for _ in range(2):
        refresh(out)
    assert cohort_rows(out) == before
    _, count = table_rows(out, f"SELECT count(*) FROM {TABLE_NAME}")
    assert count == [(6,)]


def test_full_refresh_repairs_values_and_removes_stale_keys(tmp_path):
    data, out = built(tmp_path)
    expected = cohort_rows(out)
    edit_db(out, f"UPDATE {TABLE_NAME} SET steps_daily_mean = -1, chronotype_cohort = 'A'",
            f"INSERT INTO {TABLE_NAME} (participant_id, window_days, sleep_valid_days, "
            "sleep_stage_valid_days, steps_valid_days, hr_valid_days) "
            "VALUES ('P777', 28, 0, 0, 0, 0)")
    refresh(out)
    assert cohort_rows(out) == expected


def test_refresh_with_affected_recomputes_only_those_participants(tmp_path):
    data, out = built(tmp_path)
    expected = cohort_rows(out)
    edit_db(out, f"UPDATE {TABLE_NAME} SET steps_daily_mean = -1.0")
    refresh(out, {"P001": frozenset({D[0], D[3]}), "P003": frozenset({D[27]})})
    after = cohort_rows(out)
    assert after["P001"] == expected["P001"] and after["P003"] == expected["P003"]
    for pid in ("P002", "P004", "P005", "P006"):
        assert after[pid]["steps_daily_mean"] == -1.0, pid


@pytest.mark.parametrize("affected", [
    {}, {"P001": frozenset()}, {"P001": frozenset({D[28]})}, {"P001": frozenset({D[39]})},
    {"P001": frozenset({D[0] - (D[1] - D[0])})}, {"P999": frozenset({D[0]})},
    {"P404": frozenset({D[0]})},
])
def test_refresh_ignores_empty_out_of_window_and_unenrolled_affected(tmp_path, affected):
    data, out = built(tmp_path)
    edit_db(out, f"UPDATE {TABLE_NAME} SET steps_daily_mean = -1.0")
    corrupted = cohort_rows(out)
    refresh(out, affected)
    assert cohort_rows(out) == corrupted


def test_output_is_deterministic_across_independent_full_runs(tmp_path):
    data = fixture_c(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    full(data, first)
    full(data, second)
    assert state(first)["tables"][TABLE_NAME] == state(second)["tables"][TABLE_NAME]
    assert len(state(first)["tables"][TABLE_NAME]) == 6
    refresh(first)
    assert state(first)["tables"][TABLE_NAME] == state(second)["tables"][TABLE_NAME]


# ---------------------------------------------------------------- incremental

def test_corrected_record_changes_exactly_one_cohort_row(tmp_path):
    data, out = prepared(tmp_path)
    before = cohort_rows(out)
    records = read_events(data, "P001", HR)
    records[0]["heart_rate_bpm"] = 61  # D[0] minute 0: 60 -> 61
    write_events(data, "P001", HR, records)
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    after = cohort_rows(out)
    assert changed_keys(before, after) == {"P001"}
    assert after["P001"]["hr_daily_mean_bpm"] == approx(1025 / 16)
    assert_equals_full(data, out, tmp_path)


def test_new_day_changes_exactly_one_cohort_row(tmp_path):
    data, out = prepared(tmp_path)
    before = cohort_rows(out)
    write_events(data, "P002", HR, [hr(D[5], 0, 65)])
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    after = cohort_rows(out)
    assert changed_keys(before, after) == {"P002"}
    assert after["P002"]["hr_valid_days"] == 1 and after["P002"]["hr_daily_mean_bpm"] is None
    assert_equals_full(data, out, tmp_path)


def test_removed_file_changes_only_the_participant_it_fed(tmp_path):
    data, out = prepared(tmp_path)
    before = cohort_rows(out)
    events_path(data, "P001", STEPS).unlink()
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    after = cohort_rows(out)
    assert changed_keys(before, after) == {"P001"}
    assert after["P001"]["steps_valid_days"] == 0 and after["P001"]["steps_daily_mean"] is None
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


def test_incremental_refreshes_the_cohort_table_through_the_extension_point(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    calls = []
    real = COHORT.refresh

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


def test_participants_file_change_recomputes_every_row_and_equals_full(tmp_path):
    data, out = prepared(tmp_path)
    changed_p001 = ["P001", "FBT-0001-AAAAAAAA", "38", "183", "male", "A", "167"]
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
              [changed_p001, P002, P003, P004, P005_A, P005_B, P006_A, P006_BLANK])
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "shared_input_changed")
    rows = cohort_rows(out)
    assert (rows["P001"]["chronotype_cohort"], rows["P001"]["cohort_flag"]) == ("A", None)
    assert_row(rows["P001"], {k: v for k, v in EXPECTED["P001"].items()
                              if k not in ("chronotype_cohort",)})
    assert_equals_full(data, out, tmp_path)


def test_adding_and_removing_participants_follows_the_full_rebuild_path(tmp_path):
    data, out = prepared(tmp_path)
    p007 = ["P007", "FBT-0007-GGGGGGGG", "41", "175", "male", "B", "158"]
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
              [P001, P002, p007])
    result = inc(data, out)
    assert result.fallback_reason == "shared_input_changed"
    assert sorted(cohort_rows(out)) == ["P001", "P002", "P007"]
    assert cohort_rows(out)["P007"]["chronotype_cohort"] == "B"
    assert_equals_full(data, out, tmp_path)


def test_survey_change_follows_the_full_rebuild_path(tmp_path):
    data, out = prepared(tmp_path)
    write_csv(data / "health_summaries" / "wellness_survey.csv",
              ["participant_id", "date", "fatigue_score", "stress_score", "readiness_score",
               "sleep_quality_score"],
              [["P002", D[0].isoformat(), "3", "3", "3", "3"]])
    result = inc(data, out)
    assert result.fallback_reason == "shared_input_changed"
    assert_equals_full(data, out, tmp_path)


@pytest.mark.parametrize("days", [21, 10, 15, 35])
def test_window_change_follows_the_full_rebuild_path(tmp_path, days):
    data, out = prepared(tmp_path)
    result = inc(data, out, days)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "window_changed")
    assert {r["window_days"] for r in cohort_rows(out).values()} == {days}
    assert_equals_full(data, out, tmp_path, days)


def test_old_schema_check_covers_the_cohort_table():
    assert TABLE_NAME in incremental._EXPECTED_TABLES
    assert incremental._expected_schema()[TABLE_NAME] == list(COHORT_COLUMNS)


DEGRADE = {
    "cohort_table_missing": drop_table(TABLE_NAME),
    "cohort_type_changed": alter_type(TABLE_NAME, "window_days", "BIGINT"),
    "cohort_columns_reordered": swap_first_two_columns(TABLE_NAME),
    "cohort_column_missing": lambda con: con.execute(
        f"ALTER TABLE {TABLE_NAME} DROP COLUMN hr_daily_mean_bpm"),
    "cohort_measure_type_changed": alter_type(TABLE_NAME, "steps_daily_mean", "FLOAT"),
}


@pytest.mark.parametrize("name", sorted(DEGRADE))
def test_old_cohort_schema_falls_back_to_a_full_rebuild(tmp_path, name):
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
    assert cohort_columns(out) == list(COHORT_COLUMNS)
    assert_equals_full(data, out, tmp_path)


def test_failed_incremental_run_leaves_the_cohort_output_unchanged(tmp_path):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    assert len(cohort_rows(out)) == 6
    events_path(data, "P001", HR).write_text("{not json", encoding="utf-8")
    with pytest.raises(PipelineError):
        inc(data, out)
    assert tree_hashes(out) == before


def test_failing_cohort_refresh_rolls_the_whole_run_back(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    assert len(cohort_rows(out)) == 6

    class Boom:
        name = TABLE_NAME

        def refresh(self, con, affected):
            COHORT.refresh(con, affected)
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
    assert not [r for r in rows if "CHRONO" in r[0].upper() or "COHORT" in r[0].upper()]
    _, rejects = table_rows(out, "SELECT DISTINCT rule_id FROM quality_rejects")
    assert not [r for r in rejects if "CHRONO" in r[0].upper() or "COHORT" in r[0].upper()]
    report = (out / "quality" / "report.md").read_text(encoding="utf-8").lower()
    assert "cohort" not in report and "chronotype_" not in report
    _, daily = table_rows(out, "DESCRIBE gold_clinical_daily")
    assert [(r[0], r[1]) for r in daily] == list(ALL_GOLD_COLUMNS)
    _, runs = table_rows(out, "SELECT DISTINCT run_id FROM quality_results")
    assert len(runs) == 1 and len(runs[0][0]) == 16


def test_compare_command_covers_the_cohort_table_without_code_changes(tmp_path, capsys):
    data = fixture_c(tmp_path)
    left, right = tmp_path / "l", tmp_path / "r"
    full(data, left)
    full(data, right)
    assert main(["compare", str(left), str(right)]) == 0
    assert capsys.readouterr().out == "identical\n"
    edit_db(right, f"UPDATE {TABLE_NAME} SET hr_daily_mean_bpm = 1 WHERE participant_id = 'P001'")
    assert main(["compare", str(left), str(right)]) == 1
    out = capsys.readouterr().out
    assert f"table {TABLE_NAME}: rows differ: left_rows=6 right_rows=6 left_only=1 right_only=1" in out
    assert "P001" not in out  # counts only, never row values
