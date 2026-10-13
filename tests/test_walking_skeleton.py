"""Behavior tests for INC-002 (see specs/INC-002.md). Fixtures are synthetic."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from wearable_pipeline import PipelineError, main, run_pipeline

PARTICIPANT_COLUMNS = [
    "participant_id", "device_id", "age", "height_cm", "gender", "chronotype", "max_heart_rate",
]
SURVEY_COLUMNS = [
    "participant_id", "date", "fatigue_score", "stress_score", "readiness_score",
    "sleep_quality_score",
]
START = date(2026, 1, 8)
DAYS = 3
WINDOW = [START + timedelta(days=i) for i in range(DAYS)]

P001 = ["P001", "FBT-0001-AAAAAAAA", "38", "183", "male", "B", "167"]
P002 = ["P002", "FBT-0002-BBBBBBBB", "31", "181", "male", "A", "199"]
GOOD = ["3", "3", "8", "3"]


def survey_row(pid: str, day: str, scores: list[str] | None = None) -> list[str]:
    return [pid, day, *(scores or GOOD)]


def default_survey() -> list[list[str]]:
    rows = [survey_row("P001", d.isoformat()) for d in WINDOW]
    rows += [survey_row("P002", d.isoformat(), ["2", "4", "6", "5"]) for d in WINDOW[:2]]
    return rows


def write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def make_data(
    root: Path,
    participants: list[list[str]] | None = None,
    survey: list[list[str]] | None = None,
) -> Path:
    data = root / "data"
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
              participants if participants is not None else [P001, P002])
    write_csv(data / "health_summaries" / "wellness_survey.csv", SURVEY_COLUMNS,
              survey if survey is not None else default_survey())
    write_csv(data / "health_summaries" / "device_metadata.csv",
              ["participant_id", "device_id"], [["P001", "FBT-0001-AAAAAAAA"]])
    (data / "wearable_events" / "P001").mkdir(parents=True, exist_ok=True)
    (data / "wearable_events" / "P001" / "steps.json").write_text(
        '[{"timestamp": "2026-01-08T00:00:00", "steps": 0}]', encoding="utf-8")
    (data / "wearable_events" / "P001" / "sleep.json").write_text("[]", encoding="utf-8")
    (data / "data_dictionary.md").write_text("# dictionary\n", encoding="utf-8")
    return data


def run(data: Path, out: Path):
    return run_pipeline(data, out, study_start=START, study_days=DAYS)


def query(result, sql: str) -> list[tuple]:
    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def snapshot(result) -> dict[str, list[tuple]]:
    tables = [
        "raw_manifest", "silver_participants", "silver_wellness_survey",
        "silver_steps_minute", "quality_results", "quality_rejects", "gold_clinical_daily",
    ]
    return {name: query(result, f"SELECT * FROM {name} ORDER BY ALL") for name in tables}


def gold(result, pid: str, day: date) -> dict:
    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        cursor = connection.execute(
            "SELECT * FROM gold_clinical_daily WHERE participant_id = ? AND local_date = ?",
            [pid, day],
        )
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, f"expected exactly one gold row for {pid} {day}"
    return dict(zip(columns, rows[0]))


def quality(result) -> dict[tuple[str, str], tuple]:
    rows = query(result, "SELECT rule_id, source, handling, severity, checked_count, "
                         "failed_count, run_id FROM quality_results")
    return {(r[0], r[1]): r[2:] for r in rows}


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


REASONS = {
    "PARTICIPANTS_INTEGER_INVALID": "age, height_cm or max_heart_rate is not an integer",
    "SURVEY_DATE_UNPARSEABLE": "date is not a valid YYYY-MM-DD calendar date",
    "PARTICIPANTS_KEY_DUPLICATE": "participant_id occurs in more than one participants row",
    "SURVEY_ORPHAN_PARTICIPANT": "participant_id is not enrolled in participants.csv",
    "SURVEY_DUPLICATE_PARTICIPANT_DAY": "participant_id and date occur in more than one survey row",
    "SURVEY_SCORE_INVALID": "a score is blank, not an integer, or outside its allowed range",
    "SURVEY_DATE_OUT_OF_WINDOW": "date is outside the study window",
}

SLEEP_REASONS = {
    "SLEEP_ORPHAN_PARTICIPANT": "sleep participant_id is not enrolled in participants.csv",
    "SLEEP_DUPLICATE_PARTICIPANT_DAY": "participant_id and date occur in more than one sleep session",
    "SLEEP_DATE_UNPARSEABLE": "sleep date is not a valid YYYY-MM-DD calendar date",
    "SLEEP_DATE_OUT_OF_WINDOW": "sleep date is outside the study window",
    "SLEEP_SESSION_FIELD_INVALID": "sleep_onset, sleep_end, efficiency_pct or restlessness is missing or invalid",
    "SLEEP_STAGE_INVALID": "a sleep stage has an unknown name, an invalid duration or an unparseable time",
    "SLEEP_STAGE_DURATION_MISMATCH": "a sleep stage duration differs from its timestamp interval",
    "SLEEP_STAGE_GAP_OR_OVERLAP": "consecutive sleep stages have a gap or overlap",
    "SLEEP_SPAN_OUTLIER": "sleep session span is longer than 12 hours",
    "SLEEP_FINAL_STAGE_MISALIGNED": "final sleep stage does not start at sleep_end",
    "SLEEP_EFFICIENCY_MISMATCH": "supplied efficiency_pct differs from the derived efficiency by more than 1 point",
    "SLEEP_ONSET_NOT_MINUTE_ALIGNED": "sleep_onset is not aligned to a whole minute",
}

SLEEP_HANDLING = {
    "SLEEP_ORPHAN_PARTICIPANT": ("exclude_from_output", "medium"),
    "SLEEP_DUPLICATE_PARTICIPANT_DAY": ("withhold_field", "high"),
    "SLEEP_DATE_UNPARSEABLE": ("exclude_from_output", "medium"),
    "SLEEP_DATE_OUT_OF_WINDOW": ("exclude_from_output", "medium"),
    "SLEEP_SESSION_FIELD_INVALID": ("withhold_field", "medium"),
    "SLEEP_STAGE_INVALID": ("withhold_field", "medium"),
    "SLEEP_STAGE_DURATION_MISMATCH": ("flag", "medium"),
    "SLEEP_STAGE_GAP_OR_OVERLAP": ("flag", "medium"),
    "SLEEP_SPAN_OUTLIER": ("flag", "medium"),
    "SLEEP_FINAL_STAGE_MISALIGNED": ("flag", "medium"),
    "SLEEP_EFFICIENCY_MISMATCH": ("flag", "medium"),
    "SLEEP_ONSET_NOT_MINUTE_ALIGNED": ("flag", "medium"),
}

STEPS_REASONS = {
    "STEPS_ORPHAN_PARTICIPANT": "steps participant_id is not enrolled in participants.csv",
    "STEPS_PARTICIPANT_FOLDER_MISMATCH": "steps participant_id differs from its participant folder",
    "STEPS_TIMESTAMP_UNPARSEABLE": "steps timestamp is missing or not a valid ISO 8601 timestamp",
    "STEPS_TIMESTAMP_OUT_OF_WINDOW": "steps timestamp is outside the study window",
    "STEPS_VALUE_INVALID": "steps is missing, not an integer, or outside the 32-bit integer range",
    "STEPS_NEGATIVE": "steps is negative",
    "STEPS_UNIT_CONVERTED": "steps_per_hour value converted to steps per minute",
    "STEPS_UNIT_UNSUPPORTED": (
        "steps _unit is not steps_per_hour or the hourly value is not divisible by 60"),
    "STEPS_DUPLICATE_MINUTE_CONFLICT": "records for one participant-minute have different values",
    "STEPS_DUPLICATE_MINUTE_EXACT": "records for one participant-minute have identical values",
    "STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED": "steps timestamp is not aligned to a whole minute",
}

STEPS_HANDLING = {
    "STEPS_ORPHAN_PARTICIPANT": ("exclude_from_output", "medium"),
    "STEPS_PARTICIPANT_FOLDER_MISMATCH": ("exclude_from_output", "medium"),
    "STEPS_TIMESTAMP_UNPARSEABLE": ("exclude_from_output", "medium"),
    "STEPS_TIMESTAMP_OUT_OF_WINDOW": ("exclude_from_output", "medium"),
    "STEPS_VALUE_INVALID": ("withhold_field", "medium"),
    "STEPS_NEGATIVE": ("withhold_field", "high"),
    "STEPS_UNIT_CONVERTED": ("convert", "low"),
    "STEPS_UNIT_UNSUPPORTED": ("withhold_field", "high"),
    "STEPS_DUPLICATE_MINUTE_CONFLICT": ("withhold_field", "high"),
    "STEPS_DUPLICATE_MINUTE_EXACT": ("flag", "medium"),
    "STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED": ("flag", "medium"),
}

HR_REASONS = {
    "HR_ORPHAN_PARTICIPANT": "heart rate participant_id is not enrolled in participants.csv",
    "HR_PARTICIPANT_FOLDER_MISMATCH": "heart rate participant_id differs from its participant folder",
    "HR_TIMESTAMP_UNPARSEABLE": (
        "heart rate timestamp is missing or not a valid ISO 8601 timestamp"),
    "HR_TIMESTAMP_OUT_OF_WINDOW": "heart rate timestamp is outside the study window",
    "HR_VALUE_INVALID": (
        "heart_rate_bpm is missing, not an integer, or outside the 32-bit integer range"),
    "HR_VALUE_OUT_OF_RANGE": "heart_rate_bpm is outside the range 40 to 200",
    "HR_STUCK_SENSOR_RUN": "heart_rate_bpm repeats for at least 60 consecutive minutes",
    "HR_TIMESTAMP_CONVERTED": "heart rate timestamp with Z or an offset converted to UTC-05:00",
    "HR_DUPLICATE_MINUTE_CONFLICT": "records for one participant-minute have different values",
    "HR_DUPLICATE_MINUTE_EXACT": "records for one participant-minute have identical values",
    "HR_TIMESTAMP_NOT_MINUTE_ALIGNED": "heart rate timestamp is not aligned to a whole minute",
}

HR_HANDLING = {
    "HR_ORPHAN_PARTICIPANT": ("exclude_from_output", "medium"),
    "HR_PARTICIPANT_FOLDER_MISMATCH": ("exclude_from_output", "medium"),
    "HR_TIMESTAMP_UNPARSEABLE": ("exclude_from_output", "medium"),
    "HR_TIMESTAMP_OUT_OF_WINDOW": ("exclude_from_output", "medium"),
    "HR_VALUE_INVALID": ("withhold_field", "medium"),
    "HR_VALUE_OUT_OF_RANGE": ("withhold_field", "high"),
    "HR_STUCK_SENSOR_RUN": ("withhold_field", "medium"),
    "HR_TIMESTAMP_CONVERTED": ("convert", "low"),
    "HR_DUPLICATE_MINUTE_CONFLICT": ("withhold_field", "high"),
    "HR_DUPLICATE_MINUTE_EXACT": ("flag", "medium"),
    "HR_TIMESTAMP_NOT_MINUTE_ALIGNED": ("flag", "medium"),
}

RULES = [
    ("PARTICIPANTS_INTEGER_INVALID", "participants"),
    ("PARTICIPANTS_KEY_DUPLICATE", "participants"),
    ("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey"),
    ("SURVEY_DATE_UNPARSEABLE", "wellness_survey"),
    ("SURVEY_DUPLICATE_PARTICIPANT_DAY", "wellness_survey"),
    ("SURVEY_ORPHAN_PARTICIPANT", "wellness_survey"),
    ("SURVEY_SCORE_INVALID", "wellness_survey"),
] + [(rule, "sleep") for rule in SLEEP_REASONS] + [(rule, "steps") for rule in STEPS_REASONS] + [
    (rule, "heart_rate") for rule in HR_REASONS]


# --- output locations, manifest and run id -------------------------------------------


def test_outputs_are_written_to_the_documented_locations(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    result = run(data, out)
    assert result.database_path == out / "wearable.duckdb"
    assert result.report_path == out / "quality" / "report.md"
    assert result.database_path.is_file()
    assert result.report_path.is_file()


def test_manifest_has_one_row_per_raw_file_with_checksums(tmp_path):
    data = make_data(tmp_path)
    result = run(data, tmp_path / "out")
    expected = []
    sources = {
        "participants/participants.csv": ("participants", 2),
        "health_summaries/wellness_survey.csv": ("wellness_survey", 5),
        "health_summaries/device_metadata.csv": ("device_metadata", 1),
        "wearable_events/P001/steps.json": ("steps", None),
        "wearable_events/P001/sleep.json": ("sleep", None),
        "data_dictionary.md": ("documentation", None),
    }
    for relative, (source, count) in sources.items():
        content = (data / relative).read_bytes()
        expected.append((relative, source, len(content), hashlib.sha256(content).hexdigest(), count))
    assert query(result, "SELECT relative_path, source, size_bytes, sha256, record_count "
                         "FROM raw_manifest ORDER BY relative_path") == sorted(expected)


def test_run_id_is_derived_from_manifest_checksums(tmp_path):
    data = make_data(tmp_path)
    result = run(data, tmp_path / "out")
    lines = "".join(
        f"{path}\t{digest}\n"
        for path, digest in sorted(tree_hashes(data).items())
    )
    assert result.run_id == hashlib.sha256(lines.encode("utf-8")).hexdigest()[:16]


def test_run_id_changes_when_an_input_changes(tmp_path):
    first = run(make_data(tmp_path / "a"), tmp_path / "out_a")
    changed = default_survey()
    changed[0][2] = "4"
    second = run(make_data(tmp_path / "b", survey=changed), tmp_path / "out_b")
    assert first.run_id != second.run_id


# --- silver ---------------------------------------------------------------------------


def test_silver_participants_keeps_every_row_with_typed_values(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert query(result, "SELECT participant_id, device_id, age, height_cm, "
                         "gender, chronotype, max_heart_rate, is_duplicate_key, quality_flags "
                         "FROM silver_participants ORDER BY participant_id") == [
        ("P001", "FBT-0001-AAAAAAAA", 38, 183, "male", "B", 167, False, None),
        ("P002", "FBT-0002-BBBBBBBB", 31, 181, "male", "A", 199, False, None),
    ]


def test_silver_survey_keeps_every_row_normalized(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    rows = query(result, "SELECT participant_id, local_date, fatigue_score, readiness_score, "
                         "is_orphan, is_duplicate_key, is_out_of_window, quality_flags "
                         "FROM silver_wellness_survey ORDER BY participant_id, local_date")
    assert len(rows) == 5
    assert rows[0] == ("P001", date(2026, 1, 8), 3, 8, False, False, False, None)
    assert rows[3] == ("P002", date(2026, 1, 8), 2, 6, False, False, False, None)


def test_orphan_participant_is_kept_in_silver_and_absent_from_gold(tmp_path):
    survey = default_survey() + [survey_row("P999", d.isoformat()) for d in WINDOW]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_wellness_survey WHERE participant_id = 'P999' "
                         "AND is_orphan AND quality_flags = 'SURVEY_ORPHAN_PARTICIPANT'") == [(3,)]
    assert query(result, "SELECT count(*) FROM silver_wellness_survey WHERE is_orphan") == [(3,)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily WHERE participant_id = 'P999'") == [(0,)]


def test_duplicate_participant_key_rows_are_all_kept_and_flagged(tmp_path):
    duplicate = [P001[:1] + ["FBT-0001-ZZZZZZZZ"] + P001[2:]]
    result = run(make_data(tmp_path, participants=[P001, P002] + duplicate), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_participants") == [(3,)]
    assert query(result, "SELECT device_id FROM silver_participants WHERE is_duplicate_key "
                         "AND quality_flags = 'PARTICIPANTS_KEY_DUPLICATE' "
                         "ORDER BY device_id") == [("FBT-0001-AAAAAAAA",), ("FBT-0001-ZZZZZZZZ",)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(2 * DAYS,)]
    assert quality(result)[("PARTICIPANTS_KEY_DUPLICATE", "participants")][-3:-1] == (3, 2)


# --- gold grain, values and quality handling -----------------------------------------


def test_gold_has_one_row_per_enrolled_participant_per_study_day(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    keys = query(result, "SELECT participant_id, local_date FROM gold_clinical_daily")
    assert sorted(keys) == sorted(itertools.product(["P001", "P002"], WINDOW))
    assert len(keys) == len(set(keys))


def test_gold_values_and_presence_flags(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert gold(result, "P001", WINDOW[0]) == {
        "participant_id": "P001", "local_date": WINDOW[0], "fatigue_score": 3, "stress_score": 3,
        "readiness_score": 8, "sleep_quality_score": 3, "survey_present": True,
        "survey_row_count": 1, "survey_valid_score_count": 4, "survey_quality_flags": None,
        "total_sleep_min": None, "deep_sleep_pct": None, "sleep_efficiency_pct": None,
        "sleep_session_count": 0, "sleep_present": False, "sleep_quality_flags": None,
        "total_steps": 0, "steps_valid_minute_count": 1, "minutes_rest_to_moderate": 1,
        "minutes_burst": 0, "steps_present": True, "steps_quality_flags": None,
        "hr_mean_bpm": None, "hr_min_bpm": None, "hr_max_bpm": None,
        "hr_valid_minute_count": 0, "hr_present": False, "hr_quality_flags": None,
    }
    assert gold(result, "P002", WINDOW[2]) == {
        "participant_id": "P002", "local_date": WINDOW[2], "fatigue_score": None,
        "stress_score": None, "readiness_score": None, "sleep_quality_score": None,
        "survey_present": False, "survey_row_count": 0, "survey_valid_score_count": 0,
        "survey_quality_flags": None,
        "total_sleep_min": None, "deep_sleep_pct": None, "sleep_efficiency_pct": None,
        "sleep_session_count": 0, "sleep_present": False, "sleep_quality_flags": None,
        "total_steps": None, "steps_valid_minute_count": 0, "minutes_rest_to_moderate": 0,
        "minutes_burst": 0, "steps_present": False, "steps_quality_flags": None,
        "hr_mean_bpm": None, "hr_min_bpm": None, "hr_max_bpm": None,
        "hr_valid_minute_count": 0, "hr_present": False, "hr_quality_flags": None,
    }


def test_invalid_score_is_withheld_per_field_and_raw_value_preserved(tmp_path):
    survey = default_survey()
    survey[1] = survey_row("P001", WINDOW[1].isoformat(), ["9", "3", "5", "3"])
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    row = gold(result, "P001", WINDOW[1])
    assert (row["fatigue_score"], row["stress_score"], row["readiness_score"],
            row["sleep_quality_score"]) == (None, 3, 5, 3)
    assert row["survey_valid_score_count"] == 3
    assert row["survey_present"] is True
    assert row["survey_quality_flags"] == "SURVEY_SCORE_INVALID"
    assert query(result, "SELECT fatigue_score FROM silver_wellness_survey "
                         f"WHERE participant_id = 'P001' AND local_date = '{WINDOW[1]}'") == [(None,)]
    rejects = query(result, "SELECT raw_values, reason FROM quality_rejects "
                            "WHERE rule_id = 'SURVEY_SCORE_INVALID'")
    assert len(rejects) == 1 and rejects[0][1] == REASONS["SURVEY_SCORE_INVALID"]
    assert json.loads(rejects[0][0]) == dict(zip(SURVEY_COLUMNS, survey[1]))


@pytest.mark.parametrize(
    ("column", "value", "valid"),
    [
        (2, "1", True), (2, "5", True), (2, "0", False), (2, "6", False),
        (3, "1", True), (3, "5", True), (3, "0", False), (3, "6", False),
        (4, "0", True), (4, "10", True), (4, "-1", False), (4, "11", False),
        (5, "1", True), (5, "5", True), (5, "0", False), (5, "6", False),
        (2, "", False), (3, "abc", False), (4, "7.5", False), (5, " 4 ", True),
    ],
)
def test_score_range_boundaries(tmp_path, column, value, valid):
    row = survey_row("P001", WINDOW[0].isoformat())
    row[column] = value
    result = run(make_data(tmp_path, survey=[row]), tmp_path / "out")
    failed = quality(result)[("SURVEY_SCORE_INVALID", "wellness_survey")][-2]
    assert failed == (0 if valid else 1)
    score_columns = ["fatigue_score", "stress_score", "readiness_score", "sleep_quality_score"]
    stored = gold(result, "P001", WINDOW[0])[score_columns[column - 2]]
    assert (stored is not None) is valid
    if valid:
        assert stored == int(value)


def test_exact_duplicate_does_not_inflate_and_conflict_withholds_only_that_score(tmp_path):
    survey = default_survey()
    survey.append(survey_row("P001", WINDOW[0].isoformat()))
    survey.append(survey_row("P001", WINDOW[1].isoformat(), ["3", "4", "8", "3"]))
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    exact = gold(result, "P001", WINDOW[0])
    assert (exact["fatigue_score"], exact["stress_score"], exact["readiness_score"],
            exact["sleep_quality_score"]) == (3, 3, 8, 3)
    assert exact["survey_row_count"] == 2 and exact["survey_valid_score_count"] == 4
    assert exact["survey_quality_flags"] == "SURVEY_DUPLICATE_PARTICIPANT_DAY"
    conflict = gold(result, "P001", WINDOW[1])
    assert (conflict["fatigue_score"], conflict["stress_score"], conflict["readiness_score"],
            conflict["sleep_quality_score"]) == (3, None, 8, 3)
    assert conflict["survey_valid_score_count"] == 3 and conflict["survey_row_count"] == 2
    assert query(result, "SELECT count(*) FROM silver_wellness_survey") == [(len(survey),)]
    assert query(result, "SELECT count(*) FROM silver_wellness_survey WHERE is_duplicate_key") == [(4,)]
    assert quality(result)[("SURVEY_DUPLICATE_PARTICIPANT_DAY", "wellness_survey")][-3:-1] == (len(survey), 4)


def test_out_of_window_and_unparseable_dates_are_flagged_and_kept_out_of_gold(tmp_path):
    survey = default_survey() + [
        survey_row("P001", "2026-01-07"),
        survey_row("P001", (WINDOW[-1] + timedelta(days=1)).isoformat()),
        survey_row("P001", "01/08/2026"),
    ]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(2 * DAYS,)]
    assert query(result, "SELECT local_date FROM silver_wellness_survey "
                         "WHERE is_out_of_window ORDER BY local_date") == [
        (date(2026, 1, 7),), (date(2026, 1, 11),),
    ]
    assert query(result, "SELECT count(*) FROM silver_wellness_survey WHERE local_date IS NULL "
                         "AND NOT is_out_of_window AND quality_flags = 'SURVEY_DATE_UNPARSEABLE'") == [(1,)]
    assert quality(result)[("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey")][-3:-1] == (8, 2)
    assert quality(result)[("SURVEY_DATE_UNPARSEABLE", "wellness_survey")][-3:-1] == (8, 1)
    assert gold(result, "P001", WINDOW[0])["survey_row_count"] == 1
    assert query(result, "SELECT rule_id, count(*) FROM quality_rejects WHERE rule_id LIKE "
                         "'SURVEY_DATE_%' GROUP BY rule_id ORDER BY rule_id") == [
        ("SURVEY_DATE_OUT_OF_WINDOW", 2), ("SURVEY_DATE_UNPARSEABLE", 1)]


def test_window_boundaries_are_inclusive(tmp_path):
    survey = [survey_row("P001", WINDOW[0].isoformat()), survey_row("P001", WINDOW[-1].isoformat())]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert gold(result, "P001", WINDOW[0])["survey_present"] is True
    assert gold(result, "P001", WINDOW[-1])["survey_present"] is True
    assert quality(result)[("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey")][-2] == 0


# --- quality outputs ------------------------------------------------------------------


def test_every_rule_reports_one_row_even_with_zero_failures(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    results = quality(result)
    assert sorted(results) == sorted(RULES)
    assert results[("SURVEY_ORPHAN_PARTICIPANT", "wellness_survey")][:2] == ("exclude_from_output", "medium")
    assert results[("SURVEY_SCORE_INVALID", "wellness_survey")][:2] == ("withhold_field", "medium")
    assert results[("SURVEY_DUPLICATE_PARTICIPANT_DAY", "wellness_survey")][:2] == ("withhold_field", "high")
    assert results[("SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey")][:2] == ("exclude_from_output", "medium")
    assert results[("PARTICIPANTS_KEY_DUPLICATE", "participants")][:2] == ("flag", "high")
    assert results[("PARTICIPANTS_INTEGER_INVALID", "participants")][:2] == ("withhold_field", "medium")
    assert results[("SURVEY_DATE_UNPARSEABLE", "wellness_survey")][:2] == ("exclude_from_output", "medium")
    for rule, handling in SLEEP_HANDLING.items():
        assert results[(rule, "sleep")][:2] == handling
    for rule, handling in STEPS_HANDLING.items():
        assert results[(rule, "steps")][:2] == handling
    for rule, handling in HR_HANDLING.items():
        assert results[(rule, "heart_rate")][:2] == handling
    for key, value in results.items():
        expected_checked = {"participants": 2, "wellness_survey": 5, "sleep": 0, "steps": 1,
                            "heart_rate": 0}[key[1]]
        assert value[-3:] == (expected_checked, 0, result.run_id)
    assert query(result, "SELECT count(*) FROM quality_rejects") == [(0,)]


def test_quality_results_count_defects_and_rejects_carry_raw_values(tmp_path):
    survey = default_survey() + [survey_row("P999", WINDOW[0].isoformat()),
                                 survey_row("P999", WINDOW[1].isoformat())]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    assert quality(result)[("SURVEY_ORPHAN_PARTICIPANT", "wellness_survey")][-3:-1] == (7, 2)
    rejects = query(result, "SELECT row_ref, source, raw_values FROM quality_rejects "
                            "WHERE rule_id = 'SURVEY_ORPHAN_PARTICIPANT' ORDER BY row_ref")
    assert [r[0] for r in rejects] == [
        "health_summaries/wellness_survey.csv:6", "health_summaries/wellness_survey.csv:7"]
    assert {r[1] for r in rejects} == {"wellness_survey"}
    assert json.loads(rejects[0][2]) == dict(zip(SURVEY_COLUMNS, survey[5]))
    keys = query(result, "SELECT run_id, rule_id, row_ref FROM quality_rejects")
    assert len(keys) == len(set(keys))


def test_report_lists_run_id_and_one_row_per_rule(tmp_path):
    survey = default_survey() + [survey_row("P999", WINDOW[0].isoformat())]
    result = run(make_data(tmp_path, survey=survey), tmp_path / "out")
    text = result.report_path.read_text(encoding="utf-8")
    assert text.endswith("\n") and "\r" not in text
    assert f"Run id: {result.run_id}" in text
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines() if line.startswith("| ") and "rule_id" not in line
        and not line.startswith("| ---")
    ]
    assert rows == [
        ["HR_DUPLICATE_MINUTE_CONFLICT", "heart_rate", "withhold_field", "high", "0", "0"],
        ["HR_DUPLICATE_MINUTE_EXACT", "heart_rate", "flag", "medium", "0", "0"],
        ["HR_ORPHAN_PARTICIPANT", "heart_rate", "exclude_from_output", "medium", "0", "0"],
        ["HR_PARTICIPANT_FOLDER_MISMATCH", "heart_rate", "exclude_from_output", "medium", "0", "0"],
        ["HR_STUCK_SENSOR_RUN", "heart_rate", "withhold_field", "medium", "0", "0"],
        ["HR_TIMESTAMP_CONVERTED", "heart_rate", "convert", "low", "0", "0"],
        ["HR_TIMESTAMP_NOT_MINUTE_ALIGNED", "heart_rate", "flag", "medium", "0", "0"],
        ["HR_TIMESTAMP_OUT_OF_WINDOW", "heart_rate", "exclude_from_output", "medium", "0", "0"],
        ["HR_TIMESTAMP_UNPARSEABLE", "heart_rate", "exclude_from_output", "medium", "0", "0"],
        ["HR_VALUE_INVALID", "heart_rate", "withhold_field", "medium", "0", "0"],
        ["HR_VALUE_OUT_OF_RANGE", "heart_rate", "withhold_field", "high", "0", "0"],
        ["PARTICIPANTS_INTEGER_INVALID", "participants", "withhold_field", "medium", "2", "0"],
        ["PARTICIPANTS_KEY_DUPLICATE", "participants", "flag", "high", "2", "0"],
        ["SLEEP_DATE_OUT_OF_WINDOW", "sleep", "exclude_from_output", "medium", "0", "0"],
        ["SLEEP_DATE_UNPARSEABLE", "sleep", "exclude_from_output", "medium", "0", "0"],
        ["SLEEP_DUPLICATE_PARTICIPANT_DAY", "sleep", "withhold_field", "high", "0", "0"],
        ["SLEEP_EFFICIENCY_MISMATCH", "sleep", "flag", "medium", "0", "0"],
        ["SLEEP_FINAL_STAGE_MISALIGNED", "sleep", "flag", "medium", "0", "0"],
        ["SLEEP_ONSET_NOT_MINUTE_ALIGNED", "sleep", "flag", "medium", "0", "0"],
        ["SLEEP_ORPHAN_PARTICIPANT", "sleep", "exclude_from_output", "medium", "0", "0"],
        ["SLEEP_SESSION_FIELD_INVALID", "sleep", "withhold_field", "medium", "0", "0"],
        ["SLEEP_SPAN_OUTLIER", "sleep", "flag", "medium", "0", "0"],
        ["SLEEP_STAGE_DURATION_MISMATCH", "sleep", "flag", "medium", "0", "0"],
        ["SLEEP_STAGE_GAP_OR_OVERLAP", "sleep", "flag", "medium", "0", "0"],
        ["SLEEP_STAGE_INVALID", "sleep", "withhold_field", "medium", "0", "0"],
        ["STEPS_DUPLICATE_MINUTE_CONFLICT", "steps", "withhold_field", "high", "1", "0"],
        ["STEPS_DUPLICATE_MINUTE_EXACT", "steps", "flag", "medium", "1", "0"],
        ["STEPS_NEGATIVE", "steps", "withhold_field", "high", "1", "0"],
        ["STEPS_ORPHAN_PARTICIPANT", "steps", "exclude_from_output", "medium", "1", "0"],
        ["STEPS_PARTICIPANT_FOLDER_MISMATCH", "steps", "exclude_from_output", "medium", "1", "0"],
        ["STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED", "steps", "flag", "medium", "1", "0"],
        ["STEPS_TIMESTAMP_OUT_OF_WINDOW", "steps", "exclude_from_output", "medium", "1", "0"],
        ["STEPS_TIMESTAMP_UNPARSEABLE", "steps", "exclude_from_output", "medium", "1", "0"],
        ["STEPS_UNIT_CONVERTED", "steps", "convert", "low", "1", "0"],
        ["STEPS_UNIT_UNSUPPORTED", "steps", "withhold_field", "high", "1", "0"],
        ["STEPS_VALUE_INVALID", "steps", "withhold_field", "medium", "1", "0"],
        ["SURVEY_DATE_OUT_OF_WINDOW", "wellness_survey", "exclude_from_output", "medium", "6", "0"],
        ["SURVEY_DATE_UNPARSEABLE", "wellness_survey", "exclude_from_output", "medium", "6", "0"],
        ["SURVEY_DUPLICATE_PARTICIPANT_DAY", "wellness_survey", "withhold_field", "high", "6", "0"],
        ["SURVEY_ORPHAN_PARTICIPANT", "wellness_survey", "exclude_from_output", "medium", "6", "1"],
        ["SURVEY_SCORE_INVALID", "wellness_survey", "withhold_field", "medium", "6", "0"],
    ]
    assert "| rule_id | source | handling | severity | checked | failed |" in text


# --- idempotency, MERGE and determinism ------------------------------------------------


def test_rerun_on_unchanged_inputs_is_identical(tmp_path):
    data = make_data(tmp_path, survey=default_survey() + [survey_row("P999", WINDOW[0].isoformat())])
    out = tmp_path / "out"
    first = run(data, out)
    first_tables = snapshot(first)
    first_report = first.report_path.read_bytes()
    second = run(data, out)
    assert second.run_id == first.run_id
    assert snapshot(second) == first_tables
    assert second.report_path.read_bytes() == first_report
    keys = query(second, "SELECT participant_id, local_date FROM gold_clinical_daily")
    assert len(keys) == len(set(keys)) == 2 * DAYS


def test_independent_runs_on_the_same_inputs_give_identical_tables(tmp_path):
    data = make_data(tmp_path)
    first = run(data, tmp_path / "out_1")
    second = run(data, tmp_path / "out_2")
    assert snapshot(first) == snapshot(second)
    assert first.report_path.read_bytes() == second.report_path.read_bytes()


def test_changed_survey_value_updates_exactly_one_gold_row(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before = {
        (r[0], r[1]): r for r in query(first, "SELECT * FROM gold_clinical_daily")
    }
    changed = default_survey()
    changed[1][3] = "5"
    make_data(tmp_path, survey=changed)
    second = run(data, out)
    after = {(r[0], r[1]): r for r in query(second, "SELECT * FROM gold_clinical_daily")}
    assert second.run_id != first.run_id
    assert set(after) == set(before)
    differing = [key for key in after if after[key] != before[key]]
    assert differing == [("P001", WINDOW[1])]
    assert gold(second, "P001", WINDOW[1])["stress_score"] == 5


def test_merge_inserts_new_keys_and_leaves_existing_rows_unchanged(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before = {(r[0], r[1]): r for r in query(first, "SELECT * FROM gold_clinical_daily")}
    p003 = ["P003", "FBT-0003-CCCCCCCC", "29", "170", "female", "A", "170"]
    make_data(tmp_path, participants=[P001, P002, p003],
              survey=default_survey() + [survey_row("P003", WINDOW[0].isoformat(), ["1", "2", "3", "4"])])
    second = run(data, out)
    after = {(r[0], r[1]): r for r in query(second, "SELECT * FROM gold_clinical_daily")}
    assert len(after) == 3 * DAYS
    assert all(after[key] == before[key] for key in before)
    assert gold(second, "P003", WINDOW[0])["fatigue_score"] == 1


def test_gold_rows_no_longer_produced_are_retained_while_other_layers_rebuild(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before_p002 = query(first, "SELECT * FROM gold_clinical_daily WHERE participant_id = 'P002' "
                               "ORDER BY local_date")
    make_data(tmp_path, participants=[P001],
              survey=[r for r in default_survey() if r[0] == "P001"])
    second = run(data, out)
    assert query(second, "SELECT * FROM gold_clinical_daily WHERE participant_id = 'P002' "
                         "ORDER BY local_date") == before_p002
    assert query(second, "SELECT count(*) FROM silver_participants") == [(1,)]
    assert query(second, "SELECT count(*) FROM silver_wellness_survey") == [(3,)]
    assert query(second, "SELECT count(*) FROM raw_manifest") == [(6,)]
    assert {row[0] for row in query(second, "SELECT run_id FROM quality_results")} == {second.run_id}


# --- raw immutability and failure contract ---------------------------------------------


def test_raw_files_are_unchanged_and_nothing_is_created_under_data(tmp_path):
    data = make_data(tmp_path, survey=default_survey() + [survey_row("P999", WINDOW[0].isoformat())])
    before = tree_hashes(data)
    run(data, tmp_path / "out")
    run(data, tmp_path / "out")
    assert tree_hashes(data) == before


def test_cli_returns_zero_on_defective_data_with_default_window(tmp_path):
    survey = [survey_row("P001", "2026-01-08", ["9", "x", "", "3"]),
              survey_row("P999", "2026-01-09"), survey_row("P001", "2026-01-08")]
    data = make_data(tmp_path, survey=survey)
    out = tmp_path / "out"
    assert main(["run", "--data-dir", str(data), "--output-dir", str(out)]) == 0
    connection = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM gold_clinical_daily").fetchone() == (2 * 28,)
    finally:
        connection.close()
    assert (out / "quality" / "report.md").is_file()


def test_cli_returns_one_when_a_required_input_is_missing(tmp_path, capsys):
    data = make_data(tmp_path)
    (data / "health_summaries" / "wellness_survey.csv").unlink()
    assert main(["run", "--data-dir", str(data), "--output-dir", str(tmp_path / "out")]) == 1
    assert capsys.readouterr().err.strip()


def test_cli_returns_one_for_missing_data_directory(tmp_path):
    assert main(["run", "--data-dir", str(tmp_path / "absent"),
                 "--output-dir", str(tmp_path / "out")]) == 1


def test_cli_returns_one_when_a_required_column_is_missing(tmp_path):
    data = make_data(tmp_path)
    write_csv(data / "health_summaries" / "wellness_survey.csv",
              SURVEY_COLUMNS[:-1], [row[:-1] for row in default_survey()])
    assert main(["run", "--data-dir", str(data), "--output-dir", str(tmp_path / "out")]) == 1


def test_pipeline_error_leaves_existing_tables_unchanged(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before = snapshot(first)
    (data / "participants" / "participants.csv").unlink()
    with pytest.raises(PipelineError):
        run(data, out)
    assert snapshot(first) == before


def test_pipeline_error_for_non_positive_study_days(tmp_path):
    data = make_data(tmp_path)
    with pytest.raises(PipelineError):
        run_pipeline(data, tmp_path / "out", study_start=START, study_days=0)


def test_every_rule_carries_its_exact_reason_text(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    rows = query(result, "SELECT rule_id, reason FROM quality_results")
    assert dict(rows) == {**REASONS, **SLEEP_REASONS, **STEPS_REASONS, **HR_REASONS}
    assert len(rows) == len(REASONS) + len(SLEEP_REASONS) + len(STEPS_REASONS) + len(HR_REASONS)
    assert len(rows) == 41


def test_unrecognised_files_are_catalogued_as_unclassified(tmp_path):
    data = make_data(tmp_path)
    (data / "notes.txt").write_text("extra", encoding="utf-8")
    result = run(data, tmp_path / "out")
    assert query(result, "SELECT source, record_count FROM raw_manifest "
                         "WHERE relative_path = 'notes.txt'") == [("unclassified", None)]


def test_report_states_the_manifest_file_count(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    assert "Manifest files: 6" in result.report_path.read_text(encoding="utf-8").splitlines()


def test_gold_schema_types_and_primary_key(tmp_path):
    result = run(make_data(tmp_path), tmp_path / "out")
    types = dict(query(result, "SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_name = 'gold_clinical_daily'"))
    expected = {
        "participant_id": "VARCHAR", "local_date": "DATE", "fatigue_score": "INTEGER",
        "stress_score": "INTEGER", "readiness_score": "INTEGER",
        "sleep_quality_score": "INTEGER", "survey_present": "BOOLEAN",
        "survey_row_count": "INTEGER", "survey_valid_score_count": "INTEGER",
        "survey_quality_flags": "VARCHAR",
        "total_sleep_min": "INTEGER", "deep_sleep_pct": "DOUBLE",
        "sleep_efficiency_pct": "DOUBLE", "sleep_session_count": "INTEGER",
        "sleep_present": "BOOLEAN", "sleep_quality_flags": "VARCHAR",
        "total_steps": "BIGINT", "steps_valid_minute_count": "INTEGER",
        "minutes_rest_to_moderate": "INTEGER", "minutes_burst": "INTEGER",
        "steps_present": "BOOLEAN", "steps_quality_flags": "VARCHAR",
        "hr_mean_bpm": "DOUBLE", "hr_min_bpm": "INTEGER", "hr_max_bpm": "INTEGER",
        "hr_valid_minute_count": "INTEGER", "hr_present": "BOOLEAN",
        "hr_quality_flags": "VARCHAR",
    }
    assert types == expected
    keys = query(result, "SELECT constraint_column_names FROM duckdb_constraints() "
                         "WHERE table_name = 'gold_clinical_daily' "
                         "AND constraint_type = 'PRIMARY KEY'")
    assert [sorted(row[0]) for row in keys] == [["local_date", "participant_id"]]


def test_every_reject_carries_its_rules_reason_text(tmp_path):
    survey = default_survey() + [
        survey_row("P999", WINDOW[0].isoformat()),
        survey_row("P001", WINDOW[0].isoformat()),
        survey_row("P001", "2025-01-01"),
        survey_row("P002", WINDOW[2].isoformat(), ["9", "3", "5", "3"]),
        survey_row("P001", "20260108"),
    ]
    bad_age = ["P003", "FBT-0003-CCCCCCCC", "abc", "170", "female", "A", "180"]
    result = run(make_data(tmp_path, participants=[P001, P002, P002, bad_age], survey=survey),
                 tmp_path / "out")
    rejects = query(result, "SELECT DISTINCT rule_id, reason FROM quality_rejects")
    assert dict(rejects) == REASONS
    assert len(rejects) == len(REASONS)
