"""Behavior tests for INC-012, the device calibration date and its two rules
(see specs/INC-012.md). Fixtures are synthetic; no participant data is copied.

Companion amendments of INC-011 tests live in ``tests/test_devices.py``,
``tests/test_walking_skeleton.py``, ``tests/test_sleep.py``, ``tests/test_heart_rate.py`` and
``tests/test_readme_contract.py``.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from tests.test_devices import (
    D1, D2, META_COLUMNS, devices_data, gold_rows, label, mrow, prow, sql,
)
from tests.test_incremental import state
from tests.test_walking_skeleton import DAYS, START, query, run, write_csv
from wearable_pipeline import incremental, pipeline
from wearable_pipeline.compare import compare_outputs
from wearable_pipeline.devices import (
    CALIBRATION_INVALID, CALIBRATION_STALE, CALIBRATION_STALE_DAYS, DEVICE_COLUMNS, DEVICE_RULES,
    METADATA_PATH, SILVER_COLUMNS, SILVER_TABLE, TABLE_NAME, build_device_layer,
    classify_calibration, parse_calibration_date,
)
from wearable_pipeline.incremental import run_incremental
from wearable_pipeline.pipeline import run_pipeline

LATER_START = date(2026, 2, 1)  # a non-default study_start


def dev(n: int) -> str:
    return f"FBT-{n:04d}-AAAAAAAA"


def crow(n: int, calibration: str) -> dict[str, str]:
    """``device_metadata.csv`` row of participant ``P00n`` with the given raw calibration text."""
    row = mrow(f"P{n:03d}", dev(n))
    row["calibration_date"] = calibration
    return row


def layer_for(calibrations: list[str], study_start: date = START):
    participants = [prow(f"P{i:03d}", dev(i)) for i in range(1, len(calibrations) + 1)]
    metadata = [crow(i, c) for i, c in enumerate(calibrations, start=1)]
    return participants, metadata, build_device_layer(participants, metadata, study_start)


def silver_row(n: int, raw: str, parsed: date | None, flag: str | None = None) -> tuple:
    return (f"P{n:03d}", dev(n), "2.2.0", label("2.1.8", dev(n)), "2.1.8", "2.2.0", "metadata",
            flag, raw, parsed)


def failed(layer) -> dict[str, int]:
    return {rule: layer.failed[rule] for rule in (CALIBRATION_STALE, CALIBRATION_INVALID)}


# --------------------------------------------------------------------------- constants


def test_calibration_rules_are_registered_participant_level_flags():
    assert CALIBRATION_STALE == "DEVICE_CALIBRATION_STALE"
    assert CALIBRATION_INVALID == "DEVICE_CALIBRATION_INVALID"
    assert CALIBRATION_STALE_DAYS == 30
    assert DEVICE_RULES[CALIBRATION_STALE][:3] == ("device_metadata", "flag", "medium")
    assert DEVICE_RULES[CALIBRATION_INVALID][:3] == ("device_metadata", "flag", "medium")
    assert pipeline.RULES[CALIBRATION_STALE] == DEVICE_RULES[CALIBRATION_STALE]
    assert pipeline.RULES[CALIBRATION_INVALID] == DEVICE_RULES[CALIBRATION_INVALID]
    assert len(pipeline.RULES) == 48


def test_gold_and_silver_declare_the_calibration_columns_last():
    assert DEVICE_COLUMNS == (
        ("participant_id", "VARCHAR"), ("device_id", "VARCHAR"), ("firmware_version", "VARCHAR"),
        ("calibration_date", "DATE"))
    assert SILVER_COLUMNS[-2:] == (("calibration_date_raw", "VARCHAR"), ("calibration_date", "DATE"))
    assert [n for n, _ in SILVER_COLUMNS[:8]] == [
        "participant_id", "device_id", "firmware_version_raw", "device_label",
        "firmware_version_parsed", "firmware_version", "firmware_source", "quality_flags"]


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize("raw, expected", [
    ("2025-12-04", date(2025, 12, 4)),
    ("2026-01-07", date(2026, 1, 7)),
    ("2024-02-29", date(2024, 2, 29)),
    (" 2025-12-24 ", date(2025, 12, 24)),
    ("\t2025-12-24\n", date(2025, 12, 24)),
    ("", None),
    ("   ", None),
    (None, None),
    ("not a date", None),
    ("2025/12/24", None),
    ("24-12-2025", None),
    ("12/24/2025", None),
    ("2025-1-4", None),
    ("20251224", None),
    ("2025-12-24T10:00:00", None),
    ("2025-12-24 10:00", None),
    ("2025-12-24Z", None),
    ("2025-W52-3", None),
    ("2025-13-01", None),
    ("2025-02-29", None),
    ("2025-02-30", None),
    ("2025-00-10", None),
    ("0000-01-01", None),
    ("٢٠٢٥-١٢-٢٤", None),  # non-ASCII digits are not ISO 8601
])
def test_parse_calibration_date(raw, expected):
    assert parse_calibration_date(raw) == expected


# --------------------------------------------------------------------------- classification


@pytest.mark.parametrize("calibration, expected", [
    ("2025-12-10", None),                # 29 days before 2026-01-08
    ("2025-12-09", None),                # exactly 30 days: not stale
    ("2025-12-08", CALIBRATION_STALE),   # 31 days: stale
    ("2025-12-04", CALIBRATION_STALE),   # the earliest observed date (35 days)
    ("2026-01-07", None),                # the latest observed date (1 day)
    ("2026-01-08", None),                # on study_start: valid
    ("2026-01-09", None),                # after study_start: valid
    ("2030-01-01", None),                # far after study_start: still valid, not flagged
    (" 2025-12-08 ", CALIBRATION_STALE),
    ("", CALIBRATION_INVALID),
    ("  ", CALIBRATION_INVALID),
    (None, CALIBRATION_INVALID),
    ("2025-12-08T00:00:00", CALIBRATION_INVALID),
    ("garbage", CALIBRATION_INVALID),
])
def test_classify_calibration_against_the_default_study_start(calibration, expected):
    assert classify_calibration(calibration, START) == expected


@pytest.mark.parametrize("calibration, expected", [
    ("2026-01-02", None),                # 30 days before 2026-02-01
    ("2026-01-01", CALIBRATION_STALE),   # 31 days
    ("2026-01-03", None),                # 29 days
    ("2025-12-24", CALIBRATION_STALE),   # valid under the default start, stale under this one
    ("2026-02-01", None),
    ("2026-02-02", None),
])
def test_classify_calibration_follows_the_given_study_start(calibration, expected):
    assert classify_calibration(calibration, LATER_START) == expected


def test_the_reference_date_is_the_argument_not_a_hard_coded_date():
    assert classify_calibration("2025-12-08", date(2025, 12, 20)) is None
    assert classify_calibration("2025-12-08", date(2026, 1, 8)) == CALIBRATION_STALE
    assert classify_calibration("2025-12-08", date(2026, 1, 7)) is None  # exactly 30 days


# --------------------------------------------------------------------------- build_device_layer

CASES = [
    "2025-12-10",           # P001  29 days
    "2025-12-09",           # P002  30 days
    "2025-12-08",           # P003  31 days -> stale
    "2026-01-08",           # P004  on study_start
    "2026-01-20",           # P005  after study_start
    "",                     # P006  blank -> invalid
    "08/12/2025",           # P007  unparseable -> invalid
    " 2025-12-07 ",         # P008  padded, 32 days -> stale, raw text kept
    "2025-02-30",           # P009  impossible date -> invalid
    "2025-12-08T10:00:00",  # P010  timestamp -> invalid
]
PARSED = [date(2025, 12, 10), date(2025, 12, 9), date(2025, 12, 8), date(2026, 1, 8),
          date(2026, 1, 20), None, None, date(2025, 12, 7), None, None]


def test_gold_shows_the_calibration_date_next_to_the_device_and_keeps_every_participant():
    _, _, layer = layer_for(CASES)
    assert layer.gold == [(f"P{i:03d}", dev(i), "2.2.0", PARSED[i - 1]) for i in range(1, 11)]
    assert layer.checked == 10
    assert len({row[0] for row in layer.gold}) == len(layer.gold) == 10  # grain: one per participant


def test_silver_keeps_the_raw_text_and_the_parsed_date_without_touching_quality_flags():
    _, _, layer = layer_for(CASES)
    assert layer.silver == [silver_row(i, CASES[i - 1], PARSED[i - 1]) for i in range(1, 11)]
    assert all(row[7] is None for row in layer.silver)  # quality_flags is firmware only


def test_stale_and_invalid_are_counted_per_participant_and_exclusive():
    _, _, layer = layer_for(CASES)
    assert failed(layer) == {CALIBRATION_STALE: 2, CALIBRATION_INVALID: 4}


def test_rejects_name_the_metadata_row_and_carry_the_raw_values():
    _, metadata, layer = layer_for(CASES)
    rejects = sorted(r for r in layer.rejects if r[0] in (CALIBRATION_STALE, CALIBRATION_INVALID))
    assert rejects == sorted([
        (CALIBRATION_STALE, METADATA_PATH, 3, metadata[2]),
        (CALIBRATION_STALE, METADATA_PATH, 8, metadata[7]),
        (CALIBRATION_INVALID, METADATA_PATH, 6, metadata[5]),
        (CALIBRATION_INVALID, METADATA_PATH, 7, metadata[6]),
        (CALIBRATION_INVALID, METADATA_PATH, 9, metadata[8]),
        (CALIBRATION_INVALID, METADATA_PATH, 10, metadata[9]),
    ], key=lambda r: r[:3])
    assert [r[3]["calibration_date"] for r in rejects if r[0] == CALIBRATION_STALE] == [
        "2025-12-08", " 2025-12-07 "]


def test_the_30_day_boundary_in_the_layer():
    _, _, layer = layer_for(["2025-12-10", "2025-12-09", "2025-12-08"])
    assert failed(layer) == {CALIBRATION_STALE: 1, CALIBRATION_INVALID: 0}
    assert [r[2] for r in layer.rejects if r[0] == CALIBRATION_STALE] == [3]


def test_a_non_default_study_start_moves_the_boundary():
    calibrations = ["2025-12-24", "2026-01-01", "2026-01-02", "2026-02-01"]
    _, _, default = layer_for(calibrations, START)
    _, _, later = layer_for(calibrations, LATER_START)
    assert failed(default) == {CALIBRATION_STALE: 0, CALIBRATION_INVALID: 0}
    assert failed(later) == {CALIBRATION_STALE: 2, CALIBRATION_INVALID: 0}
    assert [r[2] for r in later.rejects if r[0] == CALIBRATION_STALE] == [1, 2]
    assert later.gold == default.gold  # the shown date does not depend on study_start


def test_a_participant_without_a_metadata_row_has_a_null_date_and_is_invalid():
    participants = [prow("P001", D1), prow("P002", D2)]
    layer = build_device_layer(participants, [mrow("P001", D1)], START)
    assert layer.gold == [("P001", D1, "2.2.0", date(2025, 12, 24)), ("P002", D2, None, None)]
    assert failed(layer) == {CALIBRATION_STALE: 0, CALIBRATION_INVALID: 1}
    assert [(r[0], r[1], r[2]) for r in layer.rejects if r[0] == CALIBRATION_INVALID] == [
        (CALIBRATION_INVALID, "participants/participants.csv", 2)]


def test_a_metadata_file_without_the_calibration_column_reads_as_blank():
    layer = build_device_layer([prow("P001", D1)], [{"participant_id": "P001", "device_id": D1}],
                               START)
    assert layer.gold == [("P001", D1, None, None)]
    assert layer.silver[0][-2:] == ("", None)
    assert failed(layer) == {CALIBRATION_STALE: 0, CALIBRATION_INVALID: 1}


def test_the_date_comes_from_the_chosen_metadata_row():
    metadata = [crow(1, "2025-11-01"), {**crow(1, "2026-01-02"), "device_id": dev(1)}]
    metadata[0]["device_id"] = "FBT-0001-ZZZZZZZZ"  # not the participant's device: not chosen
    layer = build_device_layer([prow("P001", dev(1))], metadata, START)
    assert layer.gold == [("P001", dev(1), "2.2.0", date(2026, 1, 2))]
    assert failed(layer) == {CALIBRATION_STALE: 0, CALIBRATION_INVALID: 0}
    assert len(layer.silver) == 2


def test_calibration_findings_do_not_remove_or_default_anything():
    _, _, layer = layer_for(CASES)
    assert [row[1] for row in layer.gold] == [dev(i) for i in range(1, 11)]
    assert [row[2] for row in layer.gold] == ["2.2.0"] * 10


def test_layer_is_deterministic_and_independent_of_input_order():
    participants, metadata, forward = layer_for(CASES)
    backward = build_device_layer(participants[::-1], metadata[::-1], START)
    assert backward.gold == forward.gold
    assert failed(backward) == failed(forward)
    assert build_device_layer(participants, metadata, START).gold == forward.gold


# --------------------------------------------------------------------------- pipeline fixtures


def calibration_data(root: Path, calibrations: list[str]) -> Path:
    participants = [list(prow(f"P{i:03d}", dev(i)).values()) for i in range(1, len(calibrations) + 1)]
    metadata = [crow(i, c) for i, c in enumerate(calibrations, start=1)]
    return devices_data(root, participants, metadata)


BOUNDARY = ["2025-12-10", "2025-12-09", "2025-12-08", "2026-01-08", "", "08/12/2025"]
BOUNDARY_GOLD = [
    ("P001", dev(1), "2.2.0", date(2025, 12, 10)), ("P002", dev(2), "2.2.0", date(2025, 12, 9)),
    ("P003", dev(3), "2.2.0", date(2025, 12, 8)), ("P004", dev(4), "2.2.0", date(2026, 1, 8)),
    ("P005", dev(5), "2.2.0", None), ("P006", dev(6), "2.2.0", None)]


def rule_counts(result) -> dict[str, tuple[int, int]]:
    rows = query(result, "SELECT rule_id, checked_count, failed_count FROM quality_results "
                         f"WHERE rule_id IN ('{CALIBRATION_STALE}', '{CALIBRATION_INVALID}')")
    return {r[0]: (r[1], r[2]) for r in rows}


def test_full_run_writes_the_calibration_date_next_to_the_device(tmp_path):
    result = run(calibration_data(tmp_path, BOUNDARY), tmp_path / "out")
    assert query(result, f"SELECT * FROM {TABLE_NAME} ORDER BY participant_id") == BOUNDARY_GOLD
    assert [(r[0], r[1]) for r in query(result, f"DESCRIBE {TABLE_NAME}")] == list(DEVICE_COLUMNS)
    assert query(result, f"SELECT count(DISTINCT participant_id), count(*) FROM {TABLE_NAME}") \
        == [(6, 6)]


def test_silver_stores_the_raw_text_and_the_parsed_date(tmp_path):
    result = run(calibration_data(tmp_path, BOUNDARY), tmp_path / "out")
    assert [(r[0], r[1]) for r in query(result, f"DESCRIBE {SILVER_TABLE}")] == list(SILVER_COLUMNS)
    assert query(result, "SELECT participant_id, calibration_date_raw, calibration_date "
                         f"FROM {SILVER_TABLE} ORDER BY participant_id") == [
        ("P001", "2025-12-10", date(2025, 12, 10)), ("P002", "2025-12-09", date(2025, 12, 9)),
        ("P003", "2025-12-08", date(2025, 12, 8)), ("P004", "2026-01-08", date(2026, 1, 8)),
        ("P005", "", None), ("P006", "08/12/2025", None)]


def test_quality_results_report_stale_and_invalid_without_failing_the_run(tmp_path):
    result = run(calibration_data(tmp_path, BOUNDARY), tmp_path / "out")
    assert rule_counts(result) == {CALIBRATION_STALE: (6, 1), CALIBRATION_INVALID: (6, 2)}
    rows = query(result, "SELECT rule_id, source, handling, severity, reason, run_id "
                         "FROM quality_results WHERE rule_id LIKE 'DEVICE_CALIBRATION_%'")
    assert {r[0]: r[1:] for r in rows} == {
        rule: (*DEVICE_RULES[rule], result.run_id)
        for rule in (CALIBRATION_STALE, CALIBRATION_INVALID)}
    text = result.report_path.read_text(encoding="utf-8")
    assert "| DEVICE_CALIBRATION_STALE | device_metadata | flag | medium | 6 | 1 |" in text
    assert "| DEVICE_CALIBRATION_INVALID | device_metadata | flag | medium | 6 | 2 |" in text


def test_rejects_preserve_the_raw_calibration_text(tmp_path):
    result = run(calibration_data(tmp_path, BOUNDARY), tmp_path / "out")
    rows = query(result, "SELECT rule_id, row_ref, source, raw_values FROM quality_rejects "
                         "WHERE rule_id LIKE 'DEVICE_CALIBRATION_%' ORDER BY rule_id, row_ref")
    assert [(r[0], r[1], r[2], json.loads(r[3])["calibration_date"]) for r in rows] == [
        (CALIBRATION_INVALID, f"{METADATA_PATH}:5", "device_metadata", ""),
        (CALIBRATION_INVALID, f"{METADATA_PATH}:6", "device_metadata", "08/12/2025"),
        (CALIBRATION_STALE, f"{METADATA_PATH}:3", "device_metadata", "2025-12-08")]


def test_a_non_default_study_start_changes_the_findings_not_the_dates(tmp_path):
    data = calibration_data(tmp_path, ["2025-12-24", "2026-01-01", "2026-01-02", "2026-02-01"])
    default = run_pipeline(data, tmp_path / "default", study_start=START, study_days=DAYS)
    later = run_pipeline(data, tmp_path / "later", study_start=LATER_START, study_days=DAYS)
    assert rule_counts(default) == {CALIBRATION_STALE: (4, 0), CALIBRATION_INVALID: (4, 0)}
    assert rule_counts(later) == {CALIBRATION_STALE: (4, 2), CALIBRATION_INVALID: (4, 0)}
    assert query(default, f"SELECT calibration_date FROM {TABLE_NAME} ORDER BY participant_id") \
        == query(later, f"SELECT calibration_date FROM {TABLE_NAME} ORDER BY participant_id") \
        == [(date(2025, 12, 24),), (date(2026, 1, 1),), (date(2026, 1, 2),), (date(2026, 2, 1),)]


def test_rerun_with_a_changed_study_start_replaces_the_findings_in_the_same_directory(tmp_path):
    data = calibration_data(tmp_path, ["2025-12-24", "2026-01-01", "2026-01-02", "2026-02-01"])
    out = tmp_path / "out"
    run_pipeline(data, out, study_start=START, study_days=DAYS)
    result = run_pipeline(data, out, study_start=LATER_START, study_days=DAYS)
    assert rule_counts(result) == {CALIBRATION_STALE: (4, 2), CALIBRATION_INVALID: (4, 0)}
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         f"WHERE rule_id = '{CALIBRATION_STALE}'") == [(2,)]
    fresh = tmp_path / "fresh"
    run_pipeline(data, fresh, study_start=LATER_START, study_days=DAYS)
    assert state(out) == state(fresh)


def test_identical_rerun_changes_nothing_and_duplicates_no_rows(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    first = run(data, out)
    before = state(out)
    second = run(data, out)
    assert second.run_id == first.run_id
    assert state(out) == before
    assert gold_rows(out) == BOUNDARY_GOLD
    assert query(second, "SELECT count(*) FROM quality_rejects "
                         "WHERE rule_id LIKE 'DEVICE_CALIBRATION_%'") == [(3,)]


def test_changed_calibration_date_updates_the_row_and_the_findings_in_place(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    run(data, out)
    write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
              [list(crow(i, c).values()) for i, c in enumerate(
                  ["2025-12-10", "2025-12-09", "2026-01-07", "2026-01-08", "2025-12-01", ""],
                  start=1)])
    result = run(data, out)
    assert gold_rows(out)[2] == ("P003", dev(3), "2.2.0", date(2026, 1, 7))
    assert gold_rows(out)[4] == ("P005", dev(5), "2.2.0", date(2025, 12, 1))
    assert rule_counts(result) == {CALIBRATION_STALE: (6, 1), CALIBRATION_INVALID: (6, 1)}


def test_two_fresh_runs_are_identical_under_compare(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    run(data, tmp_path / "a")
    run(data, tmp_path / "b")
    assert compare_outputs(tmp_path / "a", tmp_path / "b").identical


def test_compare_detects_a_changed_calibration_date(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    run(data, tmp_path / "a")
    run(data, tmp_path / "b")
    sql(tmp_path / "b", f"UPDATE {TABLE_NAME} SET calibration_date = DATE '2025-01-01' "
                        "WHERE participant_id = 'P001'")
    result = compare_outputs(tmp_path / "a", tmp_path / "b")
    assert not result.identical
    assert [(d.table, d.status, d.left_only_rows, d.right_only_rows)
            for d in result.table_diffs] == [(TABLE_NAME, "rows", 1, 1)]


def test_compare_detects_a_changed_silver_raw_calibration_text(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    run(data, tmp_path / "a")
    run(data, tmp_path / "b")
    sql(tmp_path / "b", f"UPDATE {SILVER_TABLE} SET calibration_date_raw = '2025-12-10 ' "
                        "WHERE participant_id = 'P001'")
    result = compare_outputs(tmp_path / "a", tmp_path / "b")
    assert [(d.table, d.status) for d in result.table_diffs] == [(SILVER_TABLE, "rows")]


# --------------------------------------------------------------------------- incremental


def inc_with(data: Path, out: Path, study_start: date = START):
    return run_incremental(data, out, study_start=study_start, study_days=DAYS)


def reference(data: Path, tmp_path: Path, study_start: date = START) -> Path:
    out = tmp_path / "reference"
    shutil.rmtree(out, ignore_errors=True)
    run_pipeline(data, out, study_start=study_start, study_days=DAYS)
    return out


def test_noop_incremental_leaves_the_calibration_outputs_unchanged(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    run(data, out)
    before = state(out)
    result = inc_with(data, out)
    assert result.outcome == "noop"
    assert state(out) == before
    assert gold_rows(out) == BOUNDARY_GOLD


def test_changed_metadata_rebuilds_and_equals_a_full_run(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    run(data, out)
    before = state(out)
    write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
              [list(crow(i, c).values()) for i, c in enumerate(
                  ["2025-11-01", "2025-12-09", "2025-12-08", "2026-01-08", "2026-01-09", "x"],
                  start=1)])
    result = inc_with(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "shared_input_changed")
    assert state(out) == state(reference(data, tmp_path))
    assert state(out) != before
    assert rule_counts(result) == {CALIBRATION_STALE: (6, 2), CALIBRATION_INVALID: (6, 1)}


def test_a_changed_study_start_triggers_a_rebuild_and_equals_a_full_run(tmp_path):
    data = calibration_data(tmp_path, ["2025-12-24", "2026-01-01", "2026-01-02", "2026-02-01"])
    out = tmp_path / "out"
    first = run(data, out)
    assert rule_counts(first) == {CALIBRATION_STALE: (4, 0), CALIBRATION_INVALID: (4, 0)}
    result = inc_with(data, out, LATER_START)
    assert (result.outcome, result.fallback_reason) == ("full", incremental.REASON_WINDOW_CHANGED)
    assert rule_counts(result) == {CALIBRATION_STALE: (4, 2), CALIBRATION_INVALID: (4, 0)}
    assert state(out) == state(reference(data, tmp_path, LATER_START))


def test_incremental_into_an_empty_directory_builds_the_calibration_outputs(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    result = inc_with(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "no_database")
    assert gold_rows(out) == BOUNDARY_GOLD
    assert state(out) == state(reference(data, tmp_path))


def test_an_output_with_the_old_three_column_gold_table_is_rebuilt_in_full(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    run(data, out)
    sql(out, f"ALTER TABLE {TABLE_NAME} DROP COLUMN calibration_date")
    result = inc_with(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "old_schema")
    assert gold_rows(out) == BOUNDARY_GOLD
    assert state(out) == state(reference(data, tmp_path))


def test_an_output_with_the_old_silver_table_is_rebuilt_in_full(tmp_path):
    data = calibration_data(tmp_path, BOUNDARY)
    out = tmp_path / "out"
    run(data, out)
    sql(out, f"ALTER TABLE {SILVER_TABLE} DROP COLUMN calibration_date_raw")
    result = inc_with(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "old_schema")
    assert state(out) == state(reference(data, tmp_path))
