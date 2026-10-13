"""INC-002-01: silver normalization contract (typed participants, trimmed ISO dates)."""

from __future__ import annotations

import json
from datetime import date

import duckdb
import pytest

from tests.test_walking_skeleton import (
    DAYS, P001, P002, PARTICIPANT_COLUMNS, REASONS, SURVEY_COLUMNS, WINDOW, default_survey,
    gold, make_data, query, quality, run, snapshot, survey_row,
)
from wearable_pipeline.normalize import parse_integer, parse_iso_date

DAY = WINDOW[0].isoformat()


def participant(pid: str, age="38", height="183", max_hr="167") -> list[str]:
    return [pid, f"FBT-{pid}-XXXXXXXX", age, height, "male", "B", max_hr]


# --- pure normalizers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("38", 38), (" 38 ", 38), ("\t38\n", 38), ("-5", -5), ("+7", 7), ("0", 0),
        ("2147483647", 2147483647), ("-2147483648", -2147483648),
        ("2147483648", None), ("99999999999", None),
        ("", None), ("   ", None), ("abc", None), ("38.0", None), ("3 8", None),
        ("1,000", None), ("٣٨", None), ("--5", None), ("0x10", None),
    ],
)
def test_parse_integer(raw, expected):
    assert parse_integer(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-08", date(2026, 1, 8)), (" 2026-01-08 ", date(2026, 1, 8)),
        ("\t2026-01-08\n", date(2026, 1, 8)), ("2024-02-29", date(2024, 2, 29)),
        ("2026-02-30", None), ("2026-13-01", None), ("2025-02-29", None),
        ("20260108", None), ("2026-W02-4", None), ("01/08/2026", None),
        ("2026-1-8", None), ("2026-01-08T00:00:00", None), ("2026-01-08 10:00", None),
        ("", None), ("   ", None), ("٢٠٢٦-٠١-٠٨", None),
    ],
)
def test_parse_iso_date(raw, expected):
    assert parse_iso_date(raw) == expected


# --- silver schema --------------------------------------------------------------------------


def columns(result, table: str) -> dict[str, str]:
    return dict(query(result, "SELECT column_name, data_type FROM information_schema.columns "
                              f"WHERE table_name = '{table}'"))


def test_silver_schemas_have_no_raw_or_row_number_columns(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert columns(result, "silver_participants") == {
        "participant_id": "VARCHAR", "device_id": "VARCHAR", "age": "INTEGER",
        "height_cm": "INTEGER", "gender": "VARCHAR", "chronotype": "VARCHAR",
        "max_heart_rate": "INTEGER", "is_duplicate_key": "BOOLEAN", "quality_flags": "VARCHAR",
    }
    assert columns(result, "silver_wellness_survey") == {
        "participant_id": "VARCHAR", "local_date": "DATE", "fatigue_score": "INTEGER",
        "stress_score": "INTEGER", "readiness_score": "INTEGER",
        "sleep_quality_score": "INTEGER", "is_orphan": "BOOLEAN",
        "is_duplicate_key": "BOOLEAN", "is_out_of_window": "BOOLEAN",
        "quality_flags": "VARCHAR",
    }


def test_quality_tables_and_gold_keep_their_shape(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert set(columns(result, "quality_rejects")) == {
        "run_id", "rule_id", "source", "row_ref", "reason", "raw_values"}
    assert len(columns(result, "gold_clinical_daily")) == 22


# --- participant integer conversion -------------------------------------------------------------


def test_non_integer_participant_value_is_withheld_for_that_field_only(tmp_path):
    bad = participant("P003", age="38.0", height="170", max_hr="abc")
    result = run(make_data(tmp_path, participants=[P001, P002, bad]), tmp_path / "out")
    assert query(result, "SELECT age, height_cm, max_heart_rate, gender, chronotype, device_id, "
                         "quality_flags FROM silver_participants WHERE participant_id = 'P003'") == [
        (None, 170, None, "male", "B", "FBT-P003-XXXXXXXX", "PARTICIPANTS_INTEGER_INVALID")]
    assert query(result, "SELECT count(*) FROM silver_participants") == [(3,)]
    assert quality(result)[("PARTICIPANTS_INTEGER_INVALID", "participants")][-3:-1] == (3, 1)
    rejects = query(result, "SELECT row_ref, reason, raw_values FROM quality_rejects "
                            "WHERE rule_id = 'PARTICIPANTS_INTEGER_INVALID'")
    assert len(rejects) == 1
    assert rejects[0][0] == "participants/participants.csv:3"
    assert rejects[0][1] == REASONS["PARTICIPANTS_INTEGER_INVALID"]
    assert json.loads(rejects[0][2]) == dict(zip(PARTICIPANT_COLUMNS, bad))
    assert query(result, "SELECT count(*) FROM gold_clinical_daily WHERE participant_id = 'P003'") == [(DAYS,)]


def test_row_with_several_bad_fields_counts_once(tmp_path):
    bad = participant("P003", age="x", height="", max_hr="9999999999")
    result = run(make_data(tmp_path, participants=[P001, P002, bad]), tmp_path / "out")
    assert quality(result)[("PARTICIPANTS_INTEGER_INVALID", "participants")][-3:-1] == (3, 1)
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         "WHERE rule_id = 'PARTICIPANTS_INTEGER_INVALID'") == [(1,)]
    assert query(result, "SELECT age, height_cm, max_heart_rate FROM silver_participants "
                         "WHERE participant_id = 'P003'") == [(None, None, None)]


def test_padded_participant_integer_is_converted_without_a_flag(tmp_path):
    padded = participant("P003", age=" 41 ", height="\t172", max_hr="180 ")
    result = run(make_data(tmp_path, participants=[P001, P002, padded]), tmp_path / "out")
    assert query(result, "SELECT age, height_cm, max_heart_rate, quality_flags "
                         "FROM silver_participants WHERE participant_id = 'P003'") == [
        (41, 172, 180, None)]
    assert quality(result)[("PARTICIPANTS_INTEGER_INVALID", "participants")][-3:-1] == (3, 0)


def test_invalid_integer_participant_still_counts_as_enrolled_for_orphan_check(tmp_path):
    bad = participant("P003", age="abc")
    survey = default_survey() + [survey_row("P003", DAY)]
    result = run(make_data(tmp_path, participants=[P001, P002, bad], survey=survey),
                 tmp_path / "out")
    assert query(result, "SELECT is_orphan FROM silver_wellness_survey "
                         "WHERE participant_id = 'P003'") == [(False,)]
    assert gold(result, "P003", WINDOW[0])["survey_present"] is True


# --- survey date handling ---------------------------------------------------------------------------


def test_padded_survey_date_is_parsed_and_not_flagged(tmp_path):
    survey = [survey_row("P001", f" {DAY} "), survey_row("P001", WINDOW[1].isoformat())]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT local_date, quality_flags FROM silver_wellness_survey "
                         "ORDER BY local_date") == [(WINDOW[0], None), (WINDOW[1], None)]
    assert gold(result, "P001", WINDOW[0])["survey_row_count"] == 1
    assert quality(result)[("SURVEY_DATE_UNPARSEABLE", "wellness_survey")][-3:-1] == (2, 0)
    assert quality(result)[("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey")][-3:-1] == (2, 0)


def test_padded_date_duplicates_the_trimmed_day(tmp_path):
    survey = [survey_row("P001", DAY), survey_row("P001", f"  {DAY}")]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_wellness_survey WHERE is_duplicate_key "
                         "AND quality_flags = 'SURVEY_DUPLICATE_PARTICIPANT_DAY'") == [(2,)]


@pytest.mark.parametrize("raw", ["20260108", "01/08/2026", "2026-02-30", "", "2026-1-8", "2026-01-08T00:00:00"])
def test_unparseable_date_is_reported_by_its_own_rule_only(tmp_path, raw):
    survey = default_survey() + [survey_row("P001", raw)]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT local_date, is_out_of_window, quality_flags "
                         "FROM silver_wellness_survey WHERE local_date IS NULL") == [
        (None, False, "SURVEY_DATE_UNPARSEABLE")]
    assert quality(result)[("SURVEY_DATE_UNPARSEABLE", "wellness_survey")][-3:-1] == (6, 1)
    assert quality(result)[("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey")][-3:-1] == (6, 0)
    rejects = query(result, "SELECT reason, raw_values FROM quality_rejects "
                            "WHERE rule_id = 'SURVEY_DATE_UNPARSEABLE'")
    assert len(rejects) == 1 and rejects[0][0] == REASONS["SURVEY_DATE_UNPARSEABLE"]
    assert json.loads(rejects[0][1])["date"] == raw
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         "WHERE rule_id = 'SURVEY_DATE_OUT_OF_WINDOW'") == [(0,)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(2 * DAYS,)]


def test_valid_out_of_window_date_is_reported_by_the_other_rule_only(tmp_path):
    survey = default_survey() + [survey_row("P001", "2026-01-07")]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT local_date, is_out_of_window, quality_flags "
                         "FROM silver_wellness_survey WHERE is_out_of_window") == [
        (date(2026, 1, 7), True, "SURVEY_DATE_OUT_OF_WINDOW")]
    assert quality(result)[("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey")][-3:-1] == (6, 1)
    assert quality(result)[("SURVEY_DATE_UNPARSEABLE", "wellness_survey")][-3:-1] == (6, 0)
    rejects = query(result, "SELECT reason FROM quality_rejects "
                            "WHERE rule_id = 'SURVEY_DATE_OUT_OF_WINDOW'")
    assert rejects == [(REASONS["SURVEY_DATE_OUT_OF_WINDOW"],)]
    assert query(result, "SELECT count(*) FROM quality_rejects "
                         "WHERE rule_id = 'SURVEY_DATE_UNPARSEABLE'") == [(0,)]


def test_no_survey_row_is_dropped_from_silver(tmp_path):
    survey = default_survey() + [
        survey_row("P001", "bad"), survey_row("P001", "2026-01-07"), survey_row("P999", DAY)]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_wellness_survey") == [(len(survey),)]


# --- gold unchanged and reruns ----------------------------------------------------------------


def test_gold_is_identical_with_clean_and_padded_inputs(tmp_path):
    clean = default_survey()
    padded = [[row[0], f" {row[1]} ", *row[2:]] for row in clean]
    first = run(make_data(tmp_path / "a", survey=clean), tmp_path / "a" / "out")
    second = run(make_data(tmp_path / "b", survey=padded), tmp_path / "b" / "out")
    sql = "SELECT * FROM gold_clinical_daily ORDER BY ALL"
    assert query(first, sql) == query(second, sql)


def test_normalized_silver_is_deterministic_across_reruns(tmp_path):
    survey = default_survey() + [survey_row("P001", " bad "), survey_row("P001", f" {DAY} ")]
    bad = participant("P003", age="abc")
    data = make_data(tmp_path, participants=[P001, P002, bad], survey=survey)
    first = run(data, tmp_path / "out")
    before = snapshot(first)
    report = first.report_path.read_bytes()
    second = run(data, tmp_path / "out")
    assert snapshot(second) == before
    assert second.report_path.read_bytes() == report
    assert second.run_id == first.run_id
