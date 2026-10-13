"""Behavior tests for INC-004 (see specs/INC-004.md). Fixtures are synthetic."""

from __future__ import annotations

import copy
import dataclasses
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tests.test_walking_skeleton import (
    STEPS_HANDLING, STEPS_REASONS, SURVEY_COLUMNS, WINDOW, default_survey, gold, make_data,
    query, quality, run, snapshot, survey_row, tree_hashes, write_csv,
)
from wearable_pipeline import PipelineError, main, run_pipeline
from wearable_pipeline.steps import STEPS_RULES, StepsDay, StepsLayer, build_steps_layer

PATH = "wearable_events/P001/steps.json"
PATH_2 = "wearable_events/P002/steps.json"
DAY1, DAY2 = WINDOW[0], WINDOW[1]
T0 = datetime(2026, 1, 8, 0, 0, 0)
GOLD_STEPS = ["total_steps", "steps_valid_minute_count", "minutes_rest_to_moderate",
              "minutes_burst", "steps_present", "steps_quality_flags"]

LOW = "at_rest_to_moderate"
HIGH = "burst"
HOURLY = "steps_per_hour"

CONFLICT = "STEPS_DUPLICATE_MINUTE_CONFLICT"
EXACT = "STEPS_DUPLICATE_MINUTE_EXACT"
NEGATIVE = "STEPS_NEGATIVE"
CONVERTED = "STEPS_UNIT_CONVERTED"
UNSUPPORTED = "STEPS_UNIT_UNSUPPORTED"
INVALID = "STEPS_VALUE_INVALID"
ALIGN = "STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED"
UNPARSEABLE = "STEPS_TIMESTAMP_UNPARSEABLE"
OUT = "STEPS_TIMESTAMP_OUT_OF_WINDOW"
ORPHAN = "STEPS_ORPHAN_PARTICIPANT"
MISMATCH = "STEPS_PARTICIPANT_FOLDER_MISMATCH"

# Silver row tuple indexes (spec section 4).
RAW, SPM, LEVEL, UNIT_COL, FLAGS = 3, 4, 5, 6, 10


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def rec(when, steps, **extra) -> dict:
    """A record; ``when`` is minutes after 2026-01-08T00:00:00 or a timestamp string."""
    stamp = iso(T0 + timedelta(minutes=when)) if isinstance(when, int) else when
    return {"timestamp": stamp, "steps": steps, **extra}


def hourly(when, steps, **extra) -> dict:
    return rec(when, steps, _unit=HOURLY, **extra)


def srow(when, steps, spm, level, unit=None, fl=None, *, dup=False, pid="P001", day=DAY1,
         orphan=False, oow=False) -> tuple:
    """Expected silver row; ``when`` is minutes after T0 or a datetime."""
    stamp = T0 + timedelta(minutes=when) if isinstance(when, int) else when
    return (pid, day, stamp, steps, spm, level, unit, orphan, oow, dup, fl)


def layer(files: dict[str, list], enrolled=("P001", "P002")) -> StepsLayer:
    return build_steps_layer(files, enrolled, WINDOW)


def one(records: list, enrolled=("P001", "P002")) -> StepsLayer:
    return layer({PATH: records}, enrolled)


def flags(result: StepsLayer) -> list:
    return [row[FLAGS] for row in result.rows]


def levels(result: StepsLayer) -> list:
    return [row[LEVEL] for row in result.rows]


def spms(result: StepsLayer) -> list:
    return [row[SPM] for row in result.rows]


def raws(result: StepsLayer) -> list:
    return [row[RAW] for row in result.rows]


def sd(total, valid, flags_=None, rest=0, burst=0) -> StepsDay:
    return StepsDay(total_steps=total, steps_valid_minute_count=valid,
                    minutes_rest_to_moderate=rest, minutes_burst=burst,
                    steps_quality_flags=flags_)


def gold_steps(result, pid: str, day: date) -> list:
    row = gold(result, pid, day)
    return [row[c] for c in GOLD_STEPS]


def counts(result: StepsLayer) -> dict[str, int]:
    return {rule: n for rule, n in result.failed.items() if n}


def write_steps(data: Path, pid: str, records: list) -> None:
    folder = data / "wearable_events" / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "steps.json").write_text(json.dumps(records), encoding="utf-8")


def data_with(tmp_path: Path, p001: list | None = None, p002: list | None = None) -> Path:
    data = make_data(tmp_path)
    if p001 is not None:
        write_steps(data, "P001", p001)
    if p002 is not None:
        write_steps(data, "P002", p002)
    return data


def worked_records() -> list:
    return [
        rec(0, 0), rec(1, 5), rec(2, 31), rec(3, -1),
        hourly(4, 3600), hourly(5, 100), hourly(6, 300), rec(7, 4),
        rec(8, 6), rec(8, 6), rec(9, 2), rec(9, 3),
    ]


def p005_records() -> list:
    """Three hourly records, each sharing its minute with a unit-less record of the same
    raw number at non-zero seconds (the P005 pattern), plus one clean minute."""
    records: list = []
    for k, raw in enumerate([240, 180, 120]):
        records += [hourly(k, raw), rec(f"2026-01-08T00:0{k}:30", raw)]
    records.append(rec(5, 10))
    return records


# --- STEPS_RULES constant ---------------------------------------------------------------


def test_steps_rules_table_matches_spec():
    assert set(STEPS_RULES) == set(STEPS_REASONS)
    assert len(STEPS_RULES) == 11
    assert "STEPS_BURST" not in STEPS_RULES
    assert "STEPS_UNIT_FIELD_PRESENT" not in STEPS_RULES
    for rule_id, (source, handling, severity, reason) in STEPS_RULES.items():
        assert source == "steps"
        assert reason == STEPS_REASONS[rule_id]
        assert (handling, severity) == STEPS_HANDLING[rule_id]
    assert STEPS_RULES[CONVERTED][1] == "convert"
    assert STEPS_RULES[UNSUPPORTED][1] == "withhold_field"


# --- worked example (spec section 10) ----------------------------------------------------


def test_worked_example_silver_rows_daily_total_and_counts():
    records = worked_records()
    result = one(records)
    assert isinstance(result, StepsLayer)
    assert result.checked == 12
    assert result.rows == [
        srow(0, 0, 0, LOW),
        srow(1, 5, 5, LOW),
        srow(2, 31, 31, HIGH),
        srow(3, -1, None, None, fl=NEGATIVE),
        srow(4, 3600, 60, HIGH, HOURLY, CONVERTED),
        srow(5, 100, None, None, HOURLY, UNSUPPORTED),
        srow(6, 300, 5, LOW, HOURLY, CONVERTED),
        srow(7, 4, 4, LOW),
        srow(8, 6, 6, LOW, fl=EXACT, dup=True),
        srow(8, 6, 6, LOW, fl=EXACT, dup=True),
        srow(9, 2, None, None, fl=CONFLICT, dup=True),
        srow(9, 3, None, None, fl=CONFLICT, dup=True),
    ]
    assert result.daily == {("P001", DAY1): sd(
        111, 7, f"{CONFLICT},{EXACT},{NEGATIVE},{CONVERTED},{UNSUPPORTED}", rest=5, burst=2)}
    assert counts(result) == {NEGATIVE: 1, CONVERTED: 2, UNSUPPORTED: 1, EXACT: 2, CONFLICT: 2}
    assert result.rejects == [
        (NEGATIVE, PATH, 4, records[3]), (UNSUPPORTED, PATH, 6, records[5]),
        (EXACT, PATH, 9, records[8]), (EXACT, PATH, 10, records[9]),
        (CONFLICT, PATH, 11, records[10]), (CONFLICT, PATH, 12, records[11]),
    ]


def test_steps_day_is_frozen_and_failed_is_a_counter():
    result = one([rec(0, 1)])
    assert isinstance(result.failed, Counter)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.daily[("P001", DAY1)].total_steps = 5  # type: ignore[misc]


def test_empty_inputs_give_empty_layers():
    for files in ({}, {PATH: []}):
        result = layer(files)
        assert (result.rows, result.rejects, result.daily, result.checked) == ([], [], {}, 0)
        assert counts(result) == {}


def test_input_is_not_mutated():
    records = worked_records()
    before = copy.deepcopy(records)
    one(records)
    assert records == before


# --- clean day, gaps, zero and the activity-level boundary --------------------------------


def test_gap_minutes_are_not_zero_and_not_counted():
    result = one([rec(0, 3), rec(10, 4)])
    assert result.daily == {("P001", DAY1): sd(7, 2, None, rest=2)}


@pytest.mark.parametrize(("value", "level"), [
    (0, LOW), (1, LOW), (29, LOW), (30, LOW),
    (31, HIGH), (32, HIGH), (500, HIGH), (1_000_000, HIGH), (2**31 - 1, HIGH),
])
def test_activity_level_boundaries_per_record(value, level):
    result = one([rec(0, value)])
    assert result.rows == [srow(0, value, value, level)]
    assert counts(result) == {} and result.rejects == []
    rest, burst = (1, 0) if level == LOW else (0, 1)
    assert result.daily == {("P001", DAY1): sd(value, 1, None, rest=rest, burst=burst)}


def test_boundary_values_in_one_day_are_bucketed_and_counts_sum_to_the_valid_minutes():
    result = one([rec(0, 0), rec(1, 1), rec(2, 29), rec(3, 30), rec(4, 31), rec(5, 32)])
    assert levels(result) == [LOW, LOW, LOW, LOW, HIGH, HIGH]
    day = result.daily[("P001", DAY1)]
    assert day == sd(123, 6, None, rest=4, burst=2)
    assert day.minutes_rest_to_moderate + day.minutes_burst == day.steps_valid_minute_count


@pytest.mark.parametrize(("raw", "normalized", "level"), [
    (0, 0, LOW), (60, 1, LOW), (1740, 29, LOW), (1800, 30, LOW),
    (1860, 31, HIGH), (1920, 32, HIGH), (3600, 60, HIGH),
    (2147483640, 35791394, HIGH),
])
def test_converted_hourly_values_get_their_level_from_the_per_minute_value(
        raw, normalized, level):
    records = [hourly(0, raw)]
    result = one(records)
    assert result.rows == [srow(0, raw, normalized, level, HOURLY, CONVERTED)]
    rest, burst = (1, 0) if level == LOW else (0, 1)
    assert result.daily == {("P001", DAY1): sd(normalized, 1, CONVERTED, rest=rest, burst=burst)}
    assert counts(result) == {CONVERTED: 1}
    assert result.rejects == []


def test_a_burst_raises_no_flag_reject_or_failed_count():
    result = one([rec(0, 31), rec(1, 1_000_000)])
    assert flags(result) == [None, None]
    assert result.rejects == []
    assert counts(result) == {}
    assert "STEPS_BURST" not in result.failed


def test_burst_stays_in_the_total_with_no_upper_cutoff():
    result = one([rec(0, 1_000_000), rec(1, 2)])
    assert result.daily[("P001", DAY1)] == sd(1_000_002, 2, None, rest=1, burst=1)


def test_burst_only_day_has_null_flags():
    result = one([rec(0, 40), rec(1, 41)])
    assert result.daily == {("P001", DAY1): sd(81, 2, None, burst=2)}


def test_integer_range_bounds_and_a_sum_beyond_32_bits():
    result = one([rec(0, 2**31 - 1), rec(1, 2**31 - 1), rec(2, -(2**31))])
    assert flags(result) == [None, None, NEGATIVE]
    assert levels(result) == [HIGH, HIGH, None]
    assert raws(result) == [2**31 - 1, 2**31 - 1, -(2**31)]
    assert spms(result) == [2**31 - 1, 2**31 - 1, None]
    assert result.daily[("P001", DAY1)] == sd(2 * (2**31 - 1), 2, NEGATIVE, burst=2)


def test_days_are_separated_by_local_date():
    result = one([rec(0, 1), rec(1440, 2), rec(1439, 4)])
    assert result.daily == {
        ("P001", DAY1): sd(5, 2, None, rest=2), ("P001", DAY2): sd(2, 1, None, rest=1)}


def test_record_order_does_not_matter_and_is_not_a_defect():
    records = [rec(5, 1), rec(1, 2), rec(3, 4)]
    forward = one(records)
    backward = one(list(reversed(records)))
    assert forward.daily == backward.daily == {("P001", DAY1): sd(7, 3, None, rest=3)}
    assert counts(forward) == counts(backward) == {}
    assert sorted(forward.rows) == sorted(backward.rows)


# --- invalid and negative values ---------------------------------------------------------


@pytest.mark.parametrize("value", [None, "5", "", 5.5, 5.0, True, False, [1], {"a": 1},
                                   2**31, -(2**31) - 1])
def test_invalid_step_values_are_withheld_and_flagged(value):
    records = [rec(0, value)]
    result = one(records)
    assert result.rows == [srow(0, None, None, None, fl=INVALID)]
    assert counts(result) == {INVALID: 1}
    assert result.rejects == [(INVALID, PATH, 1, records[0])]
    assert result.daily == {("P001", DAY1): sd(None, 0, INVALID)}


def test_missing_steps_key_is_an_invalid_value():
    record = {"timestamp": iso(T0)}
    result = one([record])
    assert result.rows == [srow(0, None, None, None, fl=INVALID)]
    assert result.rejects == [(INVALID, PATH, 1, record)]


def test_negative_value_keeps_its_raw_integer_but_has_no_normalized_value_or_level():
    records = [rec(0, -1), rec(1, 7)]
    result = one(records)
    assert result.rows == [srow(0, -1, None, None, fl=NEGATIVE), srow(1, 7, 7, LOW)]
    assert result.daily == {("P001", DAY1): sd(7, 1, NEGATIVE, rest=1)}
    assert counts(result) == {NEGATIVE: 1}
    assert result.rejects == [(NEGATIVE, PATH, 1, records[0])]


def test_a_day_with_only_withheld_records_has_null_total_zero_count_and_is_present():
    result = one([rec(0, -1), rec(1, None)])
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{NEGATIVE},{INVALID}")}


# --- _unit: conversion, unsupported units, precedence --------------------------------------


def test_hourly_record_is_converted_and_keeps_its_raw_value_and_unit_text():
    records = [hourly(0, 300), rec(1, 5)]
    result = one(records)
    assert result.rows == [srow(0, 300, 5, LOW, HOURLY, CONVERTED), srow(1, 5, 5, LOW)]
    assert result.daily == {("P001", DAY1): sd(10, 2, CONVERTED, rest=2)}
    assert counts(result) == {CONVERTED: 1}
    assert result.rejects == []


def test_a_day_of_only_converted_records_has_a_total():
    result = one([hourly(0, 420), hourly(1, 300)])
    assert spms(result) == [7, 5]
    assert flags(result) == [CONVERTED, CONVERTED]
    assert counts(result) == {CONVERTED: 2}
    assert result.rejects == []
    assert result.daily == {("P001", DAY1): sd(12, 2, CONVERTED, rest=2)}


def test_converted_burst_and_the_30_31_boundary_in_one_day():
    result = one([hourly(0, 3600), hourly(1, 1800), hourly(2, 1860)])
    assert spms(result) == [60, 30, 31]
    assert levels(result) == [HIGH, LOW, HIGH]
    assert result.daily == {("P001", DAY1): sd(121, 3, CONVERTED, rest=1, burst=2)}
    assert counts(result) == {CONVERTED: 3} and result.rejects == []


def test_zero_valued_hourly_record_converts_to_zero_and_counts_as_a_valid_minute():
    result = one([hourly(0, 0)])
    assert result.rows == [srow(0, 0, 0, LOW, HOURLY, CONVERTED)]
    assert result.daily == {("P001", DAY1): sd(0, 1, CONVERTED, rest=1)}


@pytest.mark.parametrize("raw", [1, 15, 30, 45, 59, 61, 90, 100, 1830, 3601, 2**31 - 1])
def test_hourly_value_not_divisible_by_sixty_is_unsupported_and_withheld(raw):
    records = [hourly(0, raw)]
    result = one(records)
    assert result.rows == [srow(0, raw, None, None, HOURLY, UNSUPPORTED)]
    assert counts(result) == {UNSUPPORTED: 1}
    assert result.rejects == [(UNSUPPORTED, PATH, 1, records[0])]
    assert result.daily == {("P001", DAY1): sd(None, 0, UNSUPPORTED)}


@pytest.mark.parametrize(("unit", "stored"), [
    ("steps", "steps"), ("steps_per_minute", "steps_per_minute"),
    ("STEPS_PER_HOUR", "STEPS_PER_HOUR"), (" steps_per_hour", " steps_per_hour"),
    ("", ""), (None, "null"), (5, "5"), (True, "true"), (3.5, "3.5"),
])
def test_any_other_unit_value_is_unsupported_and_stored_as_text(unit, stored):
    records = [rec(0, 60, _unit=unit)]
    result = one(records)
    assert result.rows == [srow(0, 60, None, None, stored, UNSUPPORTED)]
    assert result.rejects == [(UNSUPPORTED, PATH, 1, records[0])]
    assert counts(result) == {UNSUPPORTED: 1}


def test_unit_is_decided_per_record_and_never_assumed_from_the_participant():
    records = [hourly(0, 300), rec(1, 300), hourly(2, 100), rec(3, 40)]
    result = one(records)
    assert spms(result) == [5, 300, None, 40]
    assert levels(result) == [LOW, HIGH, None, HIGH]
    assert flags(result) == [CONVERTED, None, UNSUPPORTED, None]
    assert result.daily == {("P001", DAY1): sd(
        345, 3, f"{CONVERTED},{UNSUPPORTED}", rest=1, burst=2)}


def test_negative_with_a_unit_stays_negative_and_is_not_converted():
    records = [hourly(0, -60), rec(1, -5, _unit="u"), hourly(2, -1)]
    result = one(records)
    assert flags(result) == [NEGATIVE] * 3
    assert raws(result) == [-60, -5, -1]
    assert spms(result) == [None] * 3 and levels(result) == [None] * 3
    assert counts(result) == {NEGATIVE: 3}
    assert [r[0] for r in result.rejects] == [NEGATIVE] * 3
    assert result.daily == {("P001", DAY1): sd(None, 0, NEGATIVE)}


@pytest.mark.parametrize("value", [None, "60", 5.5, 60.0, True, 2**31])
def test_invalid_value_with_a_unit_carries_only_the_invalid_rule(value):
    records = [hourly(0, value)]
    result = one(records)
    assert flags(result) == [INVALID]
    assert counts(result) == {INVALID: 1}
    assert [r[0] for r in result.rejects] == [INVALID]
    assert result.rows[0][UNIT_COL] == HOURLY and result.rows[0][SPM] is None


def test_unit_rules_apply_only_to_a_valid_non_negative_integer():
    result = one([rec(0, "x", _unit=None), rec(1, -2, _unit=None), rec(2, 2, _unit=None)])
    assert flags(result) == [INVALID, NEGATIVE, UNSUPPORTED]


# --- duplicates --------------------------------------------------------------------------


def test_exact_duplicates_are_counted_once_and_both_rows_keep_their_value():
    result = one([rec(0, 6), rec(0, 6), rec(1, 2)])
    assert raws(result) == [6, 6, 2] and spms(result) == [6, 6, 2]
    assert levels(result) == [LOW] * 3
    assert [row[9] for row in result.rows] == [True, True, False]
    assert flags(result) == [EXACT, EXACT, None]
    assert result.daily == {("P001", DAY1): sd(8, 2, EXACT, rest=2)}
    assert counts(result) == {EXACT: 2}


def test_conflicting_duplicates_withhold_only_that_minute_with_no_pick_or_average():
    result = one([rec(0, 2), rec(0, 3), rec(1, 10)])
    assert raws(result) == [2, 3, 10]
    assert spms(result) == [None, None, 10]
    assert flags(result) == [CONFLICT, CONFLICT, None]
    assert levels(result) == [None, None, LOW]
    assert result.daily == {("P001", DAY1): sd(10, 1, CONFLICT, rest=1)}
    assert counts(result) == {CONFLICT: 2}


def test_three_records_with_two_distinct_values_are_one_conflict_group():
    result = one([rec(0, 5), rec(0, 5), rec(0, 7)])
    assert flags(result) == [CONFLICT] * 3
    assert result.daily == {("P001", DAY1): sd(None, 0, CONFLICT)}


def test_duplicate_of_a_valid_and_a_negative_value_is_a_conflict():
    result = one([rec(0, 5), rec(0, -1)])
    assert flags(result) == [CONFLICT, f"{CONFLICT},{NEGATIVE}"]
    assert raws(result) == [5, -1] and spms(result) == [None, None]
    assert levels(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{CONFLICT},{NEGATIVE}")}


def test_a_unit_record_exactly_duplicated_by_an_identical_unit_record_is_counted_once():
    records = [hourly(0, 300), hourly(0, 300)]
    result = one(records)
    assert flags(result) == [f"{EXACT},{CONVERTED}"] * 2
    assert spms(result) == [5, 5] and levels(result) == [LOW, LOW]
    assert result.daily == {("P001", DAY1): sd(5, 1, f"{EXACT},{CONVERTED}", rest=1)}
    assert counts(result) == {EXACT: 2, CONVERTED: 2}
    assert [r[0] for r in result.rejects] == [EXACT, EXACT]


def test_identical_unsupported_unit_records_are_an_exact_group_and_stay_withheld():
    result = one([hourly(0, 100), hourly(0, 100)])
    assert flags(result) == [f"{EXACT},{UNSUPPORTED}"] * 2
    assert spms(result) == [None, None] and levels(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{EXACT},{UNSUPPORTED}")}


def test_two_hourly_records_with_different_values_are_a_conflict_that_is_still_converted():
    result = one([hourly(0, 300), hourly(0, 360)])
    assert flags(result) == [f"{CONFLICT},{CONVERTED}"] * 2
    assert raws(result) == [300, 360] and spms(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{CONFLICT},{CONVERTED}")}
    assert counts(result) == {CONFLICT: 2, CONVERTED: 2}


def test_hourly_and_unit_less_records_with_the_same_normalized_value_are_a_conflict():
    result = one([rec(0, 5), hourly(0, 300)])
    assert flags(result) == [CONFLICT, f"{CONFLICT},{CONVERTED}"]
    assert spms(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{CONFLICT},{CONVERTED}")}


def test_same_value_with_and_without_an_unsupported_unit_is_a_conflict():
    result = one([rec(0, 5), rec(0, 5, _unit="u")])
    assert flags(result) == [CONFLICT, f"{CONFLICT},{UNSUPPORTED}"]


def test_p005_pattern_unit_less_duplicates_of_hourly_records_are_three_conflict_slots():
    records = p005_records()
    result = one(records)
    assert result.checked == 7
    hourly_flags = f"{CONFLICT},{CONVERTED}"
    unitless_flags = f"{CONFLICT},{ALIGN}"
    assert flags(result) == [hourly_flags, unitless_flags] * 3 + [None]
    assert raws(result) == [240, 240, 180, 180, 120, 120, 10]
    assert spms(result) == [None] * 6 + [10]
    assert levels(result) == [None] * 6 + [LOW]
    assert result.rows[0] == srow(0, 240, None, None, HOURLY, hourly_flags, dup=True)
    assert result.rows[1] == srow(datetime(2026, 1, 8, 0, 0, 30), 240, None, None, None,
                                  unitless_flags, dup=True)
    assert result.daily == {("P001", DAY1): sd(
        10, 1, f"{CONFLICT},{ALIGN},{CONVERTED}", rest=1)}
    assert counts(result) == {CONFLICT: 6, ALIGN: 3, CONVERTED: 3}
    assert result.rejects == [
        (CONFLICT, PATH, 1, records[0]),
        (CONFLICT, PATH, 2, records[1]), (ALIGN, PATH, 2, records[1]),
        (CONFLICT, PATH, 3, records[2]),
        (CONFLICT, PATH, 4, records[3]), (ALIGN, PATH, 4, records[3]),
        (CONFLICT, PATH, 5, records[4]),
        (CONFLICT, PATH, 6, records[5]), (ALIGN, PATH, 6, records[5]),
    ]


def test_conflicting_burst_duplicates_carry_only_the_conflict_flag_and_no_level():
    result = one([rec(0, 40), rec(0, 41)])
    assert flags(result) == [CONFLICT] * 2
    assert raws(result) == [40, 41] and spms(result) == [None, None]
    assert levels(result) == [None, None]
    assert counts(result) == {CONFLICT: 2}
    assert result.daily == {("P001", DAY1): sd(None, 0, CONFLICT)}


def test_burst_inside_an_exact_duplicate_group_keeps_its_value_and_level():
    result = one([rec(0, 40), rec(0, 40), rec(1, 20), rec(1, 20)])
    assert flags(result) == [EXACT] * 4
    assert spms(result) == [40, 40, 20, 20]
    assert levels(result) == [HIGH, HIGH, LOW, LOW]
    assert counts(result) == {EXACT: 4}
    assert result.daily == {("P001", DAY1): sd(60, 2, EXACT, rest=1, burst=1)}


def test_exact_duplicates_that_are_invalid_stay_withheld_and_count_nothing():
    result = one([rec(0, None), rec(0, None)])
    assert flags(result) == [f"{EXACT},{INVALID}"] * 2
    assert levels(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{EXACT},{INVALID}")}


def test_the_same_minute_for_two_participants_is_not_a_duplicate():
    result = layer({PATH: [rec(0, 3)], PATH_2: [rec(0, 4)]})
    assert flags(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(3, 1, None, rest=1),
                            ("P002", DAY1): sd(4, 1, None, rest=1)}


# --- timestamps: zones, local dates, window -----------------------------------------------


def test_naive_and_z_timestamps_share_a_slot_and_utc_is_converted_to_minus_five():
    records = [rec(0, 5), rec("2026-01-08T05:00:00Z", 5), rec("2026-01-09T04:30:00Z", 7),
               rec("2026-01-08T04:59:00Z", 9), rec("2026-01-09T00:00:00", 1)]
    result = one(records)
    assert [row[2] for row in result.rows] == [
        T0, T0, datetime(2026, 1, 8, 23, 30), datetime(2026, 1, 7, 23, 59),
        datetime(2026, 1, 9, 0, 0)]
    assert [row[1] for row in result.rows] == [DAY1, DAY1, DAY1, date(2026, 1, 7), DAY2]
    assert flags(result) == [EXACT, EXACT, None, OUT, None]
    assert [row[8] for row in result.rows] == [False, False, False, True, False]
    assert levels(result) == [LOW] * 5
    assert result.daily == {("P001", DAY1): sd(12, 2, EXACT, rest=2),
                            ("P001", DAY2): sd(1, 1, None, rest=1)}
    assert counts(result) == {EXACT: 2, OUT: 1}
    assert result.rejects[-1] == (OUT, PATH, 4, records[3])


def test_converted_hourly_record_crossing_local_midnight_uses_the_converted_local_date():
    result = one([hourly("2026-01-09T04:30:00Z", 3600), hourly("2026-01-09T05:00:00Z", 120)])
    assert [row[1] for row in result.rows] == [DAY1, DAY2]
    assert spms(result) == [60, 2]
    assert result.daily == {("P001", DAY1): sd(60, 1, CONVERTED, burst=1),
                            ("P001", DAY2): sd(2, 1, CONVERTED, rest=1)}


def test_window_boundaries_use_the_local_date_and_are_inclusive():
    result = one([rec("2026-01-10T23:59:00", 1), rec("2026-01-11T00:00:00", 2),
                  rec("2026-01-07T23:59:00", 3), rec("2026-01-08T00:00:00", 4)])
    assert flags(result) == [None, OUT, OUT, None]
    assert set(result.daily) == {("P001", WINDOW[-1]), ("P001", DAY1)}


def test_out_of_window_converted_record_is_flagged_in_silver_but_not_in_the_day_flags():
    result = one([hourly("2026-01-07T23:59:00", 600), rec(0, 3)])
    assert flags(result) == [f"{OUT},{CONVERTED}", None]
    assert result.daily == {("P001", DAY1): sd(3, 1, None, rest=1)}
    assert counts(result) == {OUT: 1, CONVERTED: 1}


def test_unparseable_timestamps_keep_their_row_with_null_time_and_never_reach_gold():
    records = [rec("2026-01-08 00:00:00", 5), {"steps": 6}, rec(None, 7),
               {"timestamp": 12345, "steps": 8}, rec("garbage", 9)]
    result = one(records)
    assert len(result.rows) == 5
    for row, value in zip(result.rows, [5, 6, 7, 8, 9]):
        assert row == ("P001", None, None, value, value, LOW, None, False, False, False,
                       UNPARSEABLE)
    assert result.daily == {}
    assert counts(result) == {UNPARSEABLE: 5}
    assert len(result.rejects) == 5


def test_records_with_unparseable_timestamps_are_not_grouped_as_duplicates():
    result = one([rec("bad", 1), rec("bad", 2)])
    assert flags(result) == [UNPARSEABLE, UNPARSEABLE]
    assert [row[9] for row in result.rows] == [False, False]


# --- non-zero seconds --------------------------------------------------------------------


def test_non_zero_seconds_are_flagged_and_not_floored():
    result = one([rec("2026-01-08T00:01:30", 4)])
    assert result.rows == [srow(datetime(2026, 1, 8, 0, 1, 30), 4, 4, LOW, None, ALIGN)]
    assert result.daily == {("P001", DAY1): sd(4, 1, ALIGN, rest=1)}
    assert counts(result) == {ALIGN: 1}


def test_a_fraction_of_zero_is_aligned_but_a_real_fraction_is_not():
    result = one([rec("2026-01-08T00:00:00.000", 1), rec("2026-01-08T00:01:00.5", 2)])
    assert flags(result) == [None, ALIGN]


def test_non_zero_second_record_colliding_with_the_same_value_is_an_exact_duplicate():
    result = one([rec(2, 5), rec("2026-01-08T00:02:30", 5)])
    assert flags(result) == [EXACT, f"{EXACT},{ALIGN}"]
    assert result.rows[1][2] == datetime(2026, 1, 8, 0, 2, 30)
    assert result.daily == {("P001", DAY1): sd(5, 1, f"{EXACT},{ALIGN}", rest=1)}


def test_non_zero_second_record_colliding_with_another_value_is_a_conflict():
    result = one([rec(3, 6), rec("2026-01-08T00:03:30", 7)])
    assert flags(result) == [CONFLICT, f"{CONFLICT},{ALIGN}"]
    assert raws(result) == [6, 7] and spms(result) == [None, None]
    assert result.daily == {("P001", DAY1): sd(None, 0, f"{CONFLICT},{ALIGN}")}


# --- participants: orphan, folder mismatch, fallback ---------------------------------------


def test_orphan_participant_keeps_its_row_but_never_reaches_gold():
    records = [rec(0, 3)]
    result = layer({"wearable_events/P999/steps.json": records})
    assert result.rows == [srow(0, 3, 3, LOW, None, ORPHAN, pid="P999", orphan=True)]
    assert result.daily == {}
    assert counts(result) == {ORPHAN: 1}
    assert result.rejects == [(ORPHAN, "wearable_events/P999/steps.json", 1, records[0])]


def test_folder_mismatch_is_flagged_excluded_and_does_not_join_duplicate_groups():
    mismatched = rec(0, 3, participant_id="P002")
    own = rec(0, 9, participant_id="P002")
    result = layer({PATH: [mismatched], PATH_2: [own]})
    assert result.rows[0] == srow(0, 3, 3, LOW, None, MISMATCH, pid="P002")
    assert result.rows[1] == srow(0, 9, 9, LOW, pid="P002")
    assert result.daily == {("P002", DAY1): sd(9, 1, None, rest=1)}
    assert counts(result) == {MISMATCH: 1}
    assert result.rejects == [(MISMATCH, PATH, 1, mismatched)]


@pytest.mark.parametrize("pid", [None, "", "   ", 5, ["P001"]])
def test_missing_blank_or_non_string_participant_id_falls_back_to_the_folder(pid):
    record = rec(0, 3)
    record["participant_id"] = pid
    result = one([record])
    assert result.rows[0][0] == "P001"
    assert counts(result) == {}
    assert result.daily == {("P001", DAY1): sd(3, 1, None, rest=1)}


def test_matching_record_participant_id_is_used_without_flags():
    result = one([rec(0, 3, participant_id="P001")])
    assert result.rows[0][0] == "P001" and counts(result) == {}


def test_participant_without_a_file_has_no_daily_entry():
    result = one([rec(0, 3)])
    assert all(key[0] == "P001" for key in result.daily)


# --- robustness and determinism ------------------------------------------------------------


def test_non_object_elements_are_defective_rows_and_never_raise():
    elements = [5, "x", None, [1], rec(0, 1)]
    result = one(elements)
    assert len(result.rows) == 5 and result.checked == 5
    for row in result.rows[:4]:
        assert row == ("P001", None, None, None, None, None, None, False, False, False,
                       f"{UNPARSEABLE},{INVALID}")
    assert [r[3] for r in result.rejects if r[0] == INVALID][:4] == elements[:4]
    assert result.daily == {("P001", DAY1): sd(1, 1, None, rest=1)}


def test_in_object_defects_never_raise():
    weird = [{}, {"timestamp": [], "steps": {}}, {"timestamp": 5, "steps": "a", "_unit": []},
             {"participant_id": 5, "timestamp": None, "steps": None}]
    result = one(weird)
    assert len(result.rows) == 4
    assert counts(result)[UNPARSEABLE] == 4 and counts(result)[INVALID] == 4
    assert CONVERTED not in counts(result) and UNSUPPORTED not in counts(result)


def test_file_mapping_order_does_not_change_the_layer():
    a, b = [rec(0, 1), rec(0, 2)], [rec(1, 3)]
    forward = layer({PATH: a, PATH_2: b})
    backward = layer({PATH_2: b, PATH: a})
    assert forward == backward
    assert [row[0] for row in forward.rows] == ["P001", "P001", "P002"]


def test_checked_counts_records_across_files_and_failed_counts_each_record_once_per_rule():
    result = layer({PATH: [rec(0, -1, _unit="u"), rec(1, -2), hourly(2, 60)],
                    PATH_2: [rec(0, 5)]})
    assert result.checked == 4
    assert counts(result) == {NEGATIVE: 2, CONVERTED: 1}


def test_a_record_flagged_by_several_rules_has_sorted_flags_and_one_reject_per_rejecting_rule():
    negative = rec("2026-01-08T00:00:30", -4, _unit="u")
    converted = hourly("2026-01-08T00:01:30", 3600)
    unsupported = rec("2026-01-08T00:02:30", 7, _unit="u")
    result = one([negative, converted, unsupported])
    assert flags(result) == [f"{NEGATIVE},{ALIGN}", f"{ALIGN},{CONVERTED}",
                             f"{ALIGN},{UNSUPPORTED}"]
    assert result.rejects == [
        (NEGATIVE, PATH, 1, negative), (ALIGN, PATH, 1, negative),
        (ALIGN, PATH, 2, converted),
        (ALIGN, PATH, 3, unsupported), (UNSUPPORTED, PATH, 3, unsupported)]
    assert counts(result) == {NEGATIVE: 1, ALIGN: 3, CONVERTED: 1, UNSUPPORTED: 1}


# --- pipeline level: gold, silver, quality ------------------------------------------------


def test_default_fixture_gold_steps_values(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert gold_steps(result, "P001", DAY1) == [0, 1, 1, 0, True, None]
    for pid, day in (("P001", DAY2), ("P001", WINDOW[2]), ("P002", DAY1)):
        assert gold_steps(result, pid, day) == [None, 0, 0, 0, False, None]


def test_gold_columns_are_the_survey_sleep_then_steps_columns_in_order(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    cols = [r[0] for r in query(result, "SELECT column_name FROM information_schema.columns "
                                        "WHERE table_name = 'gold_clinical_daily' "
                                        "ORDER BY ordinal_position")]
    assert cols[10:] == [
        "total_sleep_min", "deep_sleep_pct", "sleep_efficiency_pct", "sleep_session_count",
        "sleep_present", "sleep_quality_flags", *GOLD_STEPS]
    assert len(cols) == 22


def test_silver_steps_schema_and_grain(tmp_path):
    records = worked_records() + [rec("bad", 1)]
    result = run(data_with(tmp_path, records), tmp_path / "out")
    cols = query(result, "SELECT column_name, data_type FROM information_schema.columns "
                         "WHERE table_name = 'silver_steps_minute' ORDER BY ordinal_position")
    assert cols == [
        ("participant_id", "VARCHAR"), ("local_date", "DATE"), ("minute_ts", "TIMESTAMP"),
        ("steps", "INTEGER"), ("steps_per_minute", "INTEGER"), ("activity_level", "VARCHAR"),
        ("unit", "VARCHAR"), ("is_orphan", "BOOLEAN"), ("is_out_of_window", "BOOLEAN"),
        ("is_duplicate_minute", "BOOLEAN"), ("quality_flags", "VARCHAR")]
    assert query(result, "SELECT count(*) FROM silver_steps_minute") == [(13,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute WHERE steps IS NULL") == [(0,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE steps_per_minute IS NULL") == [(4,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE (steps_per_minute IS NULL) <> (activity_level IS NULL)") == [(0,)]
    assert query(result, "SELECT activity_level, count(*) FROM silver_steps_minute "
                         "GROUP BY ALL ORDER BY ALL") == [(LOW, 7), (HIGH, 2), (None, 4)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE minute_ts IS NULL AND local_date IS NULL") == [(1,)]
    assert query(result, "SELECT unit FROM silver_steps_minute WHERE unit IS NOT NULL") == [
        (HOURLY,)] * 3
    assert query(result, "SELECT steps, steps_per_minute, activity_level, unit "
                         "FROM silver_steps_minute "
                         "WHERE minute_ts = TIMESTAMP '2026-01-08 00:04:00'") == [
        (3600, 60, HIGH, HOURLY)]
    assert query(result, "SELECT steps, steps_per_minute FROM silver_steps_minute "
                         "WHERE quality_flags = 'STEPS_NEGATIVE'") == [(-1, None)]
    assert query(result, "SELECT count(*) FROM (SELECT participant_id, minute_ts FROM "
                         "silver_steps_minute WHERE minute_ts IS NOT NULL "
                         "AND NOT is_duplicate_minute GROUP BY ALL HAVING count(*) > 1)") == [(0,)]


def test_pipeline_gold_matches_the_worked_example_and_counts_are_reported(tmp_path):
    records = worked_records()
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert gold_steps(result, "P001", DAY1) == [
        111, 7, 5, 2, True, f"{CONFLICT},{EXACT},{NEGATIVE},{CONVERTED},{UNSUPPORTED}"]
    results = quality(result)
    assert ("STEPS_BURST", "steps") not in results
    assert ("STEPS_UNIT_FIELD_PRESENT", "steps") not in results
    expected_failed = {NEGATIVE: 1, CONVERTED: 2, UNSUPPORTED: 1, EXACT: 2, CONFLICT: 2}
    for rule in STEPS_REASONS:
        assert results[(rule, "steps")][:2] == STEPS_HANDLING[rule]
        assert results[(rule, "steps")][-3:-1] == (12, expected_failed.get(rule, 0))
    rejects = query(result, "SELECT rule_id, source, row_ref, reason, raw_values "
                            "FROM quality_rejects WHERE source = 'steps' ORDER BY row_ref")
    assert len(rejects) == 6
    by_ref = {(r[0], r[2]): r for r in rejects}
    assert {r[2] for r in rejects} == {f"{PATH}:{n}" for n in (4, 6, 9, 10, 11, 12)}
    negative = by_ref[(NEGATIVE, f"{PATH}:4")]
    assert negative[1] == "steps" and negative[3] == STEPS_REASONS[NEGATIVE]
    assert json.loads(negative[4]) == records[3]
    assert json.loads(by_ref[(UNSUPPORTED, f"{PATH}:6")][4]) == records[5]
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         f"WHERE rule_id = '{CONVERTED}'") == [(0,)]
    assert query(result, "SELECT steps, steps_per_minute, activity_level, quality_flags "
                         "FROM silver_steps_minute "
                         "WHERE minute_ts = TIMESTAMP '2026-01-08 00:02:00'") == [
        (31, 31, HIGH, None)]


def test_converted_records_report_a_failed_count_and_flags_but_write_no_rejects(tmp_path):
    records = [hourly(0, 3600), hourly(1, 1800), hourly(2, 120), rec(3, 5)]
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert query(result, "SELECT steps, steps_per_minute, activity_level, unit, quality_flags "
                         "FROM silver_steps_minute ORDER BY minute_ts") == [
        (3600, 60, HIGH, HOURLY, CONVERTED), (1800, 30, LOW, HOURLY, CONVERTED),
        (120, 2, LOW, HOURLY, CONVERTED), (5, 5, LOW, None, None)]
    assert gold_steps(result, "P001", DAY1) == [97, 4, 3, 1, True, CONVERTED]
    assert quality(result)[(CONVERTED, "steps")][-3:-1] == (4, 3)
    assert query(result, "SELECT count(*) FROM quality_rejects WHERE source = 'steps'") == [(0,)]
    report = result.report_path.read_text(encoding="utf-8")
    assert f"| {CONVERTED} | steps | convert | low | 4 | 3 |" in report
    assert f"| {UNSUPPORTED} | steps | withhold_field | high | 4 | 0 |" in report


def test_unsupported_units_are_withheld_and_rejected_with_raw_values(tmp_path):
    records = [hourly(0, 100), rec(1, 5, _unit="steps_per_minute"), rec(2, 60, _unit=None),
               rec(3, 7)]
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert query(result, "SELECT steps, steps_per_minute, activity_level, unit, quality_flags "
                         "FROM silver_steps_minute ORDER BY minute_ts") == [
        (100, None, None, HOURLY, UNSUPPORTED),
        (5, None, None, "steps_per_minute", UNSUPPORTED),
        (60, None, None, "null", UNSUPPORTED), (7, 7, LOW, None, None)]
    assert gold_steps(result, "P001", DAY1) == [7, 1, 1, 0, True, UNSUPPORTED]
    assert quality(result)[(UNSUPPORTED, "steps")][-3:-1] == (4, 3)
    rejects = query(result, "SELECT row_ref, reason, raw_values FROM quality_rejects "
                            "WHERE source = 'steps' ORDER BY row_ref")
    assert [(r[0], r[1]) for r in rejects] == [
        (f"{PATH}:{n}", STEPS_REASONS[UNSUPPORTED]) for n in (1, 2, 3)]
    assert [json.loads(r[2]) for r in rejects] == records[:3]


def test_p005_pattern_withholds_three_slots_in_the_pipeline(tmp_path):
    records = p005_records()
    result = run(data_with(tmp_path, records), tmp_path / "out")
    assert gold_steps(result, "P001", DAY1) == [
        10, 1, 1, 0, True, f"{CONFLICT},{ALIGN},{CONVERTED}"]
    results = quality(result)
    for rule, failed in ((CONFLICT, 6), (ALIGN, 3), (CONVERTED, 3)):
        assert results[(rule, "steps")][-3:-1] == (7, failed)
    assert query(result, "SELECT count(*) FROM quality_rejects WHERE source = 'steps'") == [(9,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE is_duplicate_minute AND steps_per_minute IS NULL") == [(6,)]


def test_a_burst_only_day_has_null_flags_in_gold_and_nothing_in_quality_tables(tmp_path):
    result = run(data_with(tmp_path, [rec(0, 31), rec(1, 40), rec(2, 20), rec(3, 3)]),
                 tmp_path / "out")
    assert gold_steps(result, "P001", DAY1) == [94, 4, 2, 2, True, None]
    assert query(result, "SELECT count(*) FROM quality_rejects WHERE source = 'steps'") == [(0,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE quality_flags IS NOT NULL") == [(0,)]
    assert all(failed == 0 for (rule, source), (*_, failed, _) in quality(result).items()
               if source == "steps")


def test_gold_activity_minute_counts_sum_to_valid_minutes_on_every_day(tmp_path):
    records = worked_records() + [rec(1440 + i, v) for i, v in enumerate([0, 15, 16, 30, 31])]
    result = run(data_with(tmp_path, records, [rec(0, -1), hourly(1, 5940)]), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM gold_clinical_daily WHERE "
                         "minutes_rest_to_moderate + minutes_burst "
                         "<> steps_valid_minute_count") == [(0,)]
    assert gold_steps(result, "P001", DAY2) == [92, 5, 4, 1, True, None]
    assert gold_steps(result, "P002", DAY1) == [99, 1, 0, 1, True, f"{NEGATIVE},{CONVERTED}"]


def test_every_steps_rule_fires_with_its_exact_reason_and_gold_ignores_excluded_records(tmp_path):
    p001 = [
        rec(0, 5), rec(1, 31), rec(2, -1), hourly(3, 3600), hourly(4, 100), rec(5, None),
        rec(6, 6), rec(6, 6), rec(7, 2), rec(7, 3), rec("2026-01-08T00:08:30", 4),
        rec("bad", 1), rec("2026-01-07T23:00:00", 1), rec(9, 1, participant_id="P002"),
    ]
    data = data_with(tmp_path, p001)
    write_steps(data, "P999", [rec(0, 1)])
    result = run(data, tmp_path / "out")
    reasons = dict(query(result, "SELECT DISTINCT rule_id, reason FROM quality_rejects "
                                 "WHERE source = 'steps'"))
    assert reasons == {rule: text for rule, text in STEPS_REASONS.items() if rule != CONVERTED}
    results = quality(result)
    assert query(result, "SELECT reason FROM quality_results "
                         f"WHERE rule_id = '{CONVERTED}'") == [(STEPS_REASONS[CONVERTED],)]
    expected_failed = {ORPHAN: 1, MISMATCH: 1, UNPARSEABLE: 1, OUT: 1, INVALID: 1,
                       NEGATIVE: 1, CONVERTED: 1, UNSUPPORTED: 1, CONFLICT: 2, EXACT: 2,
                       ALIGN: 1}
    assert set(expected_failed) == set(STEPS_REASONS)
    for rule, n in expected_failed.items():
        assert results[(rule, "steps")][-3:-1] == (15, n)
    assert gold_steps(result, "P001", DAY1) == [
        106, 5, 3, 2, True,
        f"{CONFLICT},{EXACT},{NEGATIVE},{ALIGN},{CONVERTED},{UNSUPPORTED},{INVALID}"]
    assert gold_steps(result, "P002", DAY1) == [None, 0, 0, 0, False, None]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2,)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily "
                         "WHERE participant_id = 'P999'") == [(0,)]


def test_steps_for_both_participants_and_an_all_withheld_day(tmp_path):
    data = data_with(tmp_path, [rec(0, 2), rec(1440, 5)], [rec(0, -1), rec(1, -2)])
    result = run(data, tmp_path / "out")
    assert gold_steps(result, "P001", DAY1) == [2, 1, 1, 0, True, None]
    assert gold_steps(result, "P001", DAY2) == [5, 1, 1, 0, True, None]
    assert gold_steps(result, "P002", DAY1) == [None, 0, 0, 0, True, NEGATIVE]
    assert gold_steps(result, "P002", DAY2) == [None, 0, 0, 0, False, None]


def test_participant_without_a_steps_file_has_null_zero_false_null(tmp_path):
    data = make_data(tmp_path)
    (data / "wearable_events" / "P001" / "steps.json").unlink()
    result = run(data, tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_steps_minute") == [(0,)]
    for pid in ("P001", "P002"):
        assert gold_steps(result, pid, DAY1) == [None, 0, 0, 0, False, None]
    assert quality(result)[(NEGATIVE, "steps")][-3:-1] == (0, 0)
    assert quality(result)[(CONVERTED, "steps")][-3:-1] == (0, 0)


def test_existing_gold_columns_are_unchanged_by_the_steps_layer(tmp_path):
    with_steps = run(data_with(tmp_path / "a", worked_records()), tmp_path / "a" / "out")
    bare_data = make_data(tmp_path / "b")
    (bare_data / "wearable_events" / "P001" / "steps.json").unlink()
    bare = run(bare_data, tmp_path / "b" / "out")
    cols = ("participant_id, local_date, fatigue_score, stress_score, readiness_score, "
            "sleep_quality_score, survey_present, survey_row_count, survey_valid_score_count, "
            "survey_quality_flags, total_sleep_min, deep_sleep_pct, sleep_efficiency_pct, "
            "sleep_session_count, sleep_present, sleep_quality_flags")
    sql = f"SELECT {cols} FROM gold_clinical_daily ORDER BY ALL"
    assert query(with_steps, sql) == query(bare, sql)
    assert query(with_steps, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2,)]


# --- table evolution -----------------------------------------------------------------------


OLD_10 = ("participant_id VARCHAR, local_date DATE, fatigue_score INTEGER, stress_score INTEGER, "
          "readiness_score INTEGER, sleep_quality_score INTEGER, survey_present BOOLEAN, "
          "survey_row_count INTEGER, survey_valid_score_count INTEGER, "
          "survey_quality_flags VARCHAR")
OLD_16 = (OLD_10 + ", total_sleep_min INTEGER, deep_sleep_pct DOUBLE, "
          "sleep_efficiency_pct DOUBLE, sleep_session_count INTEGER, sleep_present BOOLEAN, "
          "sleep_quality_flags VARCHAR")
# A superseded plan: the steps columns without the two activity-minute columns, so the
# added columns land after the steps flags (a different order from a fresh table).
OLD_20 = (OLD_16 + ", total_steps BIGINT, steps_valid_minute_count INTEGER, "
          "steps_present BOOLEAN, steps_quality_flags VARCHAR")


@pytest.mark.parametrize(("ddl", "values"), [
    (OLD_10, "('P001', DATE '2026-01-08', 3, 3, 8, 3, true, 1, 4, NULL)"),
    (OLD_16, "('P001', DATE '2026-01-08', 3, 3, 8, 3, true, 1, 4, NULL, NULL, NULL, NULL, 0, "
             "false, NULL)"),
    (OLD_20, "('P001', DATE '2026-01-08', 3, 3, 8, 3, true, 1, 4, NULL, NULL, NULL, NULL, 0, "
             "false, NULL, 99, 9, true, 'STALE')"),
])
def test_database_from_before_unit_conversion_gets_the_missing_columns_added(
        tmp_path, ddl, values):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    connection = duckdb.connect(str(first.database_path))
    try:
        connection.execute("DROP TABLE gold_clinical_daily")
        connection.execute(f"CREATE TABLE gold_clinical_daily ({ddl}, "
                           "PRIMARY KEY (participant_id, local_date))")
        connection.execute(f"INSERT INTO gold_clinical_daily VALUES {values}")
        connection.execute("DROP TABLE silver_steps_minute")
    finally:
        connection.close()
    second = run(data, out)
    row = gold(second, "P001", DAY1)
    assert [row[c] for c in GOLD_STEPS] == [0, 1, 1, 0, True, None]
    assert row["fatigue_score"] == 3
    assert len(row) == 22
    types = dict(query(second, "SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_name = 'gold_clinical_daily'"))
    assert types["minutes_rest_to_moderate"] == types["minutes_burst"] == "INTEGER"
    assert query(second, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2,)]
    assert query(second, "SELECT count(*) FROM silver_steps_minute") == [(1,)]
    assert gold_steps(second, "P002", DAY1) == [None, 0, 0, 0, False, None]


def test_old_gold_rows_outside_the_window_are_retained_with_null_steps(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    connection = duckdb.connect(str(first.database_path))
    try:
        connection.execute("INSERT INTO gold_clinical_daily (participant_id, local_date) "
                           "VALUES ('P777', DATE '2020-01-01')")
    finally:
        connection.close()
    second = run(data, out)
    row = gold(second, "P777", date(2020, 1, 1))
    assert [row[c] for c in GOLD_STEPS] == [None] * 6
    assert query(second, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2 + 1,)]


# --- idempotency, determinism, raw immutability --------------------------------------------


def test_rerun_on_unchanged_inputs_is_identical_including_the_report(tmp_path):
    data = data_with(tmp_path, worked_records(), [rec(0, 4)])
    first = run(data, tmp_path / "out")
    before = snapshot(first)
    assert before["silver_steps_minute"]
    report = first.report_path.read_bytes()
    second = run(data, tmp_path / "out")
    assert snapshot(second) == before
    assert second.report_path.read_bytes() == report
    assert second.run_id == first.run_id


def test_changing_one_record_changes_exactly_that_days_step_columns(tmp_path):
    data = data_with(tmp_path, [rec(0, 3), rec(1440, 4), rec(1441, 5)], [rec(0, 8)])
    first = run(data, tmp_path / "out")
    before = query(first, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    write_steps(data, "P001", [rec(0, 3), rec(1440, 4), rec(1441, 50)])
    second = run(data, tmp_path / "out")
    after = query(second, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    changed = [(a[0], a[1]) for a, b in zip(after, before) if a != b]
    assert changed == [("P001", DAY2)]
    assert gold_steps(second, "P001", DAY2) == [54, 2, 1, 1, True, None]


def test_correcting_one_hourly_record_changes_exactly_that_days_step_columns(tmp_path):
    data = data_with(tmp_path, [hourly(0, 600), hourly(1440, 600)], [rec(0, 8)])
    first = run(data, tmp_path / "out")
    assert gold_steps(first, "P001", DAY2) == [10, 1, 1, 0, True, CONVERTED]
    before = query(first, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    write_steps(data, "P001", [hourly(0, 600), hourly(1440, 3600)])
    second = run(data, tmp_path / "out")
    after = query(second, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    assert [(a[0], a[1]) for a, b in zip(after, before) if a != b] == [("P001", DAY2)]
    assert gold_steps(second, "P001", DAY2) == [60, 1, 0, 1, True, CONVERTED]


def test_changing_a_survey_row_does_not_change_step_columns(tmp_path):
    data = data_with(tmp_path, worked_records())
    first = run(data, tmp_path / "out")
    sql = ("SELECT participant_id, local_date, " + ", ".join(GOLD_STEPS)
           + " FROM gold_clinical_daily ORDER BY ALL")
    before = query(first, sql)
    survey = default_survey()
    survey[0] = survey_row("P001", DAY1.isoformat(), ["4", "4", "9", "4"])
    write_csv(data / "health_summaries" / "wellness_survey.csv", SURVEY_COLUMNS, survey)
    second = run(data, tmp_path / "out")
    assert query(second, sql) == before
    assert gold(second, "P001", DAY1)["fatigue_score"] == 4


def test_raw_data_is_untouched_and_nothing_is_created_under_the_data_dir(tmp_path):
    data = data_with(tmp_path, worked_records())
    before = tree_hashes(data)
    run(data, tmp_path / "out")
    run(data, tmp_path / "out")
    assert tree_hashes(data) == before


# --- failure contract ----------------------------------------------------------------------


@pytest.mark.parametrize("content", [
    b"{bad", b"", b"\xff\xfe[]", b'{"a": 1}', b'"text"', b"5", b"null",
    b'[{"timestamp": "2026-01-08T00:00:00", "steps": 1}, 5]', b"[[]]", b'["x"]',
])
def test_malformed_steps_file_raises_before_any_table_is_touched(tmp_path, content):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before = snapshot(first)
    (data / "wearable_events" / "P001" / "steps.json").write_bytes(content)
    with pytest.raises(PipelineError):
        run(data, out)
    assert snapshot(first) == before


def test_malformed_steps_file_on_a_fresh_output_dir_creates_no_database(tmp_path):
    data = make_data(tmp_path)
    (data / "wearable_events" / "P001" / "steps.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(PipelineError):
        run(data, tmp_path / "out")
    assert not (tmp_path / "out" / "wearable.duckdb").exists()


def test_cli_exits_one_on_malformed_steps_json_and_zero_on_valid(tmp_path):
    data = data_with(tmp_path)
    out = tmp_path / "out"
    args = ["run", "--data-dir", str(data), "--output-dir", str(out)]
    (data / "wearable_events" / "P001" / "steps.json").write_text("{bad", encoding="utf-8")
    assert main(args) == 1
    write_steps(data, "P001", [rec(0, 1)])
    assert main(args) == 0


def test_in_object_defects_never_raise_in_the_pipeline(tmp_path):
    weird = [{}, {"timestamp": [], "steps": {}}, {"participant_id": 5, "steps": None}]
    result = run(data_with(tmp_path, weird), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_steps_minute") == [(3,)]
    assert quality(result)[(INVALID, "steps")][-3:-1] == (3, 3)


# --- scale ---------------------------------------------------------------------------------


def test_sixty_thousand_records_are_bulk_loaded_in_reasonable_time(tmp_path):
    records = [hourly(i, 60 * (31 + i % 3)) if i % 5 == 0 else rec(i, 31 + i % 3)
               for i in range(60_000)]
    data = data_with(tmp_path, records)
    started = time.monotonic()
    result = run_pipeline(data, tmp_path / "out", study_start=DAY1, study_days=60)
    elapsed = time.monotonic() - started
    assert elapsed < 25, f"pipeline took {elapsed:.1f}s for 60,000 records"
    assert query(result, "SELECT count(*) FROM silver_steps_minute") == [(60_000,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE activity_level = 'burst' AND steps_per_minute BETWEEN 31 AND 33"
                 ) == [(60_000,)]
    assert query(result, "SELECT count(*) FROM silver_steps_minute "
                         "WHERE unit = 'steps_per_hour'") == [(12_000,)]
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         "WHERE source = 'steps'") == [(0,)]
    assert quality(result)[(CONVERTED, "steps")][-3:-1] == (60_000, 12_000)
    row = gold(result, "P001", DAY1)
    assert row["steps_valid_minute_count"] == 1440
    assert row["minutes_burst"] == 1440 and row["minutes_rest_to_moderate"] == 0
    assert row["steps_quality_flags"] == CONVERTED
    assert row["total_steps"] == sum(31 + i % 3 for i in range(1440))
