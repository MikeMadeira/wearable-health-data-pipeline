"""Behavior tests for INC-005 (see specs/INC-005.md). Fixtures are synthetic."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tests.test_walking_skeleton import (
    HR_HANDLING, HR_REASONS, SURVEY_COLUMNS, WINDOW, default_survey, gold, make_data, query,
    quality, run, survey_row, tree_hashes, write_csv,
)
from wearable_pipeline import PipelineError, main, run_pipeline
from wearable_pipeline.heart_rate import (
    HEART_RATE_RULES, HeartRateDay, HeartRateLayer, build_heart_rate_layer,
)

PATH = "wearable_events/P001/heart_rate.json"
PATH_2 = "wearable_events/P002/heart_rate.json"
DAY1, DAY2, DAY3 = WINDOW
T0 = datetime(2026, 1, 8, 0, 0, 0)
GOLD_HR = ["hr_mean_bpm", "hr_min_bpm", "hr_max_bpm", "hr_valid_minute_count", "hr_present",
           "hr_quality_flags"]
GOLD_BASE = [
    "participant_id", "local_date", "fatigue_score", "stress_score", "readiness_score",
    "sleep_quality_score", "survey_present", "survey_row_count", "survey_valid_score_count",
    "survey_quality_flags", "total_sleep_min", "deep_sleep_pct", "sleep_efficiency_pct",
    "sleep_session_count", "sleep_present", "sleep_quality_flags", "total_steps",
    "steps_valid_minute_count", "minutes_rest_to_moderate", "minutes_burst", "steps_present",
    "steps_quality_flags",
]
NO_HR = [None, None, None, 0, False, None]

CONFLICT = "HR_DUPLICATE_MINUTE_CONFLICT"
EXACT = "HR_DUPLICATE_MINUTE_EXACT"
ORPHAN = "HR_ORPHAN_PARTICIPANT"
MISMATCH = "HR_PARTICIPANT_FOLDER_MISMATCH"
STUCK = "HR_STUCK_SENSOR_RUN"
CONVERTED = "HR_TIMESTAMP_CONVERTED"
ALIGN = "HR_TIMESTAMP_NOT_MINUTE_ALIGNED"
OUT = "HR_TIMESTAMP_OUT_OF_WINDOW"
UNPARSEABLE = "HR_TIMESTAMP_UNPARSEABLE"
INVALID = "HR_VALUE_INVALID"
OOR = "HR_VALUE_OUT_OF_RANGE"

# Silver row tuple indexes (spec section 4).
RAW_TS, BPM, NORM, STUCK_COL, FLAGS = 3, 4, 5, 10, 12


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def rec(when, bpm, **extra) -> dict:
    """A record; ``when`` is minutes after 2026-01-08T00:00:00 or a timestamp string."""
    stamp = iso(T0 + timedelta(minutes=when)) if isinstance(when, int) else when
    return {"timestamp": stamp, "heart_rate_bpm": bpm, **extra}


def zulu(when: int, bpm, **extra) -> dict:
    """The record for local minute ``when`` written as a UTC ``Z`` timestamp (+5 h)."""
    return rec(iso(T0 + timedelta(minutes=when, hours=5)) + "Z", bpm, **extra)


def srow(when, bpm, norm, fl=None, *, dup=False, oor=False, stuck=False, conv=False,
         raw_ts=None, pid="P001", day=DAY1, orphan=False, oow=False) -> tuple:
    """Expected silver row; ``when`` is minutes after T0 or a datetime."""
    stamp = T0 + timedelta(minutes=when) if isinstance(when, int) else when
    return (pid, day, stamp, raw_ts, bpm, norm, orphan, oow, dup, oor, stuck, conv, fl)


def hd(mean, low, high, count, flags_=None) -> HeartRateDay:
    return HeartRateDay(hr_mean_bpm=mean, hr_min_bpm=low, hr_max_bpm=high,
                        hr_valid_minute_count=count, hr_quality_flags=flags_)


def layer(files: dict[str, list], enrolled=("P001", "P002")) -> HeartRateLayer:
    return build_heart_rate_layer(files, enrolled, WINDOW)


def one(records: list, enrolled=("P001", "P002")) -> HeartRateLayer:
    return layer({PATH: records}, enrolled)


def column(result: HeartRateLayer, index: int) -> list:
    return [row[index] for row in result.rows]


def counts(result: HeartRateLayer) -> dict[str, int]:
    return {rule: n for rule, n in result.failed.items() if n}


def gold_hr(result, pid: str, day: date) -> list:
    row = gold(result, pid, day)
    return [row[c] for c in GOLD_HR]


def write_hr(data: Path, pid: str, records: list) -> None:
    folder = data / "wearable_events" / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "heart_rate.json").write_text(json.dumps(records), encoding="utf-8")


def data_with(tmp_path: Path, p001: list | None = None, p002: list | None = None) -> Path:
    data = make_data(tmp_path)
    if p001 is not None:
        write_hr(data, "P001", p001)
    if p002 is not None:
        write_hr(data, "P002", p002)
    return data


def snapshot(result) -> dict[str, list[tuple]]:
    tables = [
        "raw_manifest", "silver_participants", "silver_wellness_survey", "silver_steps_minute",
        "silver_heart_rate_minute", "quality_results", "quality_rejects", "gold_clinical_daily",
    ]
    return {name: query(result, f"SELECT * FROM {name} ORDER BY ALL") for name in tables}


def worked_records() -> list:
    return [
        rec(0, 60), rec(1, 70), rec(1, 70), rec(2, 300), rec(3, 50), rec(3, 51),
        rec("2026-01-09T04:30:00Z", 80), rec(1440, 90),
    ]


WORKED_DAY1_FLAGS = f"{CONFLICT},{EXACT},{CONVERTED},{OOR}"


# --- HEART_RATE_RULES constant -------------------------------------------------------------


def test_heart_rate_rules_table_matches_spec():
    assert set(HEART_RATE_RULES) == set(HR_REASONS)
    assert len(HEART_RATE_RULES) == 11
    for rule_id, (source, handling, severity, reason) in HEART_RATE_RULES.items():
        assert source == "heart_rate"
        assert reason == HR_REASONS[rule_id]
        assert (handling, severity) == HR_HANDLING[rule_id]
    assert HEART_RATE_RULES[CONVERTED][1] == "convert"
    assert HEART_RATE_RULES[OOR][1:3] == ("withhold_field", "high")
    assert HEART_RATE_RULES[STUCK][1] == "withhold_field"


# --- worked example (spec section 10) ------------------------------------------------------


def test_worked_example_silver_rows_daily_values_and_counts():
    records = worked_records()
    result = one(records)
    assert isinstance(result, HeartRateLayer)
    assert result.checked == 8
    assert result.rows == [
        srow(0, 60, 60),
        srow(1, 70, 70, EXACT, dup=True),
        srow(1, 70, 70, EXACT, dup=True),
        srow(2, 300, None, OOR, oor=True),
        srow(3, 50, None, CONFLICT, dup=True),
        srow(3, 51, None, CONFLICT, dup=True),
        srow(datetime(2026, 1, 8, 23, 30), 80, 80, CONVERTED, conv=True,
             raw_ts="2026-01-09T04:30:00Z"),
        srow(1440, 90, 90, day=DAY2),
    ]
    assert result.daily == {
        ("P001", DAY1): hd(70.0, 60, 80, 3, WORKED_DAY1_FLAGS),
        ("P001", DAY2): hd(90.0, 90, 90, 1),
    }
    assert counts(result) == {CONFLICT: 2, EXACT: 2, OOR: 1, CONVERTED: 1}
    assert result.rejects == [
        (EXACT, PATH, 2, records[1]), (EXACT, PATH, 3, records[2]),
        (OOR, PATH, 4, records[3]),
        (CONFLICT, PATH, 5, records[4]), (CONFLICT, PATH, 6, records[5]),
    ]


def test_hand_computed_day_with_gap_out_of_range_values_and_a_stuck_run():
    records = (
        [rec(0, 60), rec(1, 70), rec(3, 80), rec(4, 30), rec(5, 200), rec(6, 40), rec(7, 201)]
        + [rec(i, 90) for i in range(10, 70)]
        + [rec(70, 100), rec(71, 50), rec(72, 100)]
    )
    result = one(records)
    assert result.checked == 70
    # valid: 60 70 80 200 40 100 50 100 -> 700 / 8; minute 2 missing (gap), 30 and 201 out of
    # range, minutes 10-69 stuck at 90.
    assert result.daily == {("P001", DAY1): hd(87.5, 40, 200, 8, f"{STUCK},{OOR}")}
    assert counts(result) == {OOR: 2, STUCK: 60}
    assert len(result.rejects) == 62
    assert len(result.rows) == 70


def test_heart_rate_day_is_frozen_and_failed_is_a_counter():
    result = one([rec(0, 60)])
    assert isinstance(result.failed, Counter)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.daily[("P001", DAY1)].hr_min_bpm = 1  # type: ignore[misc]


def test_empty_inputs_give_empty_layers():
    for files in ({}, {PATH: []}):
        result = layer(files)
        assert (result.rows, result.rejects, result.daily, result.checked) == ([], [], {}, 0)
        assert counts(result) == {}


def test_clean_data_reports_zero_for_every_rule():
    result = one([rec(0, 60), rec(1, 61), rec(2, 62)])
    assert counts(result) == {}
    assert column(result, FLAGS) == [None] * 3
    assert result.rejects == []
    assert result.daily == {("P001", DAY1): hd(61.0, 60, 62, 3)}


# --- value typing, range and raw preservation ---------------------------------------------


@pytest.mark.parametrize("value", [None, "72", 72.5, 72.0, True, False, 2**31, -(2**31) - 1,
                                   [], {}, [72]])
def test_invalid_value_has_null_raw_and_normalized_and_only_the_invalid_rule(value):
    record = rec(0, value)
    result = one([record])
    assert result.rows == [srow(0, None, None, INVALID)]
    assert result.rejects == [(INVALID, PATH, 1, record)]
    assert counts(result) == {INVALID: 1}
    assert result.daily == {("P001", DAY1): hd(None, None, None, 0, INVALID)}


def test_missing_value_key_is_invalid():
    record = {"timestamp": iso(T0)}
    result = one([record])
    assert result.rows == [srow(0, None, None, INVALID)]
    assert result.rejects == [(INVALID, PATH, 1, record)]


@pytest.mark.parametrize("value", [-10, 0, 1, 39, 201, 999, 2**31 - 1, -(2**31)])
def test_out_of_range_value_keeps_raw_withholds_normalized_and_is_rejected(value):
    record = rec(0, value)
    result = one([record])
    assert result.rows == [srow(0, value, None, OOR, oor=True)]
    assert result.rejects == [(OOR, PATH, 1, record)]
    assert counts(result) == {OOR: 1}
    assert result.daily == {("P001", DAY1): hd(None, None, None, 0, OOR)}


@pytest.mark.parametrize("value", [40, 41, 120, 199, 200])
def test_values_inside_the_inclusive_range_are_normalized(value):
    result = one([rec(0, value)])
    assert result.rows == [srow(0, value, value)]
    assert counts(result) == {}
    assert result.daily == {("P001", DAY1): hd(float(value), value, value, 1)}


def test_invalid_and_out_of_range_rules_never_fire_together():
    result = one([rec(0, "x"), rec(1, 5000), rec(2, 60)])
    assert column(result, FLAGS) == [INVALID, OOR, None]
    assert counts(result) == {INVALID: 1, OOR: 1}


def test_all_withheld_day_is_present_with_null_aggregates_and_zero_count():
    result = one([rec(1440, 300), rec(1441, 10), rec(1442, None)])
    assert result.daily == {("P001", DAY2): hd(None, None, None, 0, f"{INVALID},{OOR}")}


def test_non_object_element_is_treated_as_an_empty_object_and_never_raises():
    result = one([5, [], "x", None])
    assert len(result.rows) == 4 and result.checked == 4
    assert column(result, FLAGS) == [f"{UNPARSEABLE},{INVALID}"] * 4
    assert result.rows[0] == (
        "P001", None, None, None, None, None, False, False, False, False, False, False,
        f"{UNPARSEABLE},{INVALID}")
    assert result.rejects[:2] == [(UNPARSEABLE, PATH, 1, 5), (INVALID, PATH, 1, 5)]
    assert result.daily == {}


@pytest.mark.parametrize("given", [None, "", "   ", 5, 3.5, ["P001"]])
def test_blank_or_non_string_participant_id_falls_back_to_the_folder(given):
    result = one([rec(0, 60, participant_id=given)])
    assert result.rows == [srow(0, 60, 60)]
    assert result.daily == {("P001", DAY1): hd(60.0, 60, 60, 1)}


# --- stuck-sensor runs ---------------------------------------------------------------------


@pytest.mark.parametrize("length", [59, 60, 61])
def test_stuck_run_boundary_59_is_not_flagged_60_and_61_are(length):
    records = [rec(i, 90) for i in range(length)]
    result = one(records)
    flagged = length >= 60
    assert column(result, FLAGS) == [STUCK if flagged else None] * length
    assert column(result, STUCK_COL) == [flagged] * length
    assert column(result, NORM) == [None if flagged else 90] * length
    assert column(result, BPM) == [90] * length  # raw kept
    assert counts(result) == ({STUCK: length} if flagged else {})
    assert result.rejects == ([(STUCK, PATH, n, records[n - 1]) for n in range(1, length + 1)]
                              if flagged else [])
    assert result.daily == {("P001", DAY1): (
        hd(None, None, None, 0, STUCK) if flagged else hd(90.0, 90, 90, length))}


def test_stuck_run_flags_every_minute_from_the_first_identical_one():
    result = one([rec(0, 70)] + [rec(i, 90) for i in range(1, 61)] + [rec(61, 71)])
    flags = column(result, FLAGS)
    assert flags[0] is None and flags[-1] is None
    assert flags[1:-1] == [STUCK] * 60
    assert result.daily == {("P001", DAY1): hd(70.5, 70, 71, 2, STUCK)}


def _breakers() -> dict[str, list]:
    return {
        "gap": [],
        "out_of_range": [rec(40, 250)],
        "invalid": [rec(40, None)],
        "conflict": [rec(40, 90), rec(40, 91)],
        "different_value": [rec(40, 91)],
    }


@pytest.mark.parametrize("name", list(_breakers()))
def test_a_run_is_broken_by_a_gap_an_out_of_range_value_an_invalid_value_a_conflict_or_a_change(
        name):
    records = ([rec(i, 90) for i in range(40)] + _breakers()[name]
               + [rec(i, 90) for i in range(41, 81)])
    result = one(records)
    assert STUCK not in counts(result)
    assert not any(column(result, STUCK_COL))
    # both 40-minute halves keep their normalized value
    kept = [row[NORM] for row in result.rows if row[BPM] == 90 and row[2] != T0 + timedelta(minutes=40)]
    assert kept == [90] * 80
    expected_flags = {"gap": None, "out_of_range": OOR, "invalid": INVALID,
                      "conflict": CONFLICT, "different_value": None}[name]
    assert {f for f in column(result, FLAGS) if f} == ({expected_flags} if expected_flags else set())


def test_a_conflicting_duplicate_slot_breaks_the_run_and_only_that_minute_is_withheld():
    records = ([rec(i, 90) for i in range(40)] + [rec(40, 90), rec(40, 91)]
               + [rec(i, 90) for i in range(41, 81)])
    result = one(records)
    assert counts(result) == {CONFLICT: 2}
    assert result.daily == {("P001", DAY1): hd(90.0, 90, 90, 80, CONFLICT)}


def test_exact_duplicates_count_once_toward_the_run_length():
    short = one([rec(i, 90) for i in range(59)] + [rec(10, 90)])  # 59 slots, 60 records
    assert STUCK not in counts(short)
    assert short.daily == {("P001", DAY1): hd(90.0, 90, 90, 59, EXACT)}
    full = one([rec(i, 90) for i in range(60)] + [rec(10, 90)])  # 60 slots, 61 records
    assert counts(full) == {STUCK: 61, EXACT: 2}
    assert full.daily == {("P001", DAY1): hd(None, None, None, 0, f"{EXACT},{STUCK}")}


def test_two_adjacent_runs_of_different_values_are_not_merged():
    result = one([rec(i, 90) for i in range(30)] + [rec(i, 91) for i in range(30, 60)])
    assert counts(result) == {}
    assert result.daily == {("P001", DAY1): hd(90.5, 90, 91, 60)}


def test_a_run_may_span_midnight_and_is_excluded_on_each_local_day():
    records = [rec(i, 90) for i in range(1410, 1470)]  # 23:30 to 00:29, 30 minutes per day
    result = one(records)
    assert counts(result) == {STUCK: 60}
    assert column(result, 1) == [DAY1] * 30 + [DAY2] * 30
    assert result.daily == {
        ("P001", DAY1): hd(None, None, None, 0, STUCK),
        ("P001", DAY2): hd(None, None, None, 0, STUCK),
    }


def test_run_detection_is_independent_of_record_order():
    forward = [rec(i, 90) for i in range(60)]
    result = one(list(reversed(forward)))
    assert counts(result) == {STUCK: 60}
    assert result.rows[0][2] == T0 + timedelta(minutes=59)  # silver keeps file order


def test_out_of_order_records_are_not_a_defect():
    result = one([rec(2, 70), rec(0, 60), rec(1, 65)])
    assert counts(result) == {}
    assert result.daily == {("P001", DAY1): hd(65.0, 60, 70, 3)}


def test_runs_are_detected_per_participant_timeline():
    result = layer({PATH: [rec(i, 90) for i in range(30)],
                    PATH_2: [rec(i, 90) for i in range(30, 60)]})
    assert counts(result) == {}
    assert set(result.daily) == {("P001", DAY1), ("P002", DAY1)}


def test_run_continues_across_naive_and_converted_timestamps():
    records = [rec(i, 90) for i in range(30)] + [zulu(i, 90) for i in range(30, 60)]
    result = one(records)
    assert counts(result) == {STUCK: 60, CONVERTED: 30}
    assert result.daily == {("P001", DAY1): hd(None, None, None, 0, f"{STUCK},{CONVERTED}")}


def test_orphan_records_are_flagged_as_stuck_but_never_reach_gold():
    result = layer({"wearable_events/P999/heart_rate.json": [rec(i, 90) for i in range(60)]})
    assert column(result, FLAGS) == [f"{ORPHAN},{STUCK}"] * 60
    assert result.daily == {}


def test_a_run_crossing_the_window_edge_counts_records_outside_the_window():
    # 40 minutes before the window start plus 30 inside: 70 identical slots, flagged on both sides
    start = T0 - timedelta(minutes=40)
    records = [rec(iso(start + timedelta(minutes=i)), 90) for i in range(70)]
    result = one(records)
    assert counts(result) == {STUCK: 70, OUT: 40}
    assert result.daily == {("P001", DAY1): hd(None, None, None, 0, STUCK)}


# --- timestamps, Z conversion, informational rule -------------------------------------------


@pytest.mark.parametrize(("text", "local", "day"), [
    ("2026-01-08T05:00:00Z", datetime(2026, 1, 8, 0, 0), DAY1),
    ("2026-01-09T04:30:00Z", datetime(2026, 1, 8, 23, 30), DAY1),
    ("2026-01-08T00:30:00-05:00", datetime(2026, 1, 8, 0, 30), DAY1),
    ("2026-01-08T04:59:00Z", datetime(2026, 1, 7, 23, 59), date(2026, 1, 7)),
    ("2026-01-08T10:00:00+05:30", datetime(2026, 1, 7, 23, 30), date(2026, 1, 7)),
])
def test_explicit_z_or_offset_is_converted_to_local_and_the_raw_text_is_kept(text, local, day):
    result = one([rec(text, 70)])
    row = result.rows[0]
    assert row[2] == local and row[1] == day
    assert row[RAW_TS] == text and row[11] is True
    expected = CONVERTED if day == DAY1 else f"{CONVERTED},{OUT}"
    assert row[FLAGS] == expected
    assert row[NORM] == 70 and row[BPM] == 70


def test_naive_timestamp_is_local_and_has_no_raw_timestamp_text():
    row = one([rec(5, 70)]).rows[0]
    assert row[2] == T0 + timedelta(minutes=5) and row[RAW_TS] is None and row[11] is False


def test_converted_records_are_counted_without_rejects_and_flag_the_converted_day():
    result = one([rec("2026-01-09T04:30:00Z", 70), rec("2026-01-08T05:00:00Z", 71), rec(5, 72)])
    assert counts(result) == {CONVERTED: 2}
    assert result.rejects == []
    # 2026-01-09T04:30Z is 2026-01-08 23:30 local: the UTC date (01-09) gets no entry
    assert result.daily == {("P001", DAY1): hd(71.0, 70, 72, 3, CONVERTED)}


def test_converted_record_beyond_the_window_is_flagged_and_excluded_from_gold():
    result = one([rec("2026-01-11T07:00:00Z", 70)])
    assert result.rows[0][1] == date(2026, 1, 11)
    assert column(result, FLAGS) == [f"{CONVERTED},{OUT}"]
    assert result.rejects == [(OUT, PATH, 1, rec("2026-01-11T07:00:00Z", 70))]
    assert result.daily == {}


def test_a_converted_record_and_a_naive_record_in_the_same_slot_form_a_duplicate_group():
    exact = one([rec(0, 70), zulu(0, 70)])
    assert column(exact, FLAGS) == [EXACT, f"{EXACT},{CONVERTED}"]
    assert exact.daily == {("P001", DAY1): hd(70.0, 70, 70, 1, f"{EXACT},{CONVERTED}")}
    conflict = one([rec(0, 70), zulu(0, 71)])
    assert column(conflict, FLAGS) == [CONFLICT, f"{CONFLICT},{CONVERTED}"]
    assert column(conflict, NORM) == [None, None]
    assert conflict.daily == {("P001", DAY1): hd(None, None, None, 0, f"{CONFLICT},{CONVERTED}")}


# --- duplicates ----------------------------------------------------------------------------


def test_exact_duplicates_are_counted_once_in_the_aggregates():
    result = one([rec(0, 70), rec(0, 70), rec(0, 70), rec(1, 80)])
    assert counts(result) == {EXACT: 3}
    assert column(result, 8) == [True, True, True, False]
    assert result.daily == {("P001", DAY1): hd(75.0, 70, 80, 2, EXACT)}


def test_conflicting_duplicates_withhold_only_that_minute_without_picking_or_averaging():
    result = one([rec(0, 60), rec(0, 61), rec(1, 70)])
    assert column(result, BPM) == [60, 61, 70]  # raw kept
    assert column(result, NORM) == [None, None, 70]
    assert counts(result) == {CONFLICT: 2}
    assert result.daily == {("P001", DAY1): hd(70.0, 70, 70, 1, CONFLICT)}


def test_duplicate_conflict_with_an_invalid_member_withholds_the_slot():
    result = one([rec(0, 60), rec(0, None)])
    assert counts(result) == {CONFLICT: 2, INVALID: 1}
    assert result.daily == {("P001", DAY1): hd(None, None, None, 0, f"{CONFLICT},{INVALID}")}


def test_folder_mismatched_record_never_joins_a_duplicate_group():
    result = one([rec(0, 70), rec(0, 80, participant_id="P002")])
    assert column(result, FLAGS) == [None, MISMATCH]
    assert result.daily == {("P001", DAY1): hd(70.0, 70, 70, 1)}


# --- non-zero seconds ----------------------------------------------------------------------


def test_non_zero_seconds_are_kept_flagged_and_the_record_still_contributes():
    result = one([rec("2026-01-08T00:01:30", 70)])
    assert result.rows == [srow(datetime(2026, 1, 8, 0, 1, 30), 70, 70, ALIGN)]
    assert result.rejects == [(ALIGN, PATH, 1, rec("2026-01-08T00:01:30", 70))]
    assert result.daily == {("P001", DAY1): hd(70.0, 70, 70, 1, ALIGN)}


@pytest.mark.parametrize(("stamp", "aligned"), [
    ("2026-01-08T00:01:00", True), ("2026-01-08T00:01:00.000", True),
    ("2026-01-08T05:01:00Z", True), ("2026-01-08T00:01:00.5", False),
    ("2026-01-08T00:01:01", False),
])
def test_only_whole_minutes_are_aligned(stamp, aligned):
    result = one([rec(stamp, 70)])
    assert (ALIGN in counts(result)) is (not aligned)


def test_a_slot_collision_caused_by_seconds_is_resolved_by_the_duplicate_rule():
    exact = one([rec(1, 70), rec("2026-01-08T00:01:30", 70)])
    assert column(exact, FLAGS) == [EXACT, f"{EXACT},{ALIGN}"]
    assert exact.daily == {("P001", DAY1): hd(70.0, 70, 70, 1, f"{EXACT},{ALIGN}")}
    conflict = one([rec(1, 70), rec("2026-01-08T00:01:30", 71)])
    assert column(conflict, FLAGS) == [CONFLICT, f"{CONFLICT},{ALIGN}"]
    assert column(conflict, 2)[1] == datetime(2026, 1, 8, 0, 1, 30)  # not floored
    assert conflict.daily == {("P001", DAY1): hd(None, None, None, 0, f"{CONFLICT},{ALIGN}")}


# --- rules for defects not observed on the profiled data ----------------------------------


def test_unparseable_timestamp_keeps_the_row_flags_it_and_excludes_it_from_gold():
    record = rec("not a timestamp", 70)
    result = one([record, rec(0, 60)])
    assert result.rows[0] == ("P001", None, None, None, 70, 70, False, False, False, False,
                              False, False, UNPARSEABLE)
    assert result.rejects == [(UNPARSEABLE, PATH, 1, record)]
    assert result.daily == {("P001", DAY1): hd(60.0, 60, 60, 1)}


@pytest.mark.parametrize("stamp", [None, 5, "", "2026-01-08", "2026-01-08 00:00:00",
                                   "2026-01-08T00:00", "2026-13-08T00:00:00"])
def test_every_unparseable_timestamp_shape_is_flagged(stamp):
    result = one([{"timestamp": stamp, "heart_rate_bpm": 70}])
    assert counts(result) == {UNPARSEABLE: 1}


def test_out_of_window_record_is_flagged_kept_and_excluded_from_gold():
    after, before = rec("2026-01-11T00:00:00", 70), rec("2026-01-07T23:59:00", 71)
    result = one([after, before, rec(0, 60)])
    assert result.rows[0] == ("P001", date(2026, 1, 11), datetime(2026, 1, 11), None, 70, 70,
                              False, True, False, False, False, False, OUT)
    assert column(result, FLAGS) == [OUT, OUT, None]
    assert result.rejects == [(OUT, PATH, 1, after), (OUT, PATH, 2, before)]
    assert result.daily == {("P001", DAY1): hd(60.0, 60, 60, 1)}


def test_window_boundaries_are_inclusive():
    result = one([rec(0, 60), rec(2 * 1440 + 1439, 61)])
    assert counts(result) == {}
    assert set(result.daily) == {("P001", DAY1), ("P001", DAY3)}


def test_orphan_participant_stays_in_silver_flagged_and_never_reaches_gold():
    record = rec(0, 70)
    result = layer({"wearable_events/P999/heart_rate.json": [record]})
    assert result.rows == [srow(0, 70, 70, ORPHAN, pid="P999", orphan=True)]
    assert result.rejects == [(ORPHAN, "wearable_events/P999/heart_rate.json", 1, record)]
    assert result.daily == {}


def test_folder_mismatch_is_flagged_and_excluded_from_gold_for_both_ids():
    mismatched = rec(0, 80, participant_id="P002")
    orphaned = rec(1, 81, participant_id="P999")
    result = one([rec(0, 70), mismatched, orphaned])
    assert result.rows == [
        srow(0, 70, 70),
        srow(0, 80, 80, MISMATCH, pid="P002"),
        srow(1, 81, 81, f"{ORPHAN},{MISMATCH}", pid="P999", orphan=True),
    ]
    assert result.rejects == [
        (MISMATCH, PATH, 2, mismatched), (ORPHAN, PATH, 3, orphaned),
        (MISMATCH, PATH, 3, orphaned)]
    assert result.daily == {("P001", DAY1): hd(70.0, 70, 70, 1)}


# --- counts, determinism, ordering ---------------------------------------------------------


def test_checked_counts_records_across_files_and_each_record_counts_once_per_rule():
    result = layer({PATH: [rec(0, 300), rec(1, 301)], PATH_2: [rec(0, 60), rec(1, "x")]})
    assert result.checked == 4
    assert counts(result) == {OOR: 2, INVALID: 1}


def test_output_is_independent_of_mapping_order_and_rows_follow_sorted_paths():
    files = {PATH_2: [rec(0, 61), rec(1, 300)], PATH: [rec(0, 60)]}
    forward = layer(files)
    backward = layer(dict(reversed(list(files.items()))))
    assert forward == backward
    assert [row[0] for row in forward.rows] == ["P001", "P002", "P002"]
    assert forward.rejects == [(OOR, PATH_2, 2, files[PATH_2][1])]


def test_layer_does_not_modify_its_input():
    records = worked_records()
    before = json.dumps(records)
    one(records)
    assert json.dumps(records) == before


# --- pipeline: silver and gold --------------------------------------------------------------


def test_participant_without_a_heart_rate_file_has_no_silver_rows_and_default_gold(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute") == [(0,)]
    for pid in ("P001", "P002"):
        for day in WINDOW:
            assert gold_hr(result, pid, day) == NO_HR
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(2 * len(WINDOW),)]


def test_gold_columns_end_with_the_six_heart_rate_columns_in_order(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    cols = [r[0] for r in query(result, "SELECT column_name FROM information_schema.columns "
                                        "WHERE table_name = 'gold_clinical_daily' "
                                        "ORDER BY ordinal_position")]
    assert cols[:22] == GOLD_BASE
    assert cols[22:] == GOLD_HR
    assert len(cols) == 28


def test_silver_heart_rate_schema_and_grain(tmp_path):
    records = worked_records() + [rec("bad", 1)]
    result = run(data_with(tmp_path, records), tmp_path / "out")
    cols = query(result, "SELECT column_name, data_type FROM information_schema.columns "
                         "WHERE table_name = 'silver_heart_rate_minute' ORDER BY ordinal_position")
    assert cols == [
        ("participant_id", "VARCHAR"), ("local_date", "DATE"), ("minute_ts", "TIMESTAMP"),
        ("raw_timestamp", "VARCHAR"), ("heart_rate_bpm", "INTEGER"),
        ("heart_rate_bpm_normalized", "INTEGER"), ("is_orphan", "BOOLEAN"),
        ("is_out_of_window", "BOOLEAN"), ("is_duplicate_minute", "BOOLEAN"),
        ("is_out_of_range", "BOOLEAN"), ("is_stuck_sensor", "BOOLEAN"),
        ("is_timestamp_converted", "BOOLEAN"), ("quality_flags", "VARCHAR")]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute") == [(9,)]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute "
                         "WHERE heart_rate_bpm IS NULL") == [(0,)]
    assert query(result, "SELECT heart_rate_bpm, heart_rate_bpm_normalized, is_out_of_range "
                         "FROM silver_heart_rate_minute WHERE heart_rate_bpm = 300") == [
        (300, None, True)]
    assert query(result, "SELECT * FROM silver_heart_rate_minute "
                         "WHERE is_timestamp_converted AND raw_timestamp IS NOT NULL") == [
        ("P001", DAY1, datetime(2026, 1, 8, 23, 30), "2026-01-09T04:30:00Z", 80, 80, False,
         False, False, False, False, True, CONVERTED)]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute "
                         "WHERE raw_timestamp IS NOT NULL") == [(1,)]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute "
                         "WHERE minute_ts IS NULL AND local_date IS NULL") == [(1,)]
    assert query(result, "SELECT count(*) FROM (SELECT participant_id, minute_ts FROM "
                         "silver_heart_rate_minute WHERE minute_ts IS NOT NULL "
                         "AND NOT is_duplicate_minute GROUP BY ALL HAVING count(*) > 1)") == [(0,)]


def test_pipeline_gold_matches_the_worked_example_and_counts_are_reported(tmp_path):
    records = worked_records()
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert gold_hr(result, "P001", DAY1) == [70.0, 60, 80, 3, True, WORKED_DAY1_FLAGS]
    assert gold_hr(result, "P001", DAY2) == [90.0, 90, 90, 1, True, None]
    assert gold_hr(result, "P001", DAY3) == NO_HR
    assert gold_hr(result, "P002", DAY1) == NO_HR
    results = quality(result)
    expected = {CONFLICT: 2, EXACT: 2, OOR: 1, CONVERTED: 1}
    for rule in HR_REASONS:
        assert results[(rule, "heart_rate")][-3:-1] == (8, expected.get(rule, 0))
        assert results[(rule, "heart_rate")][:2] == HR_HANDLING[rule]
    assert query(result, "SELECT count(*) FROM quality_results") == [(41,)]
    rejects = query(result, "SELECT rule_id, row_ref, reason, raw_values FROM quality_rejects "
                            "WHERE source = 'heart_rate' ORDER BY row_ref")
    assert [(r[0], r[1]) for r in rejects] == [
        (EXACT, f"{PATH}:2"), (EXACT, f"{PATH}:3"), (OOR, f"{PATH}:4"),
        (CONFLICT, f"{PATH}:5"), (CONFLICT, f"{PATH}:6")]
    assert [r[2] for r in rejects] == [HR_REASONS[r[0]] for r in rejects]
    assert json.loads(rejects[2][3]) == records[3]
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         f"WHERE rule_id = '{CONVERTED}'") == [(0,)]
    text = result.report_path.read_text(encoding="utf-8")
    assert "| HR_TIMESTAMP_CONVERTED | heart_rate | convert | low | 8 | 1 |" in text
    assert "| HR_DUPLICATE_MINUTE_CONFLICT | heart_rate | withhold_field | high | 8 | 2 |" in text


def test_existing_gold_columns_and_values_are_unchanged_by_heart_rate_data(tmp_path):
    cols = ", ".join(GOLD_BASE)
    sql = f"SELECT {cols} FROM gold_clinical_daily ORDER BY participant_id, local_date"
    without = run(make_data(tmp_path / "a"), tmp_path / "out_a")
    with_hr = run(data_with(tmp_path / "b", worked_records(), [rec(0, 61)]), tmp_path / "out_b")
    assert query(with_hr, sql) == query(without, sql)
    assert query(with_hr, "SELECT count(*) FROM gold_clinical_daily") == [(2 * len(WINDOW),)]


def test_orphan_heart_rate_file_stays_in_silver_and_adds_no_gold_rows(tmp_path):
    data = data_with(tmp_path)
    write_hr(data, "P999", [rec(0, 70), rec(1, 71)])
    result = run(data, tmp_path / "out")
    assert query(result, "SELECT participant_id, is_orphan, quality_flags "
                         "FROM silver_heart_rate_minute ORDER BY minute_ts") == [
        ("P999", True, ORPHAN)] * 2
    assert quality(result)[(ORPHAN, "heart_rate")][-3:-1] == (2, 2)
    assert query(result, "SELECT count(*) FROM gold_clinical_daily "
                         "WHERE participant_id = 'P999'") == [(0,)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(2 * len(WINDOW),)]


def test_out_of_window_and_unparseable_records_are_absent_from_gold(tmp_path):
    records = [rec(0, 60), rec("2026-01-12T00:00:00", 99), rec("junk", 98), rec(1, 62)]
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert gold_hr(result, "P001", DAY1) == [61.0, 60, 62, 2, True, None]
    assert quality(result)[(OUT, "heart_rate")][-3:-1] == (4, 1)
    assert quality(result)[(UNPARSEABLE, "heart_rate")][-3:-1] == (4, 1)
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute") == [(4,)]


def test_day_with_only_withheld_records_is_present_with_null_aggregates(tmp_path):
    result = run(data_with(tmp_path, [rec(1440, 300), rec(1441, 10)]), tmp_path / "out")
    assert gold_hr(result, "P001", DAY2) == [None, None, None, 0, True, OOR]
    assert gold_hr(result, "P001", DAY1) == NO_HR


def test_gaps_are_visible_as_a_lower_valid_minute_count(tmp_path):
    result = run(data_with(tmp_path, [rec(0, 60), rec(1, 62), rec(3, 64)]), tmp_path / "out")
    assert gold_hr(result, "P001", DAY1) == [62.0, 60, 64, 3, True, None]


def test_stuck_run_spanning_midnight_is_excluded_on_both_days_in_the_pipeline(tmp_path):
    records = [rec(i, 90) for i in range(1410, 1470)]
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert gold_hr(result, "P001", DAY1) == [None, None, None, 0, True, STUCK]
    assert gold_hr(result, "P001", DAY2) == [None, None, None, 0, True, STUCK]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute "
                         "WHERE is_stuck_sensor AND heart_rate_bpm = 90 "
                         "AND heart_rate_bpm_normalized IS NULL") == [(60,)]
    assert quality(result)[(STUCK, "heart_rate")][-3:-1] == (60, 60)
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         f"WHERE rule_id = '{STUCK}'") == [(60,)]


def test_heart_rate_checked_count_does_not_change_other_sources(tmp_path):
    result = run(data_with(tmp_path, worked_records()), tmp_path / "out")
    results = quality(result)
    assert results[("STEPS_NEGATIVE", "steps")][-3:-1] == (1, 0)
    assert results[("SURVEY_SCORE_INVALID", "wellness_survey")][-3:-1] == (5, 0)
    assert results[(OOR, "heart_rate")][-3:-1] == (8, 1)


# --- table evolution -----------------------------------------------------------------------

OLD_10 = ("participant_id VARCHAR, local_date DATE, fatigue_score INTEGER, stress_score INTEGER, "
          "readiness_score INTEGER, sleep_quality_score INTEGER, survey_present BOOLEAN, "
          "survey_row_count INTEGER, survey_valid_score_count INTEGER, "
          "survey_quality_flags VARCHAR")
OLD_16 = (OLD_10 + ", total_sleep_min INTEGER, deep_sleep_pct DOUBLE, "
          "sleep_efficiency_pct DOUBLE, sleep_session_count INTEGER, sleep_present BOOLEAN, "
          "sleep_quality_flags VARCHAR")
OLD_22 = (OLD_16 + ", total_steps BIGINT, steps_valid_minute_count INTEGER, "
          "minutes_rest_to_moderate INTEGER, minutes_burst INTEGER, steps_present BOOLEAN, "
          "steps_quality_flags VARCHAR")
ROW_10 = "('P001', DATE '2026-01-08', 3, 3, 8, 3, true, 1, 4, NULL"
ROW_16 = ROW_10 + ", NULL, NULL, NULL, 0, false, NULL"
ROW_22 = ROW_16 + ", 99, 9, 4, 5, true, 'STALE'"


@pytest.mark.parametrize(("ddl", "values"), [
    (OLD_10, ROW_10 + ")"),
    (OLD_16, ROW_16 + ")"),
    (OLD_22, ROW_22 + ")"),
])
def test_database_from_before_heart_rate_gets_the_missing_columns_added(tmp_path, ddl, values):
    data = data_with(tmp_path, [rec(0, 60)])
    out = tmp_path / "out"
    first = run(data, out)
    connection = duckdb.connect(str(first.database_path))
    try:
        connection.execute("DROP TABLE gold_clinical_daily")
        connection.execute(f"CREATE TABLE gold_clinical_daily ({ddl}, "
                           "PRIMARY KEY (participant_id, local_date))")
        connection.execute(f"INSERT INTO gold_clinical_daily VALUES {values}")
        connection.execute("DROP TABLE IF EXISTS silver_heart_rate_minute")
    finally:
        connection.close()
    second = run(data, out)
    row = gold(second, "P001", DAY1)
    assert len(row) == 28
    assert [row[c] for c in GOLD_HR] == [60.0, 60, 60, 1, True, None]
    assert [row[c] for c in GOLD_BASE[16:]] == [0, 1, 1, 0, True, None]
    assert row["fatigue_score"] == 3
    types = dict(query(second, "SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_name = 'gold_clinical_daily'"))
    assert (types["hr_mean_bpm"], types["hr_min_bpm"], types["hr_max_bpm"]) == (
        "DOUBLE", "INTEGER", "INTEGER")
    assert (types["hr_valid_minute_count"], types["hr_present"], types["hr_quality_flags"]) == (
        "INTEGER", "BOOLEAN", "VARCHAR")
    assert query(second, "SELECT count(*) FROM gold_clinical_daily") == [(2 * len(WINDOW),)]
    assert query(second, "SELECT count(*) FROM silver_heart_rate_minute") == [(1,)]
    assert gold_hr(second, "P002", DAY1) == NO_HR


def test_old_gold_rows_outside_the_window_are_retained_with_null_heart_rate(tmp_path):
    data = data_with(tmp_path, [rec(0, 60)])
    out = tmp_path / "out"
    first = run(data, out)
    connection = duckdb.connect(str(first.database_path))
    try:
        connection.execute("INSERT INTO gold_clinical_daily (participant_id, local_date) "
                           "VALUES ('P777', DATE '2020-01-01')")
    finally:
        connection.close()
    second = run(data, out)
    assert gold_hr(second, "P777", date(2020, 1, 1)) == [None] * 6
    assert query(second, "SELECT count(*) FROM gold_clinical_daily") == [(2 * len(WINDOW) + 1,)]


# --- idempotency, determinism, raw immutability ---------------------------------------------


def test_rerun_on_unchanged_inputs_is_identical_including_the_report(tmp_path):
    data = data_with(tmp_path, worked_records(), [rec(0, 62)])
    first = run(data, tmp_path / "out")
    before = snapshot(first)
    assert before["silver_heart_rate_minute"]
    report = first.report_path.read_bytes()
    second = run(data, tmp_path / "out")
    assert snapshot(second) == before
    assert second.report_path.read_bytes() == report
    assert second.run_id == first.run_id


def test_two_fresh_output_directories_give_identical_tables_and_report(tmp_path):
    data = data_with(tmp_path, worked_records(), [rec(0, 62)])
    first = run(data, tmp_path / "out_1")
    second = run(data, tmp_path / "out_2")
    assert snapshot(first) == snapshot(second)
    assert first.report_path.read_bytes() == second.report_path.read_bytes()


def test_changing_one_record_changes_exactly_that_days_heart_rate_columns(tmp_path):
    data = data_with(tmp_path, [rec(0, 60), rec(1440, 70), rec(1441, 80)], [rec(0, 65)])
    first = run(data, tmp_path / "out")
    assert gold_hr(first, "P001", DAY2) == [75.0, 70, 80, 2, True, None]
    before = query(first, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    write_hr(data, "P001", [rec(0, 60), rec(1440, 70), rec(1441, 90)])
    second = run(data, tmp_path / "out")
    after = query(second, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    assert [(a[0], a[1]) for a, b in zip(after, before) if a != b] == [("P001", DAY2)]
    assert gold_hr(second, "P001", DAY2) == [80.0, 70, 90, 2, True, None]


def test_changing_a_survey_row_does_not_change_heart_rate_columns(tmp_path):
    data = data_with(tmp_path, worked_records())
    first = run(data, tmp_path / "out")
    sql = ("SELECT participant_id, local_date, " + ", ".join(GOLD_HR)
           + " FROM gold_clinical_daily ORDER BY ALL")
    before = query(first, sql)
    survey = default_survey()
    survey[0] = survey_row("P001", DAY1.isoformat(), ["4", "4", "9", "4"])
    write_csv(data / "health_summaries" / "wellness_survey.csv", SURVEY_COLUMNS, survey)
    second = run(data, tmp_path / "out")
    assert query(second, sql) == before
    assert gold(second, "P001", DAY1)["fatigue_score"] == 4


def test_run_id_is_derived_from_the_manifest_including_heart_rate_files(tmp_path):
    data = data_with(tmp_path, worked_records())
    result = run(data, tmp_path / "out")
    manifest = query(result, "SELECT relative_path, source, sha256 FROM raw_manifest "
                             "ORDER BY relative_path")
    assert (PATH, "heart_rate") in [(m[0], m[1]) for m in manifest]
    text = "".join(f"{m[0]}\t{m[2]}\n" for m in manifest)
    assert result.run_id == hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    assert run(data, tmp_path / "other").run_id == result.run_id
    write_hr(data, "P001", [rec(0, 61)])
    assert run(data, tmp_path / "third").run_id != result.run_id


def test_raw_data_is_untouched_and_nothing_is_created_under_the_data_dir(tmp_path):
    data = data_with(tmp_path, worked_records())
    before = tree_hashes(data)
    run(data, tmp_path / "out")
    run(data, tmp_path / "out")
    assert tree_hashes(data) == before


# --- failure contract -----------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    b"{bad", b"", b"\xff\xfe[]", b'{"a": 1}', b'"text"', b"5", b"null",
    b'[{"timestamp": "2026-01-08T00:00:00", "heart_rate_bpm": 60}, 5]', b"[[]]", b'["x"]',
])
def test_malformed_heart_rate_file_raises_before_any_table_is_touched(tmp_path, content):
    data = data_with(tmp_path, [rec(0, 60)])
    out = tmp_path / "out"
    first = run(data, out)
    before = snapshot(first)
    (data / "wearable_events" / "P001" / "heart_rate.json").write_bytes(content)
    with pytest.raises(PipelineError):
        run(data, out)
    assert snapshot(first) == before


def test_malformed_heart_rate_file_on_a_fresh_output_dir_creates_no_database(tmp_path):
    data = data_with(tmp_path)
    (data / "wearable_events" / "P001" / "heart_rate.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(PipelineError):
        run(data, tmp_path / "out")
    assert not (tmp_path / "out" / "wearable.duckdb").exists()


def test_cli_exits_one_on_malformed_heart_rate_json_and_zero_on_valid(tmp_path):
    data = data_with(tmp_path)
    out = tmp_path / "out"
    args = ["run", "--data-dir", str(data), "--output-dir", str(out)]
    (data / "wearable_events" / "P001" / "heart_rate.json").write_text("{bad", encoding="utf-8")
    assert main(args) == 1
    write_hr(data, "P001", [rec(0, 60)])
    assert main(args) == 0


def test_in_object_defects_never_raise_in_the_pipeline(tmp_path):
    weird = [{}, {"timestamp": [], "heart_rate_bpm": {}}, {"participant_id": 5, "heart_rate_bpm": None}]
    result = run(data_with(tmp_path, weird), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute") == [(3,)]
    assert quality(result)[(INVALID, "heart_rate")][-3:-1] == (3, 3)
    assert quality(result)[(UNPARSEABLE, "heart_rate")][-3:-1] == (3, 3)


# --- scale ----------------------------------------------------------------------------------


def test_sixty_thousand_records_are_bulk_loaded_in_reasonable_time(tmp_path):
    records = [zulu(i, 60 + i % 7) if i % 10 == 0 else rec(i, 60 + i % 7) for i in range(60_000)]
    data = data_with(tmp_path, records)
    started = time.monotonic()
    result = run_pipeline(data, tmp_path / "out", study_start=DAY1, study_days=60)
    elapsed = time.monotonic() - started
    assert elapsed < 25, f"pipeline took {elapsed:.1f}s for 60,000 records"
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute") == [(60_000,)]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute "
                         "WHERE is_timestamp_converted") == [(6_000,)]
    assert query(result, "SELECT count(*) FROM silver_heart_rate_minute "
                         "WHERE heart_rate_bpm_normalized IS NULL") == [(0,)]
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         "WHERE source = 'heart_rate'") == [(0,)]
    assert quality(result)[(CONVERTED, "heart_rate")][-3:-1] == (60_000, 6_000)
    values = [60 + i % 7 for i in range(1440)]
    row = gold(result, "P001", DAY1)
    assert row["hr_valid_minute_count"] == 1440 and row["hr_present"] is True
    assert row["hr_min_bpm"] == 60 and row["hr_max_bpm"] == 66
    assert row["hr_mean_bpm"] == pytest.approx(sum(values) / 1440)
    assert row["hr_quality_flags"] == CONVERTED
