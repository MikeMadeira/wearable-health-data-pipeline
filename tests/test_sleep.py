"""Behavior tests for INC-003 (see specs/INC-003.md). Fixtures are synthetic."""

from __future__ import annotations

import copy
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tests.test_walking_skeleton import (
    SLEEP_HANDLING, SLEEP_REASONS, WINDOW, gold, make_data, query, quality, run,
    snapshot, tree_hashes,
)
from wearable_pipeline import PipelineError, main
from wearable_pipeline.normalize import parse_local_timestamp
from wearable_pipeline.sleep import (
    SLEEP_RULES, SleepDay, SleepLayer, SleepMetrics, build_sleep_layer, compute_sleep_metrics,
)

SLEEP_PATH = "wearable_events/P001/sleep.json"
SLEEP_PATH_2 = "wearable_events/P002/sleep.json"
DAY1 = WINDOW[0]
DAY2 = WINDOW[1]
DEFAULT_STAGES = [("light", 42), ("deep", 81), ("rem", 53), ("light", 90), ("awake", 19)]


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def session(
    pid: str = "P001",
    day: date = DAY1,
    stages: list[tuple[str, int]] | None = None,
    onset: datetime | None = None,
    efficiency: float | None = None,
) -> dict:
    """A clean session: contiguous stages, sleep_end at the final stage start."""
    stages = stages if stages is not None else DEFAULT_STAGES
    start = (onset or datetime.combine(day, datetime.min.time()) - timedelta(minutes=56))
    out = []
    cursor = start
    for name, minutes in stages:
        end = cursor + timedelta(minutes=minutes)
        out.append({"stage": name, "start_time": iso(cursor), "end_time": iso(end),
                    "duration_min": minutes})
        cursor = end
    final_start = datetime.fromisoformat(out[-1]["start_time"]) if out else start
    non_awake = sum(m for n, m in stages if n != "awake")
    total = sum(m for _, m in stages)
    return {
        "participant_id": pid, "date": day.isoformat(), "sleep_onset": iso(start),
        "sleep_end": iso(final_start),
        "efficiency_pct": efficiency if efficiency is not None else
        round(non_awake / total * 100, 1) if total else 0.0,
        "restlessness": 0.12, "stages": out,
    }


def layer(files: dict[str, list], enrolled=("P001", "P002")) -> SleepLayer:
    return build_sleep_layer(files, enrolled, WINDOW)


def write_sleep(data: Path, pid: str, sessions: list) -> None:
    folder = data / "wearable_events" / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sleep.json").write_text(json.dumps(sessions), encoding="utf-8")


def data_with(tmp_path: Path, p001: list | None = None, p002: list | None = None) -> Path:
    data = make_data(tmp_path)
    write_sleep(data, "P001", p001 if p001 is not None else [session()])
    if p002 is not None:
        write_sleep(data, "P002", p002)
    return data


def failed(result: SleepLayer, rule: str) -> int:
    return result.failed.get(rule, 0)


def rule_rejects(result: SleepLayer, rule: str) -> list:
    return [r for r in result.rejects if r[0] == rule]


# --- parse_local_timestamp --------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("2026-01-07T23:04:11", datetime(2026, 1, 7, 23, 4, 11)),
    ("2026-01-07T23:04:11.000", datetime(2026, 1, 7, 23, 4, 11)),
    ("2026-01-07T23:04:11.123456", datetime(2026, 1, 7, 23, 4, 11, 123456)),
    ("  2026-01-07T23:04:11  ", datetime(2026, 1, 7, 23, 4, 11)),
    ("2026-01-08T04:04:11Z", datetime(2026, 1, 7, 23, 4, 11)),
    ("2026-01-08T00:04:11+01:00", datetime(2026, 1, 7, 18, 4, 11)),
    ("2026-01-07T23:04:11-05:00", datetime(2026, 1, 7, 23, 4, 11)),
    ("2026-01-07T23:04:11+00:00", datetime(2026, 1, 7, 18, 4, 11)),
    ("2026-01-01T00:30:00Z", datetime(2025, 12, 31, 19, 30, 0)),
    ("2026-01-07T20:00:00-08:00", datetime(2026, 1, 7, 23, 0, 0)),
    ("2026-01-08T04:30:00+05:30", datetime(2026, 1, 7, 18, 0, 0)),
])
def test_parse_local_timestamp_accepts_and_converts_to_utc_minus_5(raw, expected):
    assert parse_local_timestamp(raw) == expected


@pytest.mark.parametrize("raw", [
    "2026-01-07 23:04:11", "2026-01-07", "20260107T230411", "2026-01-07T23:04",
    "2026-01-07T23:04:11,5", "2026-01-07T23:04:11+01", "2026-01-07T24:00:00",
    "2026-01-07T23:04:60", "2026-02-30T01:00:00", "2026-01-07T23:04:11.1234567",
    "2026-01-07T23:04:11+24:00", "2026-01-07T23:04:11+01:60", "2026-01-07T23:04:11 Z",
    "2026-01-08T04:04:11z", "2026-01-08T00:04:11+0100", "٢٠٢٦-01-07T23:04:11", "2026-01-07T23:04:11.", "", "   ", "garbage",
    None, 20260107, 1.5, True, [], {},
])
def test_parse_local_timestamp_rejects_everything_else(raw):
    assert parse_local_timestamp(raw) is None


# --- compute_sleep_metrics -------------------------------------------------------------


def test_metrics_hand_computed_example():
    metrics = compute_sleep_metrics(DEFAULT_STAGES)
    assert isinstance(metrics, SleepMetrics)
    assert metrics.total_sleep_min == 266
    assert metrics.deep_sleep_pct == pytest.approx(81 / 266 * 100, rel=0, abs=1e-12)
    assert metrics.sleep_efficiency_pct == pytest.approx(266 / 285 * 100, rel=0, abs=1e-12)


def test_metrics_do_not_round_and_ignore_input_order_of_awake():
    metrics = compute_sleep_metrics([("awake", 19), ("light", 42), ("deep", 81), ("rem", 53),
                                     ("light", 90)])
    assert metrics.deep_sleep_pct == 81 / 266 * 100
    assert metrics.sleep_efficiency_pct == 266 / 285 * 100


def test_metrics_empty_stage_list_is_all_none():
    assert compute_sleep_metrics([]) == SleepMetrics(None, None, None)


def test_metrics_awake_only_has_zero_total_and_no_percentages():
    assert compute_sleep_metrics([("awake", 30)]) == SleepMetrics(0, None, None)


def test_metrics_zero_deep_duration_in_valid_stage_gives_zero_percent():
    metrics = compute_sleep_metrics([("light", 60), ("deep", 0)])
    assert metrics.total_sleep_min == 60
    assert metrics.deep_sleep_pct == 0.0
    assert metrics.sleep_efficiency_pct == 100.0


def test_metrics_invalid_awake_duration_keeps_total_but_withholds_percentages():
    metrics = compute_sleep_metrics([("light", 60), ("deep", 30), ("awake", None)])
    assert metrics == SleepMetrics(90, None, None)


def test_metrics_invalid_non_awake_duration_withholds_everything():
    assert compute_sleep_metrics([("light", 60), ("deep", None)]) == SleepMetrics(None, None, None)


def test_metrics_unknown_stage_name_counts_as_non_awake_and_withholds_everything():
    assert compute_sleep_metrics([("light", 60), ("nap", 20)]) == SleepMetrics(None, None, None)


def test_metrics_unknown_name_with_missing_duration_withholds_everything():
    assert compute_sleep_metrics([("light", 60), ("Awake", None)]) == SleepMetrics(None, None, None)


def test_metrics_is_deterministic_and_does_not_mutate_input():
    stages = list(DEFAULT_STAGES)
    first = compute_sleep_metrics(stages)
    assert compute_sleep_metrics(stages) == first
    assert stages == DEFAULT_STAGES


# --- SLEEP_RULES constant ---------------------------------------------------------------


def test_sleep_rules_table_matches_spec():
    assert set(SLEEP_RULES) == set(SLEEP_REASONS)
    for rule_id, (source, handling, severity, reason) in SLEEP_RULES.items():
        assert source == "sleep"
        assert reason == SLEEP_REASONS[rule_id]
        assert (handling, severity) == SLEEP_HANDLING[rule_id]


# --- build_sleep_layer: silver grain and keys --------------------------------------------


def test_clean_session_produces_one_session_row_and_one_row_per_stage_with_no_defects():
    result = layer({SLEEP_PATH: [session()]})
    assert isinstance(result, SleepLayer)
    assert result.checked == 1
    assert sum(result.failed.values()) == 0
    assert result.rejects == []
    assert len(result.sessions) == 1
    assert len(result.stages) == 5
    row = result.sessions[0]
    assert row[0] == "P001" and row[1] == DAY1
    assert row[2] == datetime(2026, 1, 7, 23, 4, 0) and row[3] == datetime(2026, 1, 8, 3, 30, 0)
    assert row[4] == 93.3 and row[5] == 0.12
    assert row[6:9] == (False, False, False)
    assert row[9] is None
    assert [s[2] for s in result.stages] == [1, 2, 3, 4, 5]
    assert [s[3] for s in result.stages] == ["light", "deep", "rem", "light", "awake"]
    assert [s[6] for s in result.stages] == [42, 81, 53, 90, 19]
    assert [s[7] for s in result.stages] == [42.0, 81.0, 53.0, 90.0, 19.0]
    assert all(s[8] is None for s in result.stages)


def test_every_session_and_stage_is_kept_even_when_defective():
    bad = session(day=DAY2)
    bad["stages"][0]["duration_min"] = "x"
    orphan = session(pid="P999")
    files = {SLEEP_PATH: [session(), bad, {"date": "nope"}, orphan, session(day=date(2025, 1, 1))]}
    result = layer(files)
    assert result.checked == 5
    assert len(result.sessions) == 5
    assert len(result.stages) == 5 + 5 + 0 + 5 + 5
    assert result.sessions[2][1] is None


def test_wake_date_is_the_date_field_even_when_onset_is_the_previous_day():
    result = layer({SLEEP_PATH: [session()]})
    assert result.sessions[0][1] == DAY1
    assert result.sessions[0][2].date() == DAY1 - timedelta(days=1)


def test_utc_and_naive_timestamps_land_on_the_same_local_value():
    s = session()
    naive_start = datetime.fromisoformat(s["sleep_onset"])
    s["sleep_onset"] = iso(naive_start + timedelta(hours=5)) + "Z"
    result = layer({SLEEP_PATH: [s]})
    assert result.sessions[0][2] == naive_start


def test_output_is_independent_of_mapping_order_and_sorted_by_path():
    files = {SLEEP_PATH_2: [session("P002")], SLEEP_PATH: [session()]}
    reverse = dict(reversed(list(files.items())))
    first, second = layer(files), layer(reverse)
    assert first == second
    assert [r[0] for r in first.sessions] == ["P001", "P002"]


def test_empty_input_reports_zero_checked_and_nothing_else():
    result = layer({})
    assert result.checked == 0 and result.sessions == [] and result.stages == []
    assert result.rejects == [] and sum(result.failed.values()) == 0 and result.daily == {}
    assert layer({SLEEP_PATH: []}).checked == 0


def test_layer_does_not_mutate_its_input():
    files = {SLEEP_PATH: [session(), session(day=DAY2)]}
    before = copy.deepcopy(files)
    layer(files)
    assert files == before


# --- each rule: count, reject, reason ------------------------------------------------------


def assert_only(result: SleepLayer, rule: str, count: int = 1) -> None:
    assert failed(result, rule) == count
    assert len(rule_rejects(result, rule)) == count
    others = {k: v for k, v in result.failed.items() if k != rule and v}
    assert others == {}


def test_orphan_participant_is_flagged_and_kept_in_silver_but_not_in_daily():
    s = session(pid="P999")
    result = layer({"wearable_events/P999/sleep.json": [s]})
    assert_only(result, "SLEEP_ORPHAN_PARTICIPANT")
    assert result.sessions[0][7] is True
    assert result.sessions[0][9] == "SLEEP_ORPHAN_PARTICIPANT"
    assert result.daily == {}
    assert rule_rejects(result, "SLEEP_ORPHAN_PARTICIPANT")[0] == (
        "SLEEP_ORPHAN_PARTICIPANT", "wearable_events/P999/sleep.json", 1, s)


def test_participant_id_falls_back_to_folder_name_and_mismatch_is_not_checked():
    missing = session()
    del missing["participant_id"]
    blank = session(day=DAY2)
    blank["participant_id"] = "   "
    other = session(day=WINDOW[2])
    other["participant_id"] = "P002"
    result = layer({SLEEP_PATH: [missing, blank, other]})
    assert [r[0] for r in result.sessions] == ["P001", "P001", "P002"]
    assert sum(result.failed.values()) == 0


def test_duplicate_participant_day_flags_every_session_of_the_key_and_withholds_the_day():
    files = {SLEEP_PATH: [session(), session(), session(day=DAY2)]}
    result = layer(files)
    assert_only(result, "SLEEP_DUPLICATE_PARTICIPANT_DAY", 2)
    assert [r[6] for r in result.sessions] == [True, True, False]
    assert [r[1] for r in rule_rejects(result, "SLEEP_DUPLICATE_PARTICIPANT_DAY")] == [
        SLEEP_PATH, SLEEP_PATH]
    assert [r[2] for r in rule_rejects(result, "SLEEP_DUPLICATE_PARTICIPANT_DAY")] == [1, 2]


def test_duplicates_across_files_are_not_grouped_when_participant_differs():
    result = layer({SLEEP_PATH: [session()], SLEEP_PATH_2: [session("P002")]})
    assert sum(result.failed.values()) == 0


def test_duplicate_key_is_detected_across_files_of_the_same_participant():
    alt = "wearable_events/P001-copy/sleep.json"
    result = layer({SLEEP_PATH: [session()], alt: [session()]})
    assert failed(result, "SLEEP_DUPLICATE_PARTICIPANT_DAY") == 2


def test_unparseable_date_is_flagged_and_stored_null_and_never_grouped_as_duplicate():
    a, b = session(), session()
    a["date"] = "2026-13-45"
    b["date"] = "08/01/2026"
    c = session()
    del c["date"]
    result = layer({SLEEP_PATH: [a, b, c]})
    assert failed(result, "SLEEP_DATE_UNPARSEABLE") == 3
    assert failed(result, "SLEEP_DUPLICATE_PARTICIPANT_DAY") == 0
    assert all(r[1] is None for r in result.sessions)
    assert all(r[8] is False for r in result.sessions)
    assert result.daily == {}


def test_non_string_date_is_unparseable():
    s = session()
    s["date"] = 20260108
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_DATE_UNPARSEABLE") == 1


def test_out_of_window_date_is_flagged_inclusive_boundaries_are_inside():
    inside = [session(day=WINDOW[0]), session(day=WINDOW[-1])]
    outside = [session(day=WINDOW[0] - timedelta(days=1)),
               session(day=WINDOW[-1] + timedelta(days=1))]
    result = layer({SLEEP_PATH: inside + outside})
    assert_only(result, "SLEEP_DATE_OUT_OF_WINDOW", 2)
    assert [r[8] for r in result.sessions] == [False, False, True, True]
    assert set(result.daily) == {("P001", WINDOW[0]), ("P001", WINDOW[-1])}


@pytest.mark.parametrize("field, value", [
    ("sleep_onset", "2026-01-07 23:04:11"), ("sleep_onset", None), ("sleep_end", "garbage"),
    ("sleep_end", 5), ("efficiency_pct", "93.3"), ("efficiency_pct", True),
    ("efficiency_pct", 100.5), ("efficiency_pct", -0.1), ("efficiency_pct", None),
    ("restlessness", 1.1), ("restlessness", -0.01), ("restlessness", "0.1"),
    ("restlessness", False), ("restlessness", None),
])
def test_session_field_invalid_is_flagged_and_the_field_is_withheld(field, value):
    s = session()
    s[field] = value
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_SESSION_FIELD_INVALID") == 1
    row = result.sessions[0]
    index = {"sleep_onset": 2, "sleep_end": 3, "efficiency_pct": 4, "restlessness": 5}[field]
    assert row[index] is None


@pytest.mark.parametrize("field", ["sleep_onset", "sleep_end", "efficiency_pct", "restlessness"])
def test_missing_session_field_is_invalid_and_never_raises(field):
    s = session()
    del s[field]
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_SESSION_FIELD_INVALID") == 1


@pytest.mark.parametrize("value", [0, 0.0, 100, 100.0, 55.5])
def test_efficiency_bounds_are_inclusive(value):
    s = session(efficiency=value)
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_SESSION_FIELD_INVALID") == 0
    assert result.sessions[0][4] == value


@pytest.mark.parametrize("value", [0, 0.0, 1, 1.0])
def test_restlessness_bounds_are_inclusive(value):
    s = session()
    s["restlessness"] = value
    assert failed(layer({SLEEP_PATH: [s]}), "SLEEP_SESSION_FIELD_INVALID") == 0


def test_a_session_with_several_invalid_fields_counts_once():
    s = session()
    s["sleep_onset"] = "bad"
    s["sleep_end"] = "bad"
    s["restlessness"] = 7
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_SESSION_FIELD_INVALID") == 1
    assert len(rule_rejects(result, "SLEEP_SESSION_FIELD_INVALID")) == 1


@pytest.mark.parametrize("mutate", [
    lambda st: st.update(stage="nap"), lambda st: st.update(stage=None),
    lambda st: st.update(stage=7), lambda st: st.update(stage="Light"),
    lambda st: st.update(duration_min="42"), lambda st: st.update(duration_min=42.0),
    lambda st: st.update(duration_min=0), lambda st: st.update(duration_min=-5),
    lambda st: st.update(duration_min=True), lambda st: st.update(duration_min=None),
    lambda st: st.update(start_time="bad"), lambda st: st.update(end_time=None),
    lambda st: st.pop("duration_min"), lambda st: st.pop("stage"),
])
def test_stage_invalid_covers_name_duration_and_time_defects(mutate):
    s = session()
    mutate(s["stages"][1])
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_STAGE_INVALID") == 1
    assert len(rule_rejects(result, "SLEEP_STAGE_INVALID")) == 1
    assert len(result.stages) == 5
    assert result.stages[1][8] is not None and "SLEEP_STAGE_INVALID" in result.stages[1][8]
    assert "SLEEP_STAGE_INVALID" in result.sessions[0][9]


def test_missing_or_non_array_stages_is_one_invalid_session_with_no_stage_rows():
    a, b = session(), session(day=DAY2)
    del a["stages"]
    b["stages"] = "none"
    result = layer({SLEEP_PATH: [a, b]})
    assert failed(result, "SLEEP_STAGE_INVALID") == 2
    assert result.stages == []
    assert result.daily[("P001", DAY1)].total_sleep_min is None


def test_non_object_stage_element_is_one_invalid_stage_with_null_fields():
    s = session()
    s["stages"].append(7)
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_STAGE_INVALID") == 1
    assert len(result.stages) == 6
    last = result.stages[-1]
    assert last[2] == 6 and last[3:8] == (None, None, None, None, None)


def day_metrics(stage_edit, base=None):
    s = base or session()
    stage_edit(s)
    day = layer({SLEEP_PATH: [s]}).daily[("P001", DAY1)]
    return day.total_sleep_min, day.deep_sleep_pct, day.sleep_efficiency_pct


def test_layer_metrics_null_for_zero_duration_deep_stage():
    assert day_metrics(lambda s: s["stages"][1].update(duration_min=0)) == (None,) * 3


@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_layer_metrics_null_when_a_non_awake_stage_time_is_unparseable(field):
    assert day_metrics(lambda s: s["stages"][2].update({field: "bad"})) == (None,) * 3


@pytest.mark.parametrize("edit", [
    lambda s: s["stages"][4].update(start_time="bad"),
    lambda s: s["stages"][4].update(end_time=None),
    lambda s: s["stages"][4].update(duration_min="x"),
])
def test_layer_metrics_keep_total_when_only_the_awake_stage_is_invalid(edit):
    assert day_metrics(edit) == (266, None, None)


def test_layer_metrics_null_for_non_object_and_non_string_named_stages():
    assert day_metrics(lambda s: s["stages"].append(7)) == (None,) * 3
    assert day_metrics(lambda s: s["stages"][0].update(stage=None)) == (None,) * 3
    assert day_metrics(lambda s: s["stages"][0].update(stage=5)) == (None,) * 3


def test_s01_duration_mismatch_uses_exact_seconds_and_flags_without_withholding():
    s = session()
    s["stages"][2]["duration_min"] = 54
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_STAGE_DURATION_MISMATCH") == 1
    assert result.stages[2][8] == "SLEEP_STAGE_DURATION_MISMATCH"
    assert result.stages[2][6] == 54 and result.stages[2][7] == 53.0
    assert result.daily[("P001", DAY1)].total_sleep_min == 267


def test_s01_does_not_fire_for_a_sub_minute_difference_of_zero_but_does_for_one_second():
    s = session()
    end = datetime.fromisoformat(s["stages"][0]["end_time"]) + timedelta(seconds=1)
    s["stages"][0]["end_time"] = iso(end)
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_STAGE_DURATION_MISMATCH") == 1
    assert result.stages[0][7] == pytest.approx(42 + 1 / 60)


def test_s01_is_not_evaluated_for_an_invalid_duration():
    s = session()
    s["stages"][0]["duration_min"] = "x"
    assert failed(layer({SLEEP_PATH: [s]}), "SLEEP_STAGE_DURATION_MISMATCH") == 0


def test_gap_and_overlap_between_consecutive_stages_are_flagged_on_the_later_stage():
    s = session()
    s["stages"][2]["start_time"] = iso(datetime.fromisoformat(s["stages"][1]["end_time"])
                                       + timedelta(minutes=2))
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_STAGE_GAP_OR_OVERLAP") == 1
    assert "SLEEP_STAGE_GAP_OR_OVERLAP" in result.stages[2][8]
    assert result.stages[1][8] is None
    assert result.stages[3][8] is None
    t = session()
    t["stages"][1]["start_time"] = iso(datetime.fromisoformat(t["stages"][0]["end_time"])
                                       - timedelta(minutes=1))
    assert failed(layer({SLEEP_PATH: [t]}), "SLEEP_STAGE_GAP_OR_OVERLAP") == 1


def test_gap_check_skips_pairs_with_unparseable_times():
    s = session()
    s["stages"][2]["start_time"] = "bad"
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_STAGE_GAP_OR_OVERLAP") == 0


def test_s02_span_over_twelve_hours_is_flagged_exactly_twelve_is_not():
    long = session(stages=[("light", 800), ("awake", 10)])
    exact = session(day=DAY2, stages=[("light", 720), ("awake", 10)])
    just_over = session(day=WINDOW[2], stages=[("light", 721), ("awake", 10)])
    result = layer({SLEEP_PATH: [long, exact, just_over]})
    assert failed(result, "SLEEP_SPAN_OUTLIER") == 2
    assert [r[2] for r in rule_rejects(result, "SLEEP_SPAN_OUTLIER")] == [1, 3]


def test_short_sessions_under_three_hours_are_not_flagged():
    short = session(stages=[("light", 100), ("awake", 5)])
    assert failed(layer({SLEEP_PATH: [short]}), "SLEEP_SPAN_OUTLIER") == 0


def test_s03_final_stage_must_start_at_sleep_end():
    s = session()
    s["sleep_end"] = iso(datetime.fromisoformat(s["sleep_end"]) + timedelta(minutes=3))
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_FINAL_STAGE_MISALIGNED") == 1
    ok = layer({SLEEP_PATH: [session()]})
    assert failed(ok, "SLEEP_FINAL_STAGE_MISALIGNED") == 0


def test_s03_is_not_evaluated_when_sleep_end_is_invalid():
    s = session()
    s["sleep_end"] = "bad"
    assert failed(layer({SLEEP_PATH: [s]}), "SLEEP_FINAL_STAGE_MISALIGNED") == 0


def test_s04_efficiency_mismatch_uses_the_derived_value_and_one_point_tolerance():
    derived = 266 / 285 * 100
    ok = session(efficiency=round(derived + 0.9, 1))
    bad = session(day=DAY2, efficiency=round(derived + 1.5, 1))
    result = layer({SLEEP_PATH: [ok, bad]})
    assert failed(result, "SLEEP_EFFICIENCY_MISMATCH") == 1
    assert rule_rejects(result, "SLEEP_EFFICIENCY_MISMATCH")[0][2] == 2


def test_s04_cannot_fire_when_derived_efficiency_is_null():
    s = session(efficiency=10.0)
    s["stages"][1]["duration_min"] = None
    result = layer({SLEEP_PATH: [s]})
    assert failed(result, "SLEEP_EFFICIENCY_MISMATCH") == 0
    awake_only = session(day=DAY2, stages=[("awake", 30)], efficiency=50.0)
    assert failed(layer({SLEEP_PATH: [awake_only]}), "SLEEP_EFFICIENCY_MISMATCH") == 0


def test_s05_onset_not_minute_aligned_flags_seconds_and_fractions():
    sec = session(onset=datetime(2026, 1, 7, 23, 4, 11))
    frac = session(day=DAY2, onset=datetime(2026, 1, 8, 23, 4, 0))
    frac["sleep_onset"] = "2026-01-08T23:04:00.500"
    aligned = session(day=WINDOW[2])
    result = layer({SLEEP_PATH: [sec, frac, aligned]})
    assert failed(result, "SLEEP_ONSET_NOT_MINUTE_ALIGNED") == 2
    assert [r[2] for r in rule_rejects(result, "SLEEP_ONSET_NOT_MINUTE_ALIGNED")] == [1, 2]


def test_flag_handling_rules_do_not_remove_values_from_gold():
    s = session(onset=datetime(2026, 1, 7, 23, 4, 11))
    result = layer({SLEEP_PATH: [s]})
    day = result.daily[("P001", DAY1)]
    assert day.total_sleep_min == 266
    assert day.sleep_quality_flags is not None
    assert "SLEEP_ONSET_NOT_MINUTE_ALIGNED" in day.sleep_quality_flags


def test_rejects_carry_whole_raw_session_and_one_based_position_and_are_unique():
    bad = session(day=DAY2)
    bad["stages"][0]["duration_min"] = "x"
    bad["stages"][1]["stage"] = "nap"
    files = {SLEEP_PATH: [session(), bad]}
    result = layer(files)
    keys = [(r[0], r[1], r[2]) for r in result.rejects]
    assert len(keys) == len(set(keys))
    stage_rejects = rule_rejects(result, "SLEEP_STAGE_INVALID")
    assert len(stage_rejects) == 1
    assert stage_rejects[0] == ("SLEEP_STAGE_INVALID", SLEEP_PATH, 2, bad)


def test_rejects_are_ordered_deterministically():
    a, b = session(pid="P999"), session(day=DAY2, pid="P999")
    files = {"wearable_events/P999/sleep.json": [a, b]}
    result = layer(files)
    assert [r[2] for r in result.rejects] == [1, 2]


def test_quality_flags_on_session_are_sorted_distinct_comma_joined():
    s = session(onset=datetime(2026, 1, 7, 23, 4, 11))
    s["sleep_end"] = "bad"
    result = layer({SLEEP_PATH: [s]})
    flags = result.sessions[0][9].split(",")
    assert flags == sorted(set(flags))
    assert "SLEEP_ONSET_NOT_MINUTE_ALIGNED" in flags and "SLEEP_SESSION_FIELD_INVALID" in flags


# --- daily metrics ------------------------------------------------------------------------


def test_daily_metrics_for_a_clean_session():
    result = layer({SLEEP_PATH: [session()]})
    assert result.daily == {("P001", DAY1): SleepDay(
        266, 81 / 266 * 100, 266 / 285 * 100, 1, None)}


def test_exact_duplicates_keep_their_metrics_and_count_both_sessions():
    result = layer({SLEEP_PATH: [session(), session()]})
    day = result.daily[("P001", DAY1)]
    assert day.sleep_session_count == 2
    assert day.total_sleep_min == 266
    assert day.sleep_quality_flags == "SLEEP_DUPLICATE_PARTICIPANT_DAY"


def test_conflicting_duplicates_null_all_three_metrics_without_picking_either():
    other = session(stages=[("light", 100), ("deep", 50), ("awake", 10)])
    result = layer({SLEEP_PATH: [session(), other]})
    day = result.daily[("P001", DAY1)]
    assert day.sleep_session_count == 2
    assert (day.total_sleep_min, day.deep_sleep_pct, day.sleep_efficiency_pct) == (None,) * 3


def test_a_full_tuple_and_a_null_tuple_on_the_same_day_conflict():
    broken = session()
    broken["stages"][0]["duration_min"] = None
    result = layer({SLEEP_PATH: [session(), broken]})
    day = result.daily[("P001", DAY1)]
    assert (day.total_sleep_min, day.deep_sleep_pct, day.sleep_efficiency_pct) == (None,) * 3


def test_stage_invalid_session_gets_null_metrics_but_still_counts_as_present():
    s = session()
    s["stages"][1]["stage"] = "nap"
    day = layer({SLEEP_PATH: [s]}).daily[("P001", DAY1)]
    assert day.total_sleep_min is None and day.sleep_session_count == 1
    assert "SLEEP_STAGE_INVALID" in day.sleep_quality_flags


def test_orphan_unparseable_and_out_of_window_sessions_never_contribute():
    files = {SLEEP_PATH: [session(day=date(2020, 1, 1)), {"date": "bad", "stages": []}],
             "wearable_events/P999/sleep.json": [session(pid="P999")]}
    assert layer(files).daily == {}


def test_daily_includes_both_participants_and_multiple_days():
    files = {SLEEP_PATH: [session(), session(day=DAY2)], SLEEP_PATH_2: [session("P002")]}
    assert set(layer(files).daily) == {("P001", DAY1), ("P001", DAY2), ("P002", DAY1)}


# --- pipeline level: gold sleep columns ----------------------------------------------------

NEW_COLUMNS = ["total_sleep_min", "deep_sleep_pct", "sleep_efficiency_pct",
               "sleep_session_count", "sleep_present", "sleep_quality_flags"]


def test_pipeline_gold_has_sleep_values_for_the_day_and_defaults_elsewhere(tmp_path):
    result = run(data_with(tmp_path), tmp_path / "out")
    row = gold(result, "P001", DAY1)
    assert row["total_sleep_min"] == 266
    assert row["deep_sleep_pct"] == pytest.approx(81 / 266 * 100, abs=1e-12)
    assert row["sleep_efficiency_pct"] == pytest.approx(266 / 285 * 100, abs=1e-12)
    assert (row["sleep_session_count"], row["sleep_present"], row["sleep_quality_flags"]) == (
        1, True, None)
    empty = gold(result, "P001", DAY2)
    assert [empty[c] for c in NEW_COLUMNS] == [None, None, None, 0, False, None]


def test_participant_without_sleep_file_has_null_sleep_and_false_present(tmp_path):
    result = run(data_with(tmp_path), tmp_path / "out")
    row = gold(result, "P002", DAY1)
    assert [row[c] for c in NEW_COLUMNS] == [None, None, None, 0, False, None]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2,)]


def test_gold_ignores_out_of_window_and_orphan_sleep_and_adds_no_rows(tmp_path):
    outside = session(day=date(2020, 1, 1))
    data = data_with(tmp_path, [outside])
    write_sleep(data, "P999", [session(pid="P999")])
    result = run(data, tmp_path / "out")
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2,)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily WHERE sleep_present") == [(0,)]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily "
                         "WHERE participant_id = 'P999'") == [(0,)]


def test_gold_exact_duplicate_keeps_metrics_and_flags_the_day(tmp_path):
    result = run(data_with(tmp_path, [session(), session()]), tmp_path / "out")
    row = gold(result, "P001", DAY1)
    assert row["sleep_session_count"] == 2 and row["sleep_present"] is True
    assert row["total_sleep_min"] == 266
    assert row["sleep_quality_flags"] == "SLEEP_DUPLICATE_PARTICIPANT_DAY"


def test_gold_conflicting_duplicates_null_the_metrics(tmp_path):
    other = session(stages=[("light", 100), ("deep", 50), ("awake", 10)])
    result = run(data_with(tmp_path, [session(), other]), tmp_path / "out")
    row = gold(result, "P001", DAY1)
    assert row["sleep_session_count"] == 2
    assert (row["total_sleep_min"], row["deep_sleep_pct"], row["sleep_efficiency_pct"]) == (
        None, None, None)
    assert "SLEEP_DUPLICATE_PARTICIPANT_DAY" in row["sleep_quality_flags"]


def test_survey_columns_are_unchanged_by_the_sleep_layer(tmp_path):
    with_sleep = run(data_with(tmp_path / "a"), tmp_path / "a" / "out")
    without = run(make_data(tmp_path / "b"), tmp_path / "b" / "out")
    cols = ("participant_id, local_date, fatigue_score, stress_score, readiness_score, "
            "sleep_quality_score, survey_present, survey_row_count, survey_valid_score_count, "
            "survey_quality_flags")
    sql = f"SELECT {cols} FROM gold_clinical_daily ORDER BY ALL"
    assert gold(with_sleep, "P001", DAY1)["sleep_present"] is True
    assert query(with_sleep, sql) == query(without, sql)


def test_silver_sleep_tables_are_written_with_the_documented_grain(tmp_path):
    s2 = session("P002")
    result = run(data_with(tmp_path, [session(), session(day=DAY2)], [s2]), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_sleep_sessions") == [(3,)]
    assert query(result, "SELECT count(*) FROM silver_sleep_stages") == [(15,)]
    cols = [r[0] for r in query(result, "SELECT column_name FROM information_schema.columns "
                                         "WHERE table_name = 'silver_sleep_sessions' "
                                         "ORDER BY ordinal_position")]
    assert cols == ["participant_id", "local_date", "sleep_onset", "sleep_end",
                    "supplied_efficiency_pct", "restlessness", "is_duplicate_key",
                    "is_orphan", "is_out_of_window", "quality_flags"]
    cols = [r[0] for r in query(result, "SELECT column_name FROM information_schema.columns "
                                         "WHERE table_name = 'silver_sleep_stages' "
                                         "ORDER BY ordinal_position")]
    assert cols == ["participant_id", "local_date", "stage_index", "stage", "start_time",
                    "end_time", "duration_min", "derived_duration_min", "quality_flags"]
    keys = query(result, "SELECT participant_id, local_date, stage_index "
                         "FROM silver_sleep_stages")
    assert len(keys) == len(set(keys))


def test_silver_sleep_column_types(tmp_path):
    result = run(data_with(tmp_path), tmp_path / "out")
    types = dict(query(result, "SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_name = 'silver_sleep_sessions'"))
    assert types == {
        "participant_id": "VARCHAR", "local_date": "DATE", "sleep_onset": "TIMESTAMP",
        "sleep_end": "TIMESTAMP", "supplied_efficiency_pct": "DOUBLE",
        "restlessness": "DOUBLE", "is_duplicate_key": "BOOLEAN", "is_orphan": "BOOLEAN",
        "is_out_of_window": "BOOLEAN", "quality_flags": "VARCHAR",
    }
    types = dict(query(result, "SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_name = 'silver_sleep_stages'"))
    assert types == {
        "participant_id": "VARCHAR", "local_date": "DATE", "stage_index": "INTEGER",
        "stage": "VARCHAR", "start_time": "TIMESTAMP", "end_time": "TIMESTAMP",
        "duration_min": "INTEGER", "derived_duration_min": "DOUBLE", "quality_flags": "VARCHAR",
    }


def test_pipeline_reports_sleep_rule_counts_rejects_and_reasons(tmp_path):
    bad = session(day=DAY2)
    bad["stages"][1]["stage"] = "nap"
    orphan_dir = "P999"
    data = data_with(tmp_path, [session(), bad])
    write_sleep(data, orphan_dir, [session(pid="P999")])
    result = run(data, tmp_path / "out")
    results = quality(result)
    assert results[("SLEEP_ORPHAN_PARTICIPANT", "sleep")][-3:-1] == (3, 1)
    assert results[("SLEEP_STAGE_INVALID", "sleep")][-3:-1] == (3, 1)
    assert results[("SLEEP_SPAN_OUTLIER", "sleep")][-3:-1] == (3, 0)
    rejects = query(result, "SELECT rule_id, source, row_ref, reason, raw_values "
                            "FROM quality_rejects ORDER BY rule_id")
    by_rule = {r[0]: r for r in rejects}
    assert by_rule["SLEEP_STAGE_INVALID"][1] == "sleep"
    assert by_rule["SLEEP_STAGE_INVALID"][2] == f"{SLEEP_PATH}:2"
    assert by_rule["SLEEP_STAGE_INVALID"][3] == SLEEP_REASONS["SLEEP_STAGE_INVALID"]
    assert json.loads(by_rule["SLEEP_STAGE_INVALID"][4]) == bad
    assert by_rule["SLEEP_ORPHAN_PARTICIPANT"][2] == "wearable_events/P999/sleep.json:1"
    assert query(result, "SELECT count(*) FROM quality_results") == [(19,)]


def test_sleep_rejects_carry_each_rules_exact_reason(tmp_path):
    dup = session()
    unparseable = session(day=DAY2)
    unparseable["date"] = "bad"
    stale = session(day=date(2020, 1, 1))
    broken = session(day=WINDOW[2])
    broken["restlessness"] = 9
    broken["stages"][0]["duration_min"] = 43
    broken["sleep_end"] = "2026-01-10T03:00:00"
    broken["stages"][2]["start_time"] = "2026-01-09T20:00:00"
    long = session(stages=[("light", 800), ("awake", 10)], onset=datetime(2026, 1, 7, 8, 0, 3))
    data = data_with(tmp_path, [dup, dup, unparseable, stale, broken, long, session(pid="P999")])
    result = run(data, tmp_path / "out")
    rows = query(result, "SELECT DISTINCT rule_id, reason FROM quality_rejects")
    assert dict(rows) == {
        rule: SLEEP_REASONS[rule] for rule in (
            "SLEEP_ORPHAN_PARTICIPANT", "SLEEP_DUPLICATE_PARTICIPANT_DAY",
            "SLEEP_DATE_UNPARSEABLE", "SLEEP_DATE_OUT_OF_WINDOW",
            "SLEEP_SESSION_FIELD_INVALID", "SLEEP_STAGE_DURATION_MISMATCH",
            "SLEEP_STAGE_GAP_OR_OVERLAP", "SLEEP_SPAN_OUTLIER",
            "SLEEP_FINAL_STAGE_MISALIGNED", "SLEEP_ONSET_NOT_MINUTE_ALIGNED")}


def test_remaining_sleep_rejects_carry_their_exact_reasons(tmp_path):
    invalid = session()
    invalid["stages"][1]["stage"] = "nap"
    misaligned = session(day=DAY2)
    misaligned["sleep_end"] = iso(datetime.fromisoformat(misaligned["sleep_end"])
                                  + timedelta(minutes=3))
    mismatch = session(day=WINDOW[2], efficiency=50.0)
    result = run(data_with(tmp_path, [invalid, misaligned, mismatch]), tmp_path / "out")
    rows = dict(query(result, "SELECT DISTINCT rule_id, reason FROM quality_rejects"))
    for rule in ("SLEEP_STAGE_INVALID", "SLEEP_FINAL_STAGE_MISALIGNED",
                 "SLEEP_EFFICIENCY_MISMATCH"):
        assert rows[rule] == SLEEP_REASONS[rule]


def test_report_lists_all_nineteen_rules_in_rule_id_order(tmp_path):
    result = run(data_with(tmp_path), tmp_path / "out")
    text = result.report_path.read_text(encoding="utf-8")
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines() if line.startswith("| ") and "rule_id" not in line
        and not line.startswith("| ---")
    ]
    assert len(rows) == 19
    assert [(r[0], r[1]) for r in rows] == sorted((r[0], r[1]) for r in rows)
    sleep = {r[0]: r for r in rows if r[1] == "sleep"}
    assert sleep["SLEEP_SPAN_OUTLIER"] == [
        "SLEEP_SPAN_OUTLIER", "sleep", "flag", "medium", "1", "0"]


# --- pipeline level: idempotency and determinism ---------------------------------------------


def test_rerun_on_unchanged_inputs_is_identical_including_the_report(tmp_path):
    data = data_with(tmp_path, [session(), session(day=DAY2)], [session("P002")])
    first = run(data, tmp_path / "out")
    before = snapshot(first)
    sleep_before = {
        name: query(first, f"SELECT * FROM {name} ORDER BY ALL")
        for name in ("silver_sleep_sessions", "silver_sleep_stages")}
    report = first.report_path.read_bytes()
    second = run(data, tmp_path / "out")
    assert snapshot(second) == before
    assert {name: query(second, f"SELECT * FROM {name} ORDER BY ALL")
            for name in sleep_before} == sleep_before
    assert second.report_path.read_bytes() == report
    assert second.run_id == first.run_id


def test_changing_one_session_changes_exactly_that_days_sleep_columns(tmp_path):
    data = data_with(tmp_path, [session(), session(day=DAY2)], [session("P002")])
    first = run(data, tmp_path / "out")
    before = query(first, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    write_sleep(data, "P001", [session(), session(day=DAY2, stages=[("light", 120), ("awake", 5)])])
    second = run(data, tmp_path / "out")
    after = query(second, "SELECT * FROM gold_clinical_daily ORDER BY ALL")
    changed = [(a[0], a[1]) for a, b in zip(after, before) if a != b]
    assert changed == [("P001", DAY2)]
    assert len(after) == len(before)
    assert gold(second, "P001", DAY2)["total_sleep_min"] == 120


def test_correcting_a_survey_row_does_not_change_sleep_columns(tmp_path):
    data = data_with(tmp_path)
    first = run(data, tmp_path / "out")
    row = gold(first, "P001", DAY1)
    survey = data / "health_summaries" / "wellness_survey.csv"
    survey.write_text(survey.read_text(encoding="utf-8").replace("3,3,8,3", "4,4,9,4", 1),
                      encoding="utf-8")
    second = run(data, tmp_path / "out")
    after = gold(second, "P001", DAY1)
    for column in NEW_COLUMNS:
        assert after[column] == row[column]
    assert after["fatigue_score"] != row["fatigue_score"]


def test_sleep_tables_reflect_only_current_inputs_after_removal(tmp_path):
    data = data_with(tmp_path, [session(), session(day=DAY2)])
    run(data, tmp_path / "out")
    write_sleep(data, "P001", [session()])
    result = run(data, tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_sleep_sessions") == [(1,)]
    assert query(result, "SELECT count(*) FROM silver_sleep_stages") == [(5,)]
    assert gold(result, "P001", DAY2)["sleep_present"] is False


def test_database_from_before_inc_003_gets_sleep_columns_added(tmp_path):
    data = data_with(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    connection = duckdb.connect(str(first.database_path))
    try:
        connection.execute("DROP TABLE gold_clinical_daily")
        connection.execute(
            "CREATE TABLE gold_clinical_daily (participant_id VARCHAR, local_date DATE, "
            "fatigue_score INTEGER, stress_score INTEGER, readiness_score INTEGER, "
            "sleep_quality_score INTEGER, survey_present BOOLEAN, survey_row_count INTEGER, "
            "survey_valid_score_count INTEGER, survey_quality_flags VARCHAR, "
            "PRIMARY KEY (participant_id, local_date))")
        connection.execute("INSERT INTO gold_clinical_daily VALUES "
                           "('P001', DATE '2026-01-08', 3, 3, 8, 3, true, 1, 4, NULL)")
        connection.execute("DROP TABLE silver_sleep_sessions")
        connection.execute("DROP TABLE silver_sleep_stages")
    finally:
        connection.close()
    second = run(data, out)
    row = gold(second, "P001", DAY1)
    assert row["total_sleep_min"] == 266 and row["fatigue_score"] == 3
    assert query(second, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2,)]
    assert query(second, "SELECT count(*) FROM silver_sleep_sessions") == [(1,)]


def test_old_gold_rows_outside_the_window_are_retained_with_null_sleep(tmp_path):
    data = data_with(tmp_path)
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
    assert row["total_sleep_min"] is None and row["deep_sleep_pct"] is None
    assert query(second, "SELECT count(*) FROM gold_clinical_daily") == [(len(WINDOW) * 2 + 1,)]


def test_raw_data_is_untouched_and_nothing_is_created_under_the_data_dir(tmp_path):
    data = data_with(tmp_path, [session()], [session("P002")])
    before = tree_hashes(data)
    run(data, tmp_path / "out")
    assert tree_hashes(data) == before


# --- failure contract ---------------------------------------------------------------------


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}', '[1, 2]', '["x"]', "null", ""])
def test_malformed_sleep_file_raises_before_any_table_is_touched(tmp_path, content):
    data = data_with(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before = snapshot(first)
    (data / "wearable_events" / "P001" / "sleep.json").write_text(content, encoding="utf-8")
    with pytest.raises(PipelineError):
        run(data, out)
    assert snapshot(first) == before
    assert query(first, "SELECT count(*) FROM silver_sleep_sessions") == [(1,)]
    assert query(first, "SELECT count(*) FROM silver_sleep_stages") == [(5,)]


def test_sleep_file_that_is_not_utf8_raises(tmp_path):
    data = data_with(tmp_path)
    (data / "wearable_events" / "P001" / "sleep.json").write_bytes(b"\xff\xfe[]")
    with pytest.raises(PipelineError):
        run(data, tmp_path / "out")


def test_cli_exits_one_on_malformed_sleep_json_and_zero_on_valid(tmp_path):
    data = data_with(tmp_path)
    out = tmp_path / "out"
    args = ["run", "--data-dir", str(data), "--output-dir", str(out)]
    (data / "wearable_events" / "P001" / "sleep.json").write_text("{bad", encoding="utf-8")
    assert main(args) == 1
    write_sleep(data, "P001", [session()])
    assert main(args) == 0


def test_in_object_defects_never_raise(tmp_path):
    weird = [{}, {"stages": 5}, {"date": None, "stages": [None, 1, "x", []]},
             {"participant_id": 5, "date": 7, "efficiency_pct": [], "stages": {}}]
    result = run(data_with(tmp_path, weird), tmp_path / "out")
    assert query(result, "SELECT count(*) FROM silver_sleep_sessions") == [(4,)]
    assert quality(result)[("SLEEP_SESSION_FIELD_INVALID", "sleep")][-3:-1] == (4, 4)


def test_counter_type_is_used_for_failed():
    assert isinstance(layer({}).failed, Counter)
