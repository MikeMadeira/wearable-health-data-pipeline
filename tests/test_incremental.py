"""Behavior tests for the INC-006 incremental mode (see specs/INC-006.md). Fixtures are synthetic."""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path

import duckdb
import pytest

from tests.test_heart_rate import GOLD_BASE
from tests.test_sleep import session
from tests.test_walking_skeleton import (
    P001, P002, PARTICIPANT_COLUMNS, START, DAYS, SURVEY_COLUMNS, WINDOW, default_survey,
    make_data, run, survey_row, tree_hashes, write_csv,
)
from wearable_pipeline import PipelineError, main, run_pipeline
from wearable_pipeline import incremental
from wearable_pipeline.incremental import (
    OUTCOME_FULL, OUTCOME_INCREMENTAL, OUTCOME_NOOP, IncrementalResult, changed_days,
    diff_manifests, run_incremental,
)

DAY1, DAY2, DAY3 = WINDOW
P003 = ["P003", "FBT-0003-CCCCCCCC", "40", "170", "female", "C", "160"]
HR = "heart_rate.json"
STEPS = "steps.json"
SLEEP = "sleep.json"


# ---------------------------------------------------------------- helpers

def stamp(day: date, minute: int) -> str:
    return f"{day.isoformat()}T{minute // 60:02d}:{minute % 60:02d}:00"


def hr(day: date, minute: int, bpm) -> dict:
    return {"timestamp": stamp(day, minute), "heart_rate_bpm": bpm}


def st(day: date, minute: int, steps) -> dict:
    return {"timestamp": stamp(day, minute), "steps": steps}


def events_path(data: Path, pid: str, name: str) -> Path:
    return data / "wearable_events" / pid / name


def write_events(data: Path, pid: str, name: str, records: list) -> None:
    path = events_path(data, pid, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def read_events(data: Path, pid: str, name: str) -> list:
    return json.loads(events_path(data, pid, name).read_text(encoding="utf-8"))


def baseline_data(root: Path) -> Path:
    data = make_data(root)
    write_events(data, "P001", HR, [hr(DAY1, m, 60 + m) for m in range(5)]
                 + [hr(DAY2, m, 70 + m) for m in range(3)])
    write_events(data, "P001", STEPS, [st(DAY1, 0, 10), st(DAY1, 1, 20), st(DAY1, 2, 30),
                                       st(DAY2, 0, 5)])
    write_events(data, "P001", SLEEP, [session("P001", DAY1)])
    write_events(data, "P002", HR, [hr(DAY1, m, 80 + m) for m in range(3)])
    write_events(data, "P002", STEPS, [st(DAY1, 0, 7)])
    return data


def inc(data: Path, out: Path, **kwargs) -> IncrementalResult:
    return run_incremental(data, out, study_start=START, study_days=DAYS, **kwargs)


def state(out: Path) -> dict:
    """Every base table (sorted rows, column names and types) plus the report bytes."""
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY 1").fetchall()]
        tables = {n: con.execute(f"SELECT * FROM {n} ORDER BY ALL").fetchall() for n in names}
        columns = {n: [(r[0], r[1]) for r in con.execute(f"DESCRIBE {n}").fetchall()]
                   for n in names}
    finally:
        con.close()
    return {"tables": tables, "columns": columns,
            "report": (out / "quality" / "report.md").read_bytes()}


def gold_rows(out: Path) -> dict[tuple[str, date], tuple]:
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        rows = con.execute("SELECT * FROM gold_clinical_daily").fetchall()
    finally:
        con.close()
    return {(r[0], r[1]): r for r in rows}


def gold_dict(out: Path, pid: str, day: date) -> dict:
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        cursor = con.execute(
            "SELECT * FROM gold_clinical_daily WHERE participant_id = ? AND local_date = ?",
            [pid, day])
        names = [c[0] for c in cursor.description]
        row = cursor.fetchall()
    finally:
        con.close()
    assert len(row) == 1
    return dict(zip(names, row[0]))


def run_id_of(out: Path) -> str:
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        return con.execute("SELECT DISTINCT run_id FROM quality_results").fetchone()[0]
    finally:
        con.close()


def fresh_full(data: Path, tmp_path: Path) -> Path:
    """A full rebuild of ``data`` into a brand-new output directory."""
    out = tmp_path / f"fresh-{len(list(tmp_path.glob('fresh-*')))}"
    run(data, out)
    return out


def assert_equals_full(data: Path, out: Path, tmp_path: Path) -> None:
    expected = state(fresh_full(data, tmp_path))
    actual = state(out)
    assert actual["columns"] == expected["columns"]
    for name, rows in expected["tables"].items():
        assert actual["tables"][name] == rows, name
    assert actual["tables"].keys() == expected["tables"].keys()
    assert actual["report"] == expected["report"]


def changed_keys(before: dict, after: dict) -> set[tuple[str, date]]:
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


def prepared(tmp_path: Path) -> tuple[Path, Path]:
    data = baseline_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    return data, out


class InjectedFailure(Exception):
    """Not a PipelineError and not a DuckDB error: must propagate unchanged after rollback."""


class FakeDerived:
    """A stand-in for a later gold table, plugged into the extension point."""

    name = "gold_fake"

    def __init__(self, fail: bool = False) -> None:
        self.calls: list = []
        self.fail = fail

    def refresh(self, con, affected) -> None:
        self.calls.append(affected)
        con.execute("CREATE TABLE IF NOT EXISTS gold_fake (n INTEGER)")
        con.execute("INSERT INTO gold_fake VALUES (?)", [len(self.calls)])
        if self.fail:
            raise InjectedFailure("injected failure")


# ---------------------------------------------------------------- mutations

def m_new_day(data: Path) -> None:
    write_events(data, "P001", HR, read_events(data, "P001", HR) + [hr(DAY3, 0, 66)])
    write_events(data, "P001", STEPS, read_events(data, "P001", STEPS) + [st(DAY3, 0, 9)])


def m_corrected(data: Path) -> None:
    records = read_events(data, "P001", HR)
    records[2]["heart_rate_bpm"] = 90
    write_events(data, "P001", HR, records)


def m_record_removed(data: Path) -> None:
    write_events(data, "P001", HR, read_events(data, "P001", HR)[:-1])


def m_steps_file_removed(data: Path) -> None:
    events_path(data, "P001", STEPS).unlink()


def m_sleep_file_removed(data: Path) -> None:
    events_path(data, "P001", SLEEP).unlink()


def m_hr_file_removed(data: Path) -> None:
    events_path(data, "P002", HR).unlink()


def m_new_participant_file(data: Path) -> None:
    write_events(data, "P002", SLEEP, [session("P002", DAY2)])


def m_defects(data: Path) -> None:
    write_events(data, "P001", HR, [
        hr(DAY1, 0, 60), hr(DAY1, 0, 61), hr(DAY1, 1, 60), hr(DAY1, 1, 60), hr(DAY1, 2, 500),
        hr(DAY1, 3, "x"), {"timestamp": "2026-01-08T10:00:00Z", "heart_rate_bpm": 70},
        {"timestamp": "bad", "heart_rate_bpm": 70}, hr(date(2030, 1, 1), 0, 70),
        hr(DAY2, 0, 75)])


def m_orphan_folder(data: Path) -> None:
    write_events(data, "P999", HR, [hr(DAY1, 0, 70)])


def m_hr_folder_mismatch(data: Path) -> None:
    records = read_events(data, "P001", HR)
    records.append({"participant_id": "P002", "timestamp": stamp(DAY1, 30),
                    "heart_rate_bpm": 99})
    write_events(data, "P001", HR, records)


def m_sleep_pid_differs(data: Path) -> None:
    write_events(data, "P001", SLEEP, [session("P001", DAY1), session("P002", DAY2)])


def m_unrelated_file(data: Path) -> None:
    (data / "notes.txt").write_text("extra\n", encoding="utf-8")


def m_dictionary(data: Path) -> None:
    (data / "data_dictionary.md").write_text("# changed dictionary\n", encoding="utf-8")


LOCAL = {
    "new_day": m_new_day, "corrected": m_corrected, "record_removed": m_record_removed,
    "steps_file_removed": m_steps_file_removed, "sleep_file_removed": m_sleep_file_removed,
    "hr_file_removed": m_hr_file_removed, "new_participant_file": m_new_participant_file,
    "defects": m_defects, "orphan_folder": m_orphan_folder,
    "hr_folder_mismatch": m_hr_folder_mismatch, "sleep_pid_differs": m_sleep_pid_differs,
    "unrelated_file": m_unrelated_file, "dictionary": m_dictionary,
}


def s_participant_added(data: Path) -> None:
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
              [P001, P002, P003])
    write_csv(data / "health_summaries" / "wellness_survey.csv", SURVEY_COLUMNS,
              default_survey() + [survey_row("P003", DAY1.isoformat())])


def s_participant_removed(data: Path) -> None:
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS, [P001])


def s_survey_changed(data: Path) -> None:
    rows = default_survey()
    rows[0] = survey_row("P001", DAY1.isoformat(), ["1", "1", "1", "1"])
    write_csv(data / "health_summaries" / "wellness_survey.csv", SURVEY_COLUMNS, rows)


def s_device_metadata(data: Path) -> None:
    write_csv(data / "health_summaries" / "device_metadata.csv",
              ["participant_id", "device_id"], [["P001", "FBT-9999-ZZZZZZZZ"]])


SHARED = {
    "participant_added": s_participant_added, "participant_removed": s_participant_removed,
    "survey_changed": s_survey_changed, "device_metadata": s_device_metadata,
}


# ---------------------------------------------------------------- pure units

def test_diff_manifests_classifies_every_path():
    previous = {"a": "1", "b": "2", "c": "3", "d": "4"}
    current = {"a": "1", "b": "9", "e": "5", "d": "4"}
    diff = diff_manifests(previous, current)
    assert diff.new == ("e",)
    assert diff.changed == ("b",)
    assert diff.removed == ("c",)
    assert diff.unchanged == ("a", "d")


def test_diff_manifests_is_sorted_and_empty_inputs_are_empty():
    diff = diff_manifests({}, {"z": "1", "a": "2"})
    assert diff.new == ("a", "z")
    assert (diff.changed, diff.removed, diff.unchanged) == ((), (), ())
    empty = diff_manifests({}, {})
    assert (empty.new, empty.changed, empty.removed, empty.unchanged) == ((), (), (), ())


def test_diff_manifests_does_not_mutate_inputs():
    previous, current = {"a": "1"}, {"a": "2"}
    diff_manifests(previous, current)
    assert previous == {"a": "1"} and current == {"a": "2"}


def test_changed_days_reports_only_days_whose_row_multiset_differs():
    prev = [("P001", DAY1, 1), ("P001", DAY1, 2), ("P001", DAY2, 3)]
    new = [("P001", DAY1, 1), ("P001", DAY1, 9), ("P001", DAY2, 3)]
    assert changed_days(prev, new) == frozenset({DAY1})


def test_changed_days_ignores_row_order_and_detects_added_and_removed_days():
    prev = [("P001", DAY1, 1), ("P001", DAY2, 2)]
    assert changed_days(prev, list(reversed(prev))) == frozenset()
    assert changed_days(prev, prev + [("P001", DAY3, 3)]) == frozenset({DAY3})
    assert changed_days(prev, prev[:1]) == frozenset({DAY2})
    assert changed_days([], []) == frozenset()


def test_changed_days_counts_duplicate_rows_as_a_multiset_and_skips_null_dates():
    prev = [("P001", DAY1, 1)]
    assert changed_days(prev, prev + prev) == frozenset({DAY1})
    assert changed_days([("P001", None, 1)], []) == frozenset()


def test_changed_days_honours_date_index():
    prev = [(1, "x", DAY1)]
    new = [(2, "x", DAY1)]
    assert changed_days(prev, new, date_index=2) == frozenset({DAY1})


# ---------------------------------------------------------------- equivalence

@pytest.mark.parametrize("name", sorted(LOCAL))
def test_incremental_equals_full_rebuild_after_local_change(tmp_path, name):
    data, out = prepared(tmp_path)
    LOCAL[name](data)
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    assert result.fallback_reason is None
    assert_equals_full(data, out, tmp_path)


@pytest.mark.parametrize("name", sorted(SHARED))
def test_incremental_equals_full_rebuild_after_shared_input_change(tmp_path, name):
    data, out = prepared(tmp_path)
    SHARED[name](data)
    result = inc(data, out)
    assert result.outcome == OUTCOME_FULL
    assert result.fallback_reason == "shared_input_changed"
    assert_equals_full(data, out, tmp_path)


def test_participant_removed_prunes_its_gold_rows_unlike_full_mode_merge(tmp_path):
    data, out = prepared(tmp_path)
    s_participant_removed(data)
    inc(data, out)
    assert {k[0] for k in gold_rows(out)} == {"P001"}
    assert len(gold_rows(out)) == DAYS


def test_participant_added_gets_one_gold_row_per_window_day(tmp_path):
    data, out = prepared(tmp_path)
    s_participant_added(data)
    inc(data, out)
    assert sorted(k for k in gold_rows(out) if k[0] == "P003") == [("P003", d) for d in WINDOW]
    assert gold_dict(out, "P003", DAY1)["survey_present"] is True


def test_changed_study_window_is_a_shared_change_and_equals_full(tmp_path):
    data, out = prepared(tmp_path)
    result = run_incremental(data, out, study_start=START, study_days=DAYS - 1)
    assert result.outcome == OUTCOME_FULL
    assert result.fallback_reason == "window_changed"
    fresh = tmp_path / "fresh-window"
    run_pipeline(data, fresh, study_start=START, study_days=DAYS - 1)
    assert state(out) == state(fresh)


def stored_window(out: Path) -> list[tuple]:
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        columns = [(r[0], r[1]) for r in con.execute("DESCRIBE run_window").fetchall()]
        rows = con.execute("SELECT study_start, study_days FROM run_window").fetchall()
    finally:
        con.close()
    assert columns == [("study_start", "DATE"), ("study_days", "INTEGER")]
    return rows


def stale_gold_database(tmp_path: Path) -> tuple[Path, Path]:
    """A 3-day run followed by a 2-day full run: full mode keeps the 3rd-day gold rows."""
    data = baseline_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    run_pipeline(data, out, study_start=START, study_days=DAYS - 1)
    assert {k[1] for k in gold_rows(out)} == set(WINDOW)  # stale rows are really there
    return data, out


def test_study_window_is_stored_by_every_run(tmp_path):
    data, out = prepared(tmp_path)
    assert stored_window(out) == [(START, DAYS)]
    m_corrected(data)
    inc(data, out)
    assert stored_window(out) == [(START, DAYS)]
    run_pipeline(data, out, study_start=START, study_days=DAYS - 1)
    assert stored_window(out) == [(START, DAYS - 1)]
    run_incremental(data, out, study_start=START, study_days=DAYS)
    assert stored_window(out) == [(START, DAYS)]


@pytest.mark.parametrize("window", [(START, DAYS - 1), (START, DAYS + 1), (DAY2, DAYS)],
                         ids=["shorter", "longer", "shifted"])
def test_changed_window_takes_the_shared_input_path_then_is_stable(tmp_path, window):
    data, out = prepared(tmp_path)
    start, days = window
    result = run_incremental(data, out, study_start=start, study_days=days)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "window_changed")
    fresh = tmp_path / "fresh-window"
    run_pipeline(data, fresh, study_start=start, study_days=days)
    assert state(out) == state(fresh)
    assert stored_window(out) == [(start, days)]
    before = tree_hashes(out)
    again = run_incremental(data, out, study_start=start, study_days=days)
    assert again.outcome == OUTCOME_NOOP
    assert tree_hashes(out) == before


def test_stale_gold_rows_with_unchanged_inputs_and_window_are_a_noop(tmp_path):
    data, out = stale_gold_database(tmp_path)
    before = tree_hashes(out)
    result = run_incremental(data, out, study_start=START, study_days=DAYS - 1)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_NOOP, None)
    assert result.affected_gold_rows == 0
    assert tree_hashes(out) == before


def test_empty_gold_with_unchanged_inputs_and_window_is_a_noop(tmp_path):
    data = baseline_data(tmp_path)
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS, [])
    write_csv(data / "health_summaries" / "wellness_survey.csv", SURVEY_COLUMNS, [])
    out = tmp_path / "out"
    run(data, out)
    assert gold_rows(out) == {}
    before = tree_hashes(out)
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_NOOP, None)
    assert tree_hashes(out) == before
    assert inc(data, out).outcome == OUTCOME_NOOP


def test_stale_gold_with_an_event_change_stays_incremental(tmp_path):
    data, out = stale_gold_database(tmp_path)
    m_corrected(data)
    result = run_incremental(data, out, study_start=START, study_days=DAYS - 1)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_INCREMENTAL, None)
    assert result.affected_gold_rows == 1


def test_window_equal_to_the_stale_gold_dates_is_still_a_window_change(tmp_path):
    """The window is read from the stored value, not inferred from the gold dates."""
    data, out = stale_gold_database(tmp_path)  # gold covers 3 days, stored window is 2 days
    result = inc(data, out)  # requested window: 3 days
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "window_changed")
    assert {k[1] for k in gold_rows(out)} == set(WINDOW)
    assert state(out) == state(fresh_full(data, tmp_path))
    assert stored_window(out) == [(START, DAYS)]


def test_stale_gold_is_pruned_when_a_window_change_takes_the_shared_path(tmp_path):
    data, out = prepared(tmp_path)
    run_pipeline(data, out, study_start=START, study_days=DAYS - 1)  # stale DAY3 rows stay
    result = run_incremental(data, out, study_start=START, study_days=DAYS - 1)
    assert result.outcome == OUTCOME_NOOP  # same window as stored: stale rows are not touched
    assert {k[1] for k in gold_rows(out)} == set(WINDOW)
    s_survey_changed(data)  # now a shared change: gold is rebuilt and pruned to the window
    run_incremental(data, out, study_start=START, study_days=DAYS - 1)
    assert {k[1] for k in gold_rows(out)} == set(WINDOW[: DAYS - 1])


def test_two_successive_incremental_runs_equal_one_full_rebuild(tmp_path):
    data, out = prepared(tmp_path)
    m_new_day(data)
    inc(data, out)
    m_corrected(data)
    m_hr_file_removed(data)
    inc(data, out)
    assert_equals_full(data, out, tmp_path)


def test_quality_outputs_are_global_not_per_delta(tmp_path):
    data, out = prepared(tmp_path)
    write_events(data, "P002", HR, [hr(DAY1, 0, 500), hr(DAY1, 1, 60), hr(DAY1, 1, 61)])
    inc(data, out)  # P002 defective, P001 untouched
    m_corrected(data)
    inc(data, out)  # only P001 changes; P002 defects must still be counted
    assert_equals_full(data, out, tmp_path)
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        failed = dict(con.execute(
            "SELECT rule_id, failed_count FROM quality_results").fetchall())
        rejects = con.execute(
            "SELECT count(*) FROM quality_rejects WHERE row_ref LIKE '%P002/heart_rate.json:%'"
        ).fetchone()[0]
    finally:
        con.close()
    assert failed["HR_VALUE_OUT_OF_RANGE"] == 1
    assert failed["HR_DUPLICATE_MINUTE_CONFLICT"] == 2
    assert rejects == 3
    assert run_id_of(out) == run_id_of(fresh_full(data, tmp_path))


def test_removed_file_removes_its_silver_rows_rejects_and_quality_counts(tmp_path):
    data, out = prepared(tmp_path)
    write_events(data, "P002", HR, [hr(DAY1, 0, 500)])
    inc(data, out)
    events_path(data, "P002", HR).unlink()
    inc(data, out)
    assert_equals_full(data, out, tmp_path)
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM quality_rejects WHERE row_ref LIKE "
                           "'%P002/heart_rate.json:%'").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM silver_heart_rate_minute WHERE "
                           "participant_id = 'P002'").fetchone()[0] == 0
    finally:
        con.close()


def test_run_id_and_report_follow_the_current_manifest(tmp_path):
    data, out = prepared(tmp_path)
    before = run_id_of(out)
    m_corrected(data)
    result = inc(data, out)
    assert result.run_id != before
    assert result.run_id == run_id_of(out) == run_id_of(fresh_full(data, tmp_path))
    assert f"Run id: {result.run_id}" in (out / "quality" / "report.md").read_text("utf-8")
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT DISTINCT run_id FROM quality_rejects").fetchall() in (
            [], [(result.run_id,)])
    finally:
        con.close()


def test_incremental_result_lists_the_classified_paths(tmp_path):
    data, out = prepared(tmp_path)
    m_corrected(data)
    m_new_participant_file(data)
    events_path(data, "P002", STEPS).unlink()
    result = inc(data, out)
    assert result.changed_paths == ("wearable_events/P001/heart_rate.json",)
    assert result.new_paths == ("wearable_events/P002/sleep.json",)
    assert result.removed_paths == ("wearable_events/P002/steps.json",)
    assert result.database_path == out / "wearable.duckdb"
    assert result.report_path == out / "quality" / "report.md"


# ---------------------------------------------------------------- reads

def test_only_changed_event_files_are_parsed(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    calls: list[str] = []
    real = incremental.read_event_file

    def spy(path, relative, kind):
        calls.append(relative)
        return real(path, relative, kind)

    monkeypatch.setattr(incremental, "read_event_file", spy)
    m_corrected(data)
    inc(data, out)
    assert calls == ["wearable_events/P001/heart_rate.json"]


def test_new_file_is_parsed_once_and_removed_file_is_not_parsed(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    calls: list[str] = []
    real = incremental.read_event_file

    def spy(path, relative, kind):
        calls.append(relative)
        return real(path, relative, kind)

    monkeypatch.setattr(incremental, "read_event_file", spy)
    m_new_participant_file(data)
    events_path(data, "P002", STEPS).unlink()
    inc(data, out)
    assert calls == ["wearable_events/P002/sleep.json"]


def test_noop_run_parses_nothing(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)

    def forbidden(path, relative, kind):
        raise AssertionError(f"unexpected parse of {relative}")

    monkeypatch.setattr(incremental, "read_event_file", forbidden)
    assert inc(data, out).outcome == OUTCOME_NOOP


def test_unclassified_and_documentation_changes_parse_no_event_file(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    monkeypatch.setattr(incremental, "read_event_file", lambda *a: pytest.fail("parsed"))
    m_unrelated_file(data)
    m_dictionary(data)
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    assert result.affected_gold_rows == 0


# ---------------------------------------------------------------- affected gold rows

EXPECTED_KEYS = {
    "new_day": {("P001", DAY3)},
    "corrected": {("P001", DAY1)},
    "record_removed": {("P001", DAY2)},
    "steps_file_removed": {("P001", DAY1), ("P001", DAY2)},
    "sleep_file_removed": {("P001", DAY1)},
    "hr_file_removed": {("P002", DAY1)},
    "new_participant_file": {("P002", DAY2)},
    "unrelated_file": set(),
    "dictionary": set(),
    "orphan_folder": set(),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_KEYS))
def test_only_the_affected_gold_rows_change(tmp_path, name):
    data, out = prepared(tmp_path)
    before = gold_rows(out)
    LOCAL[name](data)
    result = inc(data, out)
    after = gold_rows(out)
    assert changed_keys(before, after) == EXPECTED_KEYS[name]
    assert after.keys() == before.keys()
    assert result.affected_gold_rows == len(EXPECTED_KEYS[name])


# (changed gold keys, staged rows). Staged rows are the affected (participant, day) pairs that
# have a gold row; a staged row may keep its values (a folder-mismatch record leaves gold as is).
EXPECTED_DEFECT_CASES = {
    "defects": ({("P001", DAY1), ("P001", DAY2)}, 2),  # 2030 and unparseable dates: no gold row
    "orphan_folder": (set(), 0),  # P999 is not enrolled
    "hr_folder_mismatch": (set(), 1),  # P002 DAY1 silver changes, its gold values do not
    "sleep_pid_differs": ({("P002", DAY2)}, 1),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_DEFECT_CASES))
def test_defect_and_mismatch_cases_change_exactly_the_expected_gold_rows(tmp_path, name):
    data, out = prepared(tmp_path)
    before = gold_rows(out)
    LOCAL[name](data)
    result = inc(data, out)
    after = gold_rows(out)
    changed, staged = EXPECTED_DEFECT_CASES[name]
    assert changed_keys(before, after) == changed
    assert after.keys() == before.keys()
    assert result.affected_gold_rows == staged
    assert_equals_full(data, out, tmp_path)


# ---------------------------------------------------------------- cross-file participant ownership
# Two files of one source carry the same participant: a record whose participant_id differs
# from its folder (heart rate, steps), or a sleep session of P002 stored under P001. The
# builders group duplicates per participant, not per file (sleep.py counts (pid, date) across
# files), so a change in one file alters rows owned by the other. Spec section 4 step 3: the
# whole source is re-parsed.

SOURCE_FILE = {"sleep": SLEEP, "steps": STEPS, "heart_rate": HR}
LATER_STAGES = [("light", 60), ("deep", 81), ("rem", 53), ("light", 90), ("awake", 19)]


def cross_file_setup(data: Path, source: str) -> None:
    if source == "sleep":
        write_events(data, "P001", SLEEP, [session("P001", DAY1), session("P002", DAY2)])
        write_events(data, "P002", SLEEP, [session("P002", DAY2)])
    elif source == "steps":
        write_events(data, "P001", STEPS, read_events(data, "P001", STEPS) + [
            {"participant_id": "P002", "timestamp": stamp(DAY1, 30), "steps": 11}])
        write_events(data, "P002", STEPS, [st(DAY1, 0, 7), st(DAY1, 30, 7)])
    else:
        write_events(data, "P001", HR, read_events(data, "P001", HR) + [
            {"participant_id": "P002", "timestamp": stamp(DAY1, 30), "heart_rate_bpm": 99}])
        write_events(data, "P002", HR, read_events(data, "P002", HR) + [hr(DAY1, 30, 81)])


def cross_file_change(data: Path, source: str, pid: str) -> None:
    records = read_events(data, pid, SOURCE_FILE[source])
    if source == "sleep":
        first = records[0]
        records[0] = session(first["participant_id"], date.fromisoformat(first["date"]),
                             stages=LATER_STAGES)
    elif source == "steps":
        records[0]["steps"] += 1
    else:
        records[0]["heart_rate_bpm"] += 1
    write_events(data, pid, SOURCE_FILE[source], records)


def cross_file_remove(data: Path, source: str, pid: str) -> None:
    events_path(data, pid, SOURCE_FILE[source]).unlink()


# case -> (changed gold keys, staged rows); keys are the same for the three sources unless the
# source is named in CROSS_OVERRIDES.
CROSS_CASES = ("change_p002", "change_p001", "remove_p002", "remove_p001")
CROSS_KEYS = {
    ("sleep", "change_p002"): ({("P002", DAY2)}, 1),
    ("sleep", "change_p001"): ({("P001", DAY1)}, 1),
    ("sleep", "remove_p002"): ({("P002", DAY2)}, 1),
    ("sleep", "remove_p001"): ({("P001", DAY1), ("P002", DAY2)}, 2),
    **{(source, "change_p002"): ({("P002", DAY1)}, 1) for source in ("steps", "heart_rate")},
    **{(source, "change_p001"): ({("P001", DAY1)}, 1) for source in ("steps", "heart_rate")},
    **{(source, "remove_p002"): ({("P002", DAY1)}, 1) for source in ("steps", "heart_rate")},
    **{(source, "remove_p001"): ({("P001", DAY1), ("P001", DAY2)}, 3)
       for source in ("steps", "heart_rate")},
}


def source_paths(source: str) -> list[str]:
    return [f"wearable_events/{pid}/{SOURCE_FILE[source]}" for pid in ("P001", "P002")]


@pytest.mark.parametrize("case", CROSS_CASES)
@pytest.mark.parametrize("source", sorted(SOURCE_FILE))
def test_participant_owned_by_two_files_reparses_the_whole_source(
        tmp_path, monkeypatch, source, case):
    data = baseline_data(tmp_path)
    cross_file_setup(data, source)
    out = tmp_path / "out"
    run(data, out)
    before = gold_rows(out)
    calls: list[str] = []
    real = incremental.read_event_file

    def spy(path, relative, kind):
        calls.append(relative)
        return real(path, relative, kind)

    monkeypatch.setattr(incremental, "read_event_file", spy)
    action, pid = case.split("_")[0], "P002" if case.endswith("p002") else "P001"
    (cross_file_change if action == "change" else cross_file_remove)(data, source, pid)
    result = inc(data, out)
    assert result.outcome == OUTCOME_INCREMENTAL
    # Every remaining file of the source is parsed, none of another source, never a removed file.
    expected_calls = [p for p in source_paths(source)
                      if action == "change" or not p.endswith(f"/{pid}/{SOURCE_FILE[source]}")]
    assert sorted(calls) == expected_calls
    assert_equals_full(data, out, tmp_path)
    changed, staged = CROSS_KEYS[(source, case)]
    after = gold_rows(out)
    assert changed_keys(before, after) == changed
    assert result.affected_gold_rows == staged


def test_sleep_duplicate_count_spans_the_two_files_and_follows_each_change(tmp_path):
    data = baseline_data(tmp_path)
    cross_file_setup(data, "sleep")
    out = tmp_path / "out"
    run(data, out)

    def duplicates() -> int | None:
        con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
        try:
            row = con.execute("SELECT failed_count FROM quality_results "
                              "WHERE rule_id = 'SLEEP_DUPLICATE_PARTICIPANT_DAY'").fetchone()
        finally:
            con.close()
        return row[0] if row else None

    assert duplicates() == 2  # the P002 DAY2 session in each file
    cross_file_change(data, "sleep", "P002")
    inc(data, out)
    assert duplicates() == 2  # a change confined to P002/sleep.json must not drop the pair
    cross_file_remove(data, "sleep", "P002")
    inc(data, out)
    assert duplicates() in (0, None)
    assert_equals_full(data, out, tmp_path)


def test_sleep_session_under_the_wrong_folder_is_flagged_as_duplicate_in_both_files(tmp_path):
    data = baseline_data(tmp_path)
    cross_file_setup(data, "sleep")
    out = tmp_path / "out"
    run(data, out)
    cross_file_change(data, "sleep", "P001")
    inc(data, out)
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        flagged = con.execute("SELECT count(*) FROM silver_sleep_sessions "
                              "WHERE participant_id = 'P002' AND is_duplicate_key").fetchone()[0]
    finally:
        con.close()
    assert flagged == 2
    assert_equals_full(data, out, tmp_path)


def test_an_unrelated_participant_file_is_not_reparsed_with_the_whole_source(
        tmp_path, monkeypatch):
    """Without shared participants a change stays confined to its own file."""
    data, out = prepared(tmp_path)  # P001 and P002 each own only their own heart-rate file
    calls: list[str] = []
    real = incremental.read_event_file

    def spy(path, relative, kind):
        calls.append(relative)
        return real(path, relative, kind)

    monkeypatch.setattr(incremental, "read_event_file", spy)
    cross_file_change(data, "heart_rate", "P002")
    inc(data, out)
    assert calls == ["wearable_events/P002/heart_rate.json"]


def corrected_steps(data: Path) -> None:
    records = read_events(data, "P001", STEPS)
    records[1]["steps"] = 25
    write_events(data, "P001", STEPS, records)


def corrected_sleep(data: Path) -> None:
    stages = [("light", 60), ("deep", 81), ("rem", 53), ("light", 90), ("awake", 19)]
    write_events(data, "P001", SLEEP, [session("P001", DAY1, stages=stages)])


@pytest.mark.parametrize("mutate", [m_corrected, corrected_steps, corrected_sleep],
                         ids=["heart_rate", "steps", "sleep"])
def test_one_corrected_record_changes_exactly_one_gold_row(tmp_path, mutate):
    data, out = prepared(tmp_path)
    before = gold_rows(out)
    mutate(data)
    inc(data, out)
    after = gold_rows(out)
    assert changed_keys(before, after) == {("P001", DAY1)}
    assert_equals_full(data, out, tmp_path)


def test_removed_file_gives_the_documented_values_for_a_day_without_records(tmp_path):
    data, out = prepared(tmp_path)
    assert gold_dict(out, "P001", DAY1)["steps_present"] is True
    m_steps_file_removed(data)
    inc(data, out)
    row = gold_dict(out, "P001", DAY1)
    assert row["total_steps"] is None
    assert row["steps_valid_minute_count"] == 0
    assert row["minutes_rest_to_moderate"] == 0 and row["minutes_burst"] == 0
    assert row["steps_present"] is False
    assert row["steps_quality_flags"] is None
    assert row["hr_present"] is True  # other sources keep their values
    assert row["survey_present"] is True


def test_removed_record_that_empties_a_day_clears_that_day(tmp_path):
    data, out = prepared(tmp_path)
    write_events(data, "P001", STEPS, [st(DAY1, 0, 10), st(DAY1, 1, 20), st(DAY1, 2, 30)])
    inc(data, out)
    row = gold_dict(out, "P001", DAY2)
    assert row["steps_present"] is False and row["total_steps"] is None
    assert row["steps_valid_minute_count"] == 0


def test_staged_gold_rows_stay_enrolled_participants_times_window(tmp_path):
    data, out = prepared(tmp_path)
    m_new_day(data)
    m_orphan_folder(data)
    inc(data, out)
    assert sorted(gold_rows(out)) == sorted((p, d) for p in ("P001", "P002") for d in WINDOW)


# ---------------------------------------------------------------- no-op

def test_noop_rerun_changes_no_byte_of_the_output(tmp_path):
    data, out = prepared(tmp_path)
    before_tree, before_state, before_id = tree_hashes(out), state(out), run_id_of(out)
    result = inc(data, out)
    assert result.outcome == OUTCOME_NOOP
    assert result.fallback_reason is None
    assert result.affected_gold_rows == 0
    assert (result.new_paths, result.changed_paths, result.removed_paths) == ((), (), ())
    assert result.run_id == before_id == run_id_of(out)
    assert state(out) == before_state
    assert tree_hashes(out) == before_tree


def test_noop_after_an_incremental_run(tmp_path):
    data, out = prepared(tmp_path)
    m_corrected(data)
    assert inc(data, out).outcome == OUTCOME_INCREMENTAL
    before = tree_hashes(out)
    assert inc(data, out).outcome == OUTCOME_NOOP
    assert inc(data, out).outcome == OUTCOME_NOOP
    assert tree_hashes(out) == before


def test_noop_does_not_refresh_derived_tables(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    fake = FakeDerived()
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [fake])
    inc(data, out)
    assert fake.calls == []


def test_noop_after_a_full_rerun_is_also_a_noop(tmp_path):
    data, out = prepared(tmp_path)
    run(data, out)
    assert inc(data, out).outcome == OUTCOME_NOOP


# ---------------------------------------------------------------- fallback

def test_no_output_directory_falls_back_to_a_full_rebuild(tmp_path):
    data = baseline_data(tmp_path)
    out = tmp_path / "never-run"
    result = inc(data, out)
    assert result.outcome == OUTCOME_FULL
    assert result.fallback_reason == "no_database"
    assert_equals_full(data, out, tmp_path)


def test_existing_directory_without_database_falls_back(tmp_path):
    data = baseline_data(tmp_path)
    out = tmp_path / "empty-out"
    out.mkdir()
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == (OUTCOME_FULL, "no_database")
    assert_equals_full(data, out, tmp_path)


def drop_table(name: str):
    def degrade(con):
        con.execute(f"DROP TABLE {name}")
    return degrade


def empty_manifest(con):
    con.execute("DELETE FROM raw_manifest")


def manifest_without_sha(con):
    con.execute("ALTER TABLE raw_manifest DROP COLUMN sha256")


def gold_with_22_columns(con):
    con.execute(f"CREATE TABLE gold_old AS SELECT {', '.join(GOLD_BASE)} "
                "FROM gold_clinical_daily")
    con.execute("DROP TABLE gold_clinical_daily")
    con.execute("ALTER TABLE gold_old RENAME TO gold_clinical_daily")


def silver_without_a_column(con):
    con.execute("ALTER TABLE silver_steps_minute DROP COLUMN activity_level")


def alter_type(table: str, column: str, new_type: str):
    def degrade(con):
        con.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {new_type}")
    return degrade


def swap_first_two_columns(table: str):
    """Same column names and types, different order."""
    def degrade(con):
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
            "ORDER BY ordinal_position", [table]).fetchall()]
        order = ", ".join([cols[1], cols[0], *cols[2:]])
        con.execute(f"CREATE TABLE {table}_reordered AS SELECT {order} FROM {table}")
        con.execute(f"DROP TABLE {table}")
        con.execute(f"ALTER TABLE {table}_reordered RENAME TO {table}")
    return degrade


DEGRADE = {
    "gold_column_type_changed": (alter_type("gold_clinical_daily", "total_steps", "INTEGER"),
                                 "old_schema"),
    "quality_results_type_changed": (alter_type("quality_results", "failed_count", "DOUBLE"),
                                     "old_schema"),
    "manifest_type_changed": (alter_type("raw_manifest", "size_bytes", "VARCHAR"),
                              "old_schema"),
    "side_table_type_changed": (alter_type("quality_file_counts", "checked_count", "INTEGER"),
                                "old_schema"),
    "gold_columns_reordered": (swap_first_two_columns("gold_clinical_daily"), "old_schema"),
    "manifest_columns_reordered": (swap_first_two_columns("raw_manifest"), "old_schema"),
    "silver_steps_columns_reordered": (swap_first_two_columns("silver_steps_minute"),
                                       "old_schema"),
    "owners_columns_reordered": (swap_first_two_columns("silver_file_owners"), "old_schema"),
    "missing_window_table": (drop_table("run_window"), "old_schema"),
    "no_manifest_table": (drop_table("raw_manifest"), "no_manifest"),
    "empty_manifest": (empty_manifest, "no_manifest"),
    "manifest_without_sha256": (manifest_without_sha, "old_schema"),
    "missing_heart_rate_silver": (drop_table("silver_heart_rate_minute"), "old_schema"),
    "gold_with_22_columns": (gold_with_22_columns, "old_schema"),
    "silver_missing_column": (silver_without_a_column, "old_schema"),
    "missing_file_state_table": (drop_table("quality_file_counts"), "old_schema"),
    "missing_owner_table": (drop_table("silver_file_owners"), "old_schema"),
    "missing_quality_rejects": (drop_table("quality_rejects"), "old_schema"),
}


@pytest.mark.parametrize("name", sorted(DEGRADE))
def test_missing_manifest_or_old_schema_falls_back_to_a_full_rebuild(tmp_path, name):
    data, out = prepared(tmp_path)
    degrade, reason = DEGRADE[name]
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        degrade(con)
    finally:
        con.close()
    m_corrected(data)
    result = inc(data, out)
    assert result.outcome == OUTCOME_FULL
    assert result.fallback_reason == reason
    assert_equals_full(data, out, tmp_path)


def test_database_that_is_not_a_database_is_a_pipeline_error_and_is_left_alone(tmp_path):
    data = baseline_data(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "wearable.duckdb").write_bytes(b"this is not a duckdb file")
    before = tree_hashes(out)
    with pytest.raises(PipelineError):
        inc(data, out)
    assert tree_hashes(out) == before


def test_side_tables_exist_after_full_and_incremental_runs(tmp_path):
    data, out = prepared(tmp_path)
    for _ in range(2):
        con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
        try:
            names = {r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables").fetchall()}
            counts_cols = [r[0] for r in con.execute("DESCRIBE quality_file_counts").fetchall()]
            owner_cols = [r[0] for r in con.execute("DESCRIBE silver_file_owners").fetchall()]
        finally:
            con.close()
        assert {"quality_file_counts", "silver_file_owners"} <= names
        assert counts_cols == ["relative_path", "source", "rule_id", "checked_count",
                               "failed_count"]
        assert owner_cols == ["relative_path", "silver_table", "participant_id"]
        m_corrected(data)
        inc(data, out)


# ---------------------------------------------------------------- failure

def test_malformed_changed_file_fails_and_leaves_the_output_untouched(tmp_path):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    events_path(data, "P001", HR).write_text("{not json", encoding="utf-8")
    with pytest.raises(PipelineError):
        inc(data, out)
    assert tree_hashes(out) == before


def test_non_array_changed_file_fails_and_leaves_the_output_untouched(tmp_path):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    events_path(data, "P001", STEPS).write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(PipelineError):
        inc(data, out)
    assert tree_hashes(out) == before


def test_failure_inside_the_transaction_rolls_back_everything(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    before = state(out)
    report_before = (out / "quality" / "report.md").read_bytes()
    quality_files = sorted(p.name for p in (out / "quality").iterdir())
    fake = FakeDerived(fail=True)
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [fake])
    m_corrected(data)
    with pytest.raises(InjectedFailure, match="injected failure"):
        inc(data, out)
    assert fake.calls, "refresh must run inside the transaction"
    assert state(out) == before
    assert (out / "quality" / "report.md").read_bytes() == report_before
    assert sorted(p.name for p in (out / "quality").iterdir()) == quality_files
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == ["wearable.duckdb"]
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [])
    inc(data, out)  # the output is usable and the same change now succeeds
    assert_equals_full(data, out, tmp_path)


def test_failure_during_a_shared_input_rebuild_rolls_back_everything(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    before = state(out)
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [FakeDerived(fail=True)])
    s_participant_added(data)
    with pytest.raises(InjectedFailure):
        inc(data, out)
    assert state(out) == before


def test_missing_required_input_fails_without_touching_the_output(tmp_path):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    (data / "participants" / "participants.csv").unlink()
    with pytest.raises(PipelineError):
        inc(data, out)
    assert tree_hashes(out) == before


def test_missing_data_directory_and_bad_study_days_are_pipeline_errors(tmp_path):
    data, out = prepared(tmp_path)
    with pytest.raises(PipelineError):
        inc(tmp_path / "absent", out)
    with pytest.raises(PipelineError):
        run_incremental(data, out, study_start=START, study_days=0)


# ---------------------------------------------------------------- extension point

def test_derived_table_receives_exactly_the_affected_participant_days(tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    fake = FakeDerived()
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [fake])
    m_corrected(data)
    inc(data, out)
    assert fake.calls == [{"P001": frozenset({DAY1})}]
    assert state(out)["tables"]["gold_fake"] == [(1,)]


def test_derived_table_receives_none_for_a_full_rebuild_by_fallback_or_shared_change(
        tmp_path, monkeypatch):
    data = baseline_data(tmp_path)
    fake = FakeDerived()
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [fake])
    out = tmp_path / "out"
    inc(data, out)  # no database: full
    s_survey_changed(data)
    inc(data, out)  # shared input: full
    assert fake.calls == [None, None]


def test_run_pipeline_refreshes_derived_tables_with_none(tmp_path, monkeypatch):
    data = baseline_data(tmp_path)
    fake = FakeDerived()
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [fake])
    run(data, tmp_path / "out")
    assert fake.calls == [None]
    assert state(tmp_path / "out")["tables"]["gold_fake"] == [(1,)]


def test_a_new_participant_file_with_no_gold_effect_passes_an_empty_mapping_or_skips(
        tmp_path, monkeypatch):
    data, out = prepared(tmp_path)
    fake = FakeDerived()
    monkeypatch.setattr(incremental, "DERIVED_TABLES", [fake])
    m_orphan_folder(data)
    inc(data, out)
    assert fake.calls in ([], [{}])


# ---------------------------------------------------------------- determinism and raw data

def test_independent_incremental_histories_give_identical_outputs(tmp_path):
    states = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        data, out = prepared(root)
        m_new_day(data)
        inc(data, out)
        m_defects(data)
        m_hr_file_removed(data)
        inc(data, out)
        states.append(state(out))
    assert states[0] == states[1]


def test_incremental_is_idempotent(tmp_path):
    data, out = prepared(tmp_path)
    m_corrected(data)
    inc(data, out)
    first = state(out)
    inc(data, out)
    assert state(out) == first


def test_raw_data_is_untouched_and_no_file_is_created_under_it(tmp_path):
    data, out = prepared(tmp_path)
    m_corrected(data)
    before = tree_hashes(data)
    inc(data, out)
    inc(data, out)
    assert tree_hashes(data) == before


# ---------------------------------------------------------------- CLI

LINE = re.compile(r"^run_id=([0-9a-f]{16}) database=(\S+) report=(\S+)( mode=(\w+))?$")


def cli_args(data: Path, out: Path, *extra: str) -> list[str]:
    return ["run", "--data-dir", str(data), "--output-dir", str(out), *extra]


def test_run_pipeline_and_run_incremental_keep_the_documented_signatures():
    assert list(inspect.signature(run_pipeline).parameters) == [
        "data_dir", "output_dir", "study_start", "study_days"]
    assert list(inspect.signature(run_incremental).parameters) == [
        "data_dir", "output_dir", "study_start", "study_days"]


def test_default_mode_is_full_and_its_stdout_line_is_unchanged(tmp_path, capsys):
    data = baseline_data(tmp_path)
    assert main(cli_args(data, tmp_path / "out")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    match = LINE.match(lines[0])
    assert match and match.group(4) is None


def test_explicit_full_mode_equals_the_default(tmp_path, capsys):
    data = baseline_data(tmp_path)
    assert main(cli_args(data, tmp_path / "a")) == 0
    assert main(cli_args(data, tmp_path / "b", "--mode", "full")) == 0
    first, second = capsys.readouterr().out.splitlines()
    assert LINE.match(first).group(1) == LINE.match(second).group(1)
    assert state(tmp_path / "a") == state(tmp_path / "b")


def test_default_mode_ignores_an_existing_database_like_before(tmp_path):
    data, out = prepared(tmp_path)
    m_corrected(data)
    assert main(cli_args(data, out)) == 0
    assert state(out) == state(fresh_full(data, tmp_path))


def test_unknown_mode_is_rejected_by_the_argument_parser(tmp_path):
    with pytest.raises(SystemExit) as raised:
        main(cli_args(baseline_data(tmp_path), tmp_path / "out", "--mode", "bogus"))
    assert raised.value.code == 2


def test_cli_incremental_without_a_database_says_it_fell_back(tmp_path, capsys):
    data = baseline_data(tmp_path)
    assert main(cli_args(data, tmp_path / "out", "--mode", "incremental")) == 0
    lines = capsys.readouterr().out.splitlines()
    assert LINE.match(lines[0]).group(5) == "full"
    assert lines[1] == "fallback: full rebuild (no_database)"
    assert len(lines) == 2


def test_cli_incremental_run_and_noop_report_their_mode(tmp_path, capsys):
    data, out = prepared(tmp_path)
    m_corrected(data)
    assert main(cli_args(data, out, "--mode", "incremental")) == 0
    first = capsys.readouterr().out.splitlines()
    assert len(first) == 1 and LINE.match(first[0]).group(5) == "incremental"
    assert state(out) == state(fresh_full(data, tmp_path))
    assert main(cli_args(data, out, "--mode", "incremental")) == 0
    second = capsys.readouterr().out.splitlines()
    assert len(second) == 1 and LINE.match(second[0]).group(5) == "noop"
    assert LINE.match(second[0]).group(1) == LINE.match(first[0]).group(1)


def test_cli_incremental_returns_one_and_names_the_problem_on_stderr(tmp_path, capsys):
    data, out = prepared(tmp_path)
    before = tree_hashes(out)
    events_path(data, "P001", HR).write_text("[1]", encoding="utf-8")
    assert main(cli_args(data, out, "--mode", "incremental")) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert captured.out == ""
    assert tree_hashes(out) == before


def test_cli_incremental_missing_data_directory_returns_one(tmp_path):
    assert main(cli_args(tmp_path / "absent", tmp_path / "out", "--mode", "incremental")) == 1


def test_existing_run_arguments_are_still_offered_and_mode_is_added(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["run", "--help"])
    assert raised.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--data-dir", "--output-dir", "--mode"):
        assert flag in text
