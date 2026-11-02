"""Behavior tests for INC-011 (see specs/INC-011.md). Fixtures are synthetic."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from tests.test_incremental import DAY1, HR, hr, inc, m_corrected, state, write_events
from tests.test_walking_skeleton import (
    PARTICIPANT_COLUMNS, WINDOW, make_data, query, run, write_csv,
)
from wearable_pipeline import PipelineError, incremental, pipeline
from wearable_pipeline.compare import compare_outputs
from wearable_pipeline.devices import (
    DEVICE_COLUMNS, DEVICE_RULES, METADATA_PATH, MALFORMED, MISMATCH, PARTICIPANTS_PATH,
    RECOVERED, SHARED, SILVER_COLUMNS, SILVER_TABLE, TABLE_NAME, UNRECOVERABLE,
    build_device_layer, parse_firmware_label, resolve_firmware,
)

META_COLUMNS = ["participant_id", "device_id", "wear_site", "calibration_date", "firmware_version",
                "device_label"]
D1, D2, D3 = "FBT-0001-AAAAAAAA", "FBT-0002-BBBBBBBB", "FBT-0003-CCCCCCCC"


def label(version: str | None, device: str = D1) -> str:
    middle = f" · fw{version}" if version else ""
    return f"Fitbit Charge 5{middle} · SN:{device}"


def prow(pid: str, device: str) -> dict[str, str]:
    values = [pid, device, "38", "183", "male", "B", "167"]
    return dict(zip(PARTICIPANT_COLUMNS, values))


def mrow(pid: str, device: str, firmware: str = "2.2.0", text: str | None = None) -> dict[str, str]:
    values = [pid, device, "wrist_dominant", "2025-12-24", firmware,
              label("2.1.8", device) if text is None else text]
    return dict(zip(META_COLUMNS, values))


def counts(layer) -> dict[str, int]:
    return {rule: layer.failed[rule] for rule in DEVICE_RULES}


def no_failures(**overrides: int) -> dict[str, int]:
    return {**{rule: 0 for rule in DEVICE_RULES}, **overrides}


def ordered(rejects):
    return sorted(rejects, key=lambda r: r[:3])


# --------------------------------------------------------------------------- constants


def test_rule_constants_are_the_exact_five_device_rules():
    assert set(DEVICE_RULES) == {MISMATCH, SHARED, MALFORMED, RECOVERED, UNRECOVERABLE}
    assert DEVICE_RULES[MISMATCH][:3] == ("device_metadata", "flag", "high")
    assert DEVICE_RULES[SHARED][:3] == ("device_metadata", "flag", "high")
    assert DEVICE_RULES[MALFORMED][:3] == ("device_metadata", "flag", "medium")
    assert DEVICE_RULES[RECOVERED][:3] == ("device_metadata", "convert", "low")
    assert DEVICE_RULES[UNRECOVERABLE][:3] == ("device_metadata", "flag", "medium")


def test_rules_and_silver_ddl_are_registered_in_the_pipeline():
    for rule, spec in DEVICE_RULES.items():
        assert pipeline.RULES[rule] == spec
    ddl = pipeline.TABLE_DDL[SILVER_TABLE]
    assert [part.strip().split()[0] for part in ddl.split(",")] == [n for n, _ in SILVER_COLUMNS]
    assert [part.strip().split()[1] for part in ddl.split(",")] == [t for _, t in SILVER_COLUMNS]


def test_the_gold_table_is_part_of_the_incremental_schema_check():
    assert TABLE_NAME == "gold_participant_devices"
    assert TABLE_NAME in incremental._EXPECTED_TABLES


# --------------------------------------------------------------------------- firmware parsing


@pytest.mark.parametrize("text, expected", [
    ("Fitbit Charge 5 · fw2.2.1 · SN:FBT-0001-9D79801C", "2.2.1"),
    ("Fitbit Charge 5 · fw2.1.8 · SN:X", "2.1.8"),
    ("fw10.0", "10.0"),
    ("fw2.2.1.4", "2.2.1.4"),
    ("fw2.2.1-beta", "2.2.1"),
    ("fw1.0.0 fw2.0.0", "1.0.0"),
    ("", None),
    (None, None),
    ("Fitbit Charge 5", None),
    ("fw2", None),
    ("fw", None),
    ("softfw2.2.1", None),
    ("FW2.2.1", None),
    ("fw2.2.1a", None),
])
def test_parse_firmware_label(text, expected):
    assert parse_firmware_label(text) == expected


@pytest.mark.parametrize("column, text, expected", [
    ("2.2.0", label("2.1.8"), ("2.2.0", "metadata")),
    (" 2.2.1 ", None, ("2.2.1", "metadata")),
    ("", label("2.1.8"), ("2.1.8", "device_label")),
    ("   ", label("2.1.8"), ("2.1.8", "device_label")),
    (None, label("2.1.8"), ("2.1.8", "device_label")),
    ("", "Fitbit Charge 5", (None, "none")),
    ("", "", (None, "none")),
    (None, None, (None, "none")),
])
def test_resolve_firmware(column, text, expected):
    assert resolve_firmware(column, text) == expected


# --------------------------------------------------------------------------- build_device_layer


def test_clean_blank_and_unrecoverable_firmware_rows_are_exact():
    participants = [prow("P001", D1), prow("P002", D2), prow("P003", D3)]
    metadata = [mrow("P001", D1, "2.2.0"), mrow("P002", D2, " "),
                mrow("P003", D3, "", "Fitbit Charge 5")]
    layer = build_device_layer(participants, metadata)
    assert layer.gold == [("P001", D1, "2.2.0"), ("P002", D2, "2.1.8"), ("P003", D3, None)]
    assert layer.silver == [
        ("P001", D1, "2.2.0", label("2.1.8", D1), "2.1.8", "2.2.0", "metadata", None),
        ("P002", D2, " ", label("2.1.8", D2), "2.1.8", "2.1.8", "device_label", RECOVERED),
        ("P003", D3, "", "Fitbit Charge 5", None, None, "none", UNRECOVERABLE),
    ]
    assert counts(layer) == no_failures(**{RECOVERED: 1, UNRECOVERABLE: 1})
    assert layer.checked == 3
    assert ordered(layer.rejects) == [
        (RECOVERED, METADATA_PATH, 2, metadata[1]),
        (UNRECOVERABLE, METADATA_PATH, 3, metadata[2]),
    ]


def test_mismatch_shared_malformed_and_missing_metadata_are_flagged_not_removed():
    participants = [prow("P001", D1), prow("P002", D2), prow("P003", D3), prow("P004", D3),
                    prow("P005", "FBT-5-BAD"), prow("P006", "FBT-0006-FFFFFFFF"),
                    prow("P007", "FBT-0007-HHHHHHHH")]
    metadata = [mrow("P001", D1), mrow("P002", D2), mrow("P003", D3), mrow("P004", D3),
                mrow("P005", "FBT-5-BAD"), mrow("P006", "FBT-0006-GGGGGGGG"),
                mrow("P099", "FBT-0099-ZZZZZZZZ")]
    layer = build_device_layer(participants, metadata)
    assert layer.gold == [
        ("P001", D1, "2.2.0"), ("P002", D2, "2.2.0"), ("P003", D3, "2.2.0"),
        ("P004", D3, "2.2.0"), ("P005", "FBT-5-BAD", "2.2.0"),
        ("P006", "FBT-0006-FFFFFFFF", "2.2.0"), ("P007", "FBT-0007-HHHHHHHH", None)]
    assert counts(layer) == no_failures(**{MISMATCH: 2, SHARED: 2, MALFORMED: 1, UNRECOVERABLE: 1})
    assert layer.checked == 7
    assert ordered(layer.rejects) == [
        (MALFORMED, PARTICIPANTS_PATH, 5, participants[4]),
        (MISMATCH, METADATA_PATH, 6, metadata[5]),
        (MISMATCH, PARTICIPANTS_PATH, 7, participants[6]),
        (SHARED, PARTICIPANTS_PATH, 3, participants[2]),
        (SHARED, PARTICIPANTS_PATH, 4, participants[3]),
        (UNRECOVERABLE, PARTICIPANTS_PATH, 7, participants[6]),
    ]
    assert [r[0] for r in layer.silver] == ["P001", "P002", "P003", "P004", "P005", "P006", "P099"]
    assert all(r[7] is None for r in layer.silver)


def test_blank_participant_device_id_is_malformed_and_kept_raw():
    layer = build_device_layer([prow("P001", "")], [mrow("P001", "")])
    assert layer.gold == [("P001", "", "2.2.0")]
    assert counts(layer) == no_failures(**{MALFORMED: 1})


def test_malformed_pattern_is_dictionary_shaped():
    participants = [prow("P001", "FBT-0001-AAAAAAAA"), prow("P002", "FBT-0002-9D79801C"),
                    prow("P003", "FBT-001-AAAAAAAA"), prow("P004", "FBT-0004-AAAAAAA"),
                    prow("P005", "FBT-0005-aaaaaaaa"), prow("P006", "XFBT-0006-AAAAAAAA"),
                    prow("P007", "FBT-0007-AAAAAAAAA"), prow("P008", " FBT-0008-AAAAAAAA")]
    metadata = [mrow(p["participant_id"], p["device_id"]) for p in participants]
    layer = build_device_layer(participants, metadata)
    assert counts(layer) == no_failures(**{MALFORMED: 6})
    assert sorted(r[2] for r in layer.rejects if r[0] == MALFORMED) == [3, 4, 5, 6, 7, 8]


def test_duplicate_participant_rows_give_one_gold_row_from_the_first_listing():
    participants = [prow("P001", D1), prow("P001", "FBT-0001-ZZZZZZZZ")]
    layer = build_device_layer(participants, [mrow("P001", D1)])
    assert layer.gold == [("P001", D1, "2.2.0")]
    assert layer.checked == 1
    assert counts(layer) == no_failures()


def test_duplicate_metadata_rows_prefer_the_row_with_the_matching_device_id():
    metadata = [mrow("P001", "FBT-0001-ZZZZZZZZ", "1.0.0"), mrow("P001", D1, "2.2.0")]
    layer = build_device_layer([prow("P001", D1)], metadata)
    assert layer.gold == [("P001", D1, "2.2.0")]
    assert counts(layer) == no_failures()
    assert len(layer.silver) == 2


def test_metadata_without_optional_columns_leaves_firmware_null_and_counts_it():
    layer = build_device_layer([prow("P001", D1)], [{"participant_id": "P001", "device_id": D1}])
    assert layer.gold == [("P001", D1, None)]
    assert layer.silver == [("P001", D1, "", "", None, None, "none", UNRECOVERABLE)]
    assert counts(layer) == no_failures(**{UNRECOVERABLE: 1})


def test_a_device_metadata_row_for_an_unenrolled_participant_stays_in_silver_only():
    layer = build_device_layer([prow("P001", D1)], [mrow("P001", D1), mrow("P099", D2)])
    assert [r[0] for r in layer.gold] == ["P001"]
    assert [r[0] for r in layer.silver] == ["P001", "P099"]
    assert counts(layer) == no_failures()


def test_gold_is_sorted_and_independent_of_input_order():
    participants = [prow("P003", D3), prow("P001", D1), prow("P002", D2)]
    metadata = [mrow("P003", D3), mrow("P001", D1), mrow("P002", D2, "")]
    forward = build_device_layer(participants, metadata)
    backward = build_device_layer(participants[::-1], metadata[::-1])
    assert [r[0] for r in forward.gold] == ["P001", "P002", "P003"]
    assert backward.gold == forward.gold
    assert counts(backward) == counts(forward)
    assert build_device_layer(participants, metadata).gold == forward.gold


def test_empty_inputs_give_an_empty_layer():
    layer = build_device_layer([], [])
    assert (layer.gold, layer.silver, layer.rejects, layer.checked) == ([], [], [], 0)
    assert counts(layer) == no_failures()


# --------------------------------------------------------------------------- pipeline fixtures


def devices_data(root: Path, participants=None, metadata=None) -> Path:
    data = make_data(root)
    if participants is not None:
        write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS, participants)
    rows = metadata if metadata is not None else [
        mrow("P001", D1, "2.2.0"), mrow("P002", D2, "")]
    write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
              [list(r.values()) for r in rows])
    return data


def gold_rows(out: Path) -> list[tuple]:
    con = duckdb.connect(str(out / "wearable.duckdb"), read_only=True)
    try:
        return con.execute(f"SELECT * FROM {TABLE_NAME} ORDER BY ALL").fetchall()
    finally:
        con.close()


def sql(out: Path, statement: str, *params) -> None:
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        con.execute(statement, list(params))
    finally:
        con.close()


EXPECTED_GOLD = [("P001", D1, "2.2.0"), ("P002", D2, "2.1.8")]


# --------------------------------------------------------------------------- full run


def test_full_run_writes_one_row_per_participant_with_recovered_firmware(tmp_path):
    result = run(devices_data(tmp_path), tmp_path / "out")
    assert query(result, f"SELECT * FROM {TABLE_NAME} ORDER BY participant_id") == EXPECTED_GOLD
    described = query(result, f"DESCRIBE {TABLE_NAME}")
    assert [(r[0], r[1]) for r in described] == list(DEVICE_COLUMNS)
    assert query(result, f"SELECT count(DISTINCT participant_id), count(*) FROM {TABLE_NAME}") \
        == [(2, 2)]


def test_silver_device_metadata_keeps_raw_values_and_marks_the_derived_one(tmp_path):
    result = run(devices_data(tmp_path), tmp_path / "out")
    described = query(result, f"DESCRIBE {SILVER_TABLE}")
    assert [(r[0], r[1]) for r in described] == list(SILVER_COLUMNS)
    assert query(result, f"SELECT * FROM {SILVER_TABLE} ORDER BY participant_id") == [
        ("P001", D1, "2.2.0", label("2.1.8", D1), "2.1.8", "2.2.0", "metadata", None),
        ("P002", D2, "", label("2.1.8", D2), "2.1.8", "2.1.8", "device_label", RECOVERED),
    ]


def test_quality_results_carry_the_five_device_rules(tmp_path):
    result = run(devices_data(tmp_path), tmp_path / "out")
    rows = query(result, "SELECT rule_id, source, handling, severity, reason, checked_count, "
                         "failed_count, run_id FROM quality_results WHERE source = 'device_metadata'")
    assert {r[0]: (r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in rows} == {
        rule: (*DEVICE_RULES[rule], 2, 1 if rule == RECOVERED else 0, result.run_id)
        for rule in DEVICE_RULES}


def test_flagged_devices_do_not_fail_the_run_and_the_report_lists_them(tmp_path):
    participants = [list(prow("P001", D1).values()), list(prow("P002", D1).values())]
    metadata = [mrow("P001", D1, "", "Fitbit Charge 5"), mrow("P002", D2, "2.2.0")]
    result = run(devices_data(tmp_path, participants, metadata), tmp_path / "out")
    assert query(result, f"SELECT * FROM {TABLE_NAME} ORDER BY participant_id") == [
        ("P001", D1, None), ("P002", D1, "2.2.0")]
    failed = dict(query(result, "SELECT rule_id, failed_count FROM quality_results "
                                "WHERE source = 'device_metadata'"))
    assert failed == no_failures(**{SHARED: 2, MISMATCH: 1, UNRECOVERABLE: 1})
    text = result.report_path.read_text(encoding="utf-8")
    assert "| DEVICE_SHARED | device_metadata | flag | high | 2 | 2 |" in text
    assert "| DEVICE_ID_MISMATCH | device_metadata | flag | high | 2 | 1 |" in text
    assert "| FIRMWARE_UNRECOVERABLE | device_metadata | flag | medium | 2 | 1 |" in text
    assert query(result, "SELECT rule_id, row_ref, raw_values FROM quality_rejects "
                         "WHERE source = 'device_metadata' ORDER BY rule_id, row_ref") == [
        (MISMATCH, f"{METADATA_PATH}:2", json.dumps(metadata[1], ensure_ascii=False)),
        (SHARED, f"{PARTICIPANTS_PATH}:1", json.dumps(prow("P001", D1), ensure_ascii=False)),
        (SHARED, f"{PARTICIPANTS_PATH}:2", json.dumps(prow("P002", D1), ensure_ascii=False)),
        (UNRECOVERABLE, f"{METADATA_PATH}:1", json.dumps(metadata[0], ensure_ascii=False)),
    ]


def test_malformed_device_id_is_kept_raw_in_silver_and_gold(tmp_path):
    participants = [list(prow("P001", "FBT-1-bad").values())]
    metadata = [mrow("P001", "FBT-1-bad")]
    result = run(devices_data(tmp_path, participants, metadata), tmp_path / "out")
    assert query(result, f"SELECT * FROM {TABLE_NAME}") == [("P001", "FBT-1-bad", "2.2.0")]
    assert query(result, "SELECT device_id FROM silver_participants") == [("FBT-1-bad",)]
    assert query(result, "SELECT failed_count FROM quality_results "
                         f"WHERE rule_id = '{MALFORMED}'") == [(1,)]


def test_only_participants_that_pass_enrolment_appear(tmp_path):
    data = devices_data(tmp_path, metadata=[
        mrow("P001", D1), mrow("P002", D2), mrow("P099", D3)])
    result = run(data, tmp_path / "out")
    assert [r[0] for r in query(result, f"SELECT * FROM {TABLE_NAME} ORDER BY 1")] \
        == ["P001", "P002"]


def test_duplicate_participant_listing_gives_one_row_and_keeps_its_existing_flag(tmp_path):
    participants = [list(prow("P001", D1).values()), list(prow("P001", D1).values())]
    result = run(devices_data(tmp_path, participants, [mrow("P001", D1)]), tmp_path / "out")
    assert query(result, f"SELECT * FROM {TABLE_NAME}") == [("P001", D1, "2.2.0")]
    assert query(result, "SELECT failed_count FROM quality_results "
                         "WHERE rule_id = 'PARTICIPANTS_KEY_DUPLICATE'") == [(2,)]


def test_missing_device_metadata_file_fails_before_any_output(tmp_path):
    data = devices_data(tmp_path)
    (data / "health_summaries" / "device_metadata.csv").unlink()
    with pytest.raises(PipelineError, match="required input missing: "
                                            "health_summaries/device_metadata.csv"):
        run(data, tmp_path / "out")
    assert not (tmp_path / "out" / "wearable.duckdb").exists()


def test_device_metadata_without_a_required_column_fails(tmp_path):
    data = devices_data(tmp_path)
    write_csv(data / "health_summaries" / "device_metadata.csv", ["participant_id"], [["P001"]])
    with pytest.raises(PipelineError, match="missing required columns: device_id"):
        run(data, tmp_path / "out")


def test_existing_tables_keep_their_columns_and_the_daily_grain(tmp_path):
    result = run(devices_data(tmp_path), tmp_path / "out")
    assert [r[0] for r in query(result, "DESCRIBE silver_participants")] == list(
        PARTICIPANT_COLUMNS) + ["is_duplicate_key", "quality_flags"]
    assert query(result, "SELECT count(*) FROM gold_clinical_daily") == [(2 * len(WINDOW),)]


# --------------------------------------------------------------------------- reruns


def test_identical_rerun_in_the_same_directory_changes_nothing(tmp_path):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    first = run(data, out)
    before = state(out)
    second = run(data, out)
    assert second.run_id == first.run_id
    assert state(out) == before
    assert gold_rows(out) == EXPECTED_GOLD


def test_two_fresh_runs_are_identical_under_compare(tmp_path):
    data = devices_data(tmp_path)
    run(data, tmp_path / "a")
    run(data, tmp_path / "b")
    assert compare_outputs(tmp_path / "a", tmp_path / "b").identical


def test_compare_includes_the_device_table(tmp_path):
    data = devices_data(tmp_path)
    run(data, tmp_path / "a")
    run(data, tmp_path / "b")
    sql(tmp_path / "b", f"UPDATE {TABLE_NAME} SET firmware_version = '9.9.9' "
                        "WHERE participant_id = 'P001'")
    result = compare_outputs(tmp_path / "a", tmp_path / "b")
    assert not result.identical
    assert [(d.table, d.status, d.left_only_rows, d.right_only_rows)
            for d in result.table_diffs] == [(TABLE_NAME, "rows", 1, 1)]


def test_changed_device_metadata_updates_the_table_in_place(tmp_path):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
              [list(mrow("P001", D1, "2.3.0").values()), list(mrow("P002", D2, "").values())])
    run(data, out)
    assert gold_rows(out) == [("P001", D1, "2.3.0"), ("P002", D2, "2.1.8")]


def test_changed_participant_device_updates_and_removed_participant_disappears(tmp_path):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
              [list(prow("P001", D3).values())])
    write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
              [list(mrow("P001", D3, "2.2.0").values())])
    run(data, out)
    assert gold_rows(out) == [("P001", D3, "2.2.0")]


# --------------------------------------------------------------------------- incremental


def reference(data: Path, tmp_path: Path, name: str = "reference") -> Path:
    out = tmp_path / name
    shutil.rmtree(out, ignore_errors=True)
    run(data, out)
    return out


def test_noop_incremental_leaves_every_table_including_the_device_table_unchanged(tmp_path):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    before = state(out)
    result = inc(data, out)
    assert result.outcome == "noop"
    assert state(out) == before


@pytest.mark.parametrize("change", ["metadata_firmware", "metadata_device", "participant_device"])
def test_input_file_change_rebuilds_and_equals_a_full_run(tmp_path, change):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    before = state(out)
    if change == "metadata_firmware":
        write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
                  [list(mrow("P001", D1, "2.3.0").values()), list(mrow("P002", D2, "").values())])
    elif change == "metadata_device":
        write_csv(data / "health_summaries" / "device_metadata.csv", META_COLUMNS,
                  [list(mrow("P001", D1).values()), list(mrow("P002", D3, "").values())])
    else:
        write_csv(data / "participants" / "participants.csv", PARTICIPANT_COLUMNS,
                  [list(prow("P001", D1).values()), list(prow("P002", D3).values())])
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "shared_input_changed")
    assert state(out) == state(reference(data, tmp_path))
    assert state(out) != before


def test_event_file_change_keeps_the_device_table_and_still_equals_a_full_run(tmp_path):
    data = devices_data(tmp_path)
    write_events(data, "P001", HR, [hr(DAY1, m, 60 + m) for m in range(5)])
    out = tmp_path / "out"
    run(data, out)
    before = gold_rows(out)
    m_corrected(data)
    result = inc(data, out)
    assert result.outcome == "incremental"
    assert gold_rows(out) == before == EXPECTED_GOLD
    assert state(out) == state(reference(data, tmp_path))


@pytest.mark.parametrize("table", [TABLE_NAME, SILVER_TABLE])
def test_an_output_without_the_new_tables_is_rebuilt_in_full(tmp_path, table):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    run(data, out)
    sql(out, f"DROP TABLE {table}")
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "old_schema")
    assert gold_rows(out) == EXPECTED_GOLD
    assert state(out) == state(reference(data, tmp_path))


def test_incremental_into_an_empty_directory_builds_the_device_table(tmp_path):
    data = devices_data(tmp_path)
    out = tmp_path / "out"
    result = inc(data, out)
    assert (result.outcome, result.fallback_reason) == ("full", "no_database")
    assert gold_rows(out) == EXPECTED_GOLD
