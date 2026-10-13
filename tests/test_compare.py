"""Behavior tests for the INC-006 compare command (see specs/INC-006.md). Fixtures are synthetic."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from tests.test_walking_skeleton import make_data, run, tree_hashes
from wearable_pipeline import PipelineError, main
from wearable_pipeline.compare import CompareResult, TableDiff, compare_outputs, format_comparison

# Raw participant values that must never appear in compare output.
RAW_VALUES = ["P001", "P002", "FBT-0001-AAAAAAAA", "FBT-0002-BBBBBBBB"]


def outputs(tmp_path: Path) -> tuple[Path, Path]:
    """Two independent full runs on the same synthetic inputs."""
    data = make_data(tmp_path)
    left, right = tmp_path / "left", tmp_path / "right"
    run(data, left)
    run(data, right)
    return left, right


def edit_db(out: Path, *statements: str) -> None:
    con = duckdb.connect(str(out / "wearable.duckdb"))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()


def compare(left: Path, right: Path, capsys) -> tuple[int, str, str]:
    code = main(["compare", str(left), str(right)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def assert_no_raw_values(text: str) -> None:
    for value in RAW_VALUES:
        assert value not in text


# ---------------------------------------------------------------- identical

def test_independent_runs_on_the_same_inputs_are_identical(tmp_path, capsys):
    left, right = outputs(tmp_path)
    code, out, err = compare(left, right, capsys)
    assert (code, out.strip(), err) == (0, "identical", "")


def test_an_output_is_identical_to_itself_and_to_its_copy(tmp_path, capsys):
    left, _ = outputs(tmp_path)
    copy = tmp_path / "copy"
    shutil.copytree(left, copy)
    assert compare(left, left, capsys)[0] == 0
    assert compare(left, copy, capsys)[0] == 0


def test_row_order_does_not_matter(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right,
            "CREATE TABLE m AS SELECT * FROM raw_manifest ORDER BY relative_path DESC",
            "DROP TABLE raw_manifest", "ALTER TABLE m RENAME TO raw_manifest")
    code, out, _ = compare(left, right, capsys)
    assert (code, out.strip()) == (0, "identical")


def test_compare_result_for_identical_outputs(tmp_path):
    left, right = outputs(tmp_path)
    result = compare_outputs(left, right)
    assert isinstance(result, CompareResult)
    assert result.identical is True
    assert result.table_diffs == () and result.report_diffs == ()
    assert format_comparison(result).strip() == "identical"


# ---------------------------------------------------------------- differing rows

def test_differing_rows_are_reported_with_counts_only(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "UPDATE gold_clinical_daily SET fatigue_score = 5 "
                   "WHERE participant_id = 'P001' AND local_date = DATE '2026-01-08'")
    code, out, err = compare(left, right, capsys)
    assert code == 1
    assert out.splitlines()[0] == "differ"
    assert ("table gold_clinical_daily: rows differ: left_rows=6 right_rows=6 "
            "left_only=1 right_only=1") in out.splitlines()
    assert [line for line in out.splitlines() if line.startswith("table ")] == [
        line for line in out.splitlines() if "gold_clinical_daily" in line]
    assert_no_raw_values(out + err)


def test_extra_and_missing_rows_are_counted_as_a_multiset(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "INSERT INTO raw_manifest SELECT * FROM raw_manifest LIMIT 1",
            "INSERT INTO raw_manifest SELECT * FROM raw_manifest LIMIT 1")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "table raw_manifest: rows differ: left_rows=6 right_rows=8 left_only=0 right_only=2" \
        in out.splitlines()


def test_the_result_object_carries_the_same_counts(tmp_path):
    left, right = outputs(tmp_path)
    edit_db(right, "DELETE FROM silver_wellness_survey")
    result = compare_outputs(left, right)
    assert result.identical is False
    assert [d.table for d in result.table_diffs] == ["silver_wellness_survey"]
    diff = result.table_diffs[0]
    assert isinstance(diff, TableDiff)
    assert diff.status == "rows"
    assert diff.right_rows == 0 and diff.left_only_rows == diff.left_rows
    assert diff.right_only_rows == 0


# ---------------------------------------------------------------- differing columns

def test_differing_columns_are_named_and_rows_are_not_compared(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "ALTER TABLE silver_participants ADD COLUMN extra_column INTEGER")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "table silver_participants: columns differ: extra_column" in out.splitlines()
    assert not any("silver_participants: rows differ" in line for line in out.splitlines())


def test_a_changed_column_type_is_a_column_difference(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "ALTER TABLE raw_manifest ALTER COLUMN size_bytes TYPE VARCHAR")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "table raw_manifest: columns differ: size_bytes" in out.splitlines()


# ---------------------------------------------------------------- report bytes

def test_differing_report_bytes_are_reported(tmp_path, capsys):
    left, right = outputs(tmp_path)
    with (right / "quality" / "report.md").open("ab") as handle:
        handle.write(b" ")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "report quality/report.md: differs" in out.splitlines()


def test_a_missing_report_file_is_a_difference(tmp_path, capsys):
    left, right = outputs(tmp_path)
    (right / "quality" / "report.md").unlink()
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "report quality/report.md: missing in right" in out.splitlines()


def test_every_file_under_quality_is_compared(tmp_path, capsys):
    left, right = outputs(tmp_path)
    (left / "quality" / "extra.md").write_bytes(b"x")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "report quality/extra.md: missing in right" in out.splitlines()


# ---------------------------------------------------------------- tables

def test_a_missing_table_is_reported_for_either_side(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "DROP TABLE silver_steps_minute")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "table silver_steps_minute: missing in right" in out.splitlines()
    code, out, _ = compare(right, left, capsys)
    assert code == 1
    assert "table silver_steps_minute: missing in left" in out.splitlines()


def test_tables_are_discovered_dynamically(tmp_path, capsys):
    left, right = outputs(tmp_path)
    for out in (left, right):
        edit_db(out, "CREATE TABLE gold_future_weekly (k VARCHAR, v INTEGER)",
                "INSERT INTO gold_future_weekly VALUES ('a', 1)")
    assert compare(left, right, capsys)[0] == 0
    edit_db(right, "UPDATE gold_future_weekly SET v = 2")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "table gold_future_weekly: rows differ: left_rows=1 right_rows=1 " \
           "left_only=1 right_only=1" in out.splitlines()


def test_a_table_that_exists_on_one_side_only_is_reported(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(left, "CREATE TABLE gold_cohort_future (k INTEGER)")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    assert "table gold_cohort_future: missing in right" in out.splitlines()


def test_output_lists_tables_in_name_order_and_is_deterministic(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "DELETE FROM silver_wellness_survey", "DELETE FROM raw_manifest",
            "UPDATE gold_clinical_daily SET fatigue_score = 4")
    first = compare(left, right, capsys)
    second = compare(left, right, capsys)
    assert first == second
    names = [line.split()[1].rstrip(":") for line in first[1].splitlines()
             if line.startswith("table ")]
    assert names == sorted(names) and len(names) == 3


def test_several_differences_are_all_reported(tmp_path, capsys):
    left, right = outputs(tmp_path)
    edit_db(right, "DROP TABLE silver_steps_minute",
            "ALTER TABLE silver_participants ADD COLUMN extra_column INTEGER",
            "UPDATE gold_clinical_daily SET stress_score = 1")
    (right / "quality" / "report.md").write_bytes(b"changed")
    code, out, _ = compare(left, right, capsys)
    assert code == 1
    text = out.splitlines()
    assert "table silver_steps_minute: missing in right" in text
    assert "table silver_participants: columns differ: extra_column" in text
    assert any(line.startswith("table gold_clinical_daily: rows differ") for line in text)
    assert "report quality/report.md: differs" in text


# ---------------------------------------------------------------- unreadable

def test_a_missing_directory_exits_one_with_a_message_on_stderr(tmp_path, capsys):
    left, _ = outputs(tmp_path)
    code, out, err = compare(left, tmp_path / "absent", capsys)
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")
    assert not (tmp_path / "absent").exists()


def test_a_directory_without_a_database_exits_one(tmp_path, capsys):
    left, _ = outputs(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    code, out, err = compare(empty, left, capsys)
    assert code == 1 and out == "" and err.startswith("error: ")
    assert list(empty.iterdir()) == []


def test_a_corrupt_database_exits_one_and_is_not_modified(tmp_path, capsys):
    left, right = outputs(tmp_path)
    (right / "wearable.duckdb").write_bytes(b"definitely not a database" * 100)
    before = tree_hashes(right)
    code, out, err = compare(left, right, capsys)
    assert code == 1 and err.startswith("error: ")
    assert tree_hashes(right) == before


def test_a_file_in_place_of_a_directory_exits_one(tmp_path, capsys):
    left, _ = outputs(tmp_path)
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    assert compare(left, not_a_dir, capsys)[0] == 1


def test_compare_outputs_raises_pipeline_error_when_unreadable(tmp_path):
    left, _ = outputs(tmp_path)
    with pytest.raises(PipelineError):
        compare_outputs(left, tmp_path / "absent")
    with pytest.raises(PipelineError):
        compare_outputs(tmp_path / "absent", left)


def test_wrong_argument_count_is_an_argument_error(tmp_path):
    with pytest.raises(SystemExit) as raised:
        main(["compare", str(tmp_path)])
    assert raised.value.code == 2


# ---------------------------------------------------------------- read-only and privacy

@pytest.mark.parametrize("variant", ["identical", "rows", "columns", "report", "missing"])
def test_compare_never_modifies_either_output(tmp_path, capsys, variant):
    left, right = outputs(tmp_path)
    if variant == "rows":
        edit_db(right, "UPDATE gold_clinical_daily SET fatigue_score = 5")
    elif variant == "columns":
        edit_db(right, "ALTER TABLE raw_manifest ADD COLUMN extra INTEGER")
    elif variant == "report":
        (right / "quality" / "report.md").write_bytes(b"changed")
    elif variant == "missing":
        edit_db(right, "DROP TABLE quality_results")
    before = (tree_hashes(left), tree_hashes(right))
    names = (sorted(p.name for p in left.iterdir()), sorted(p.name for p in right.iterdir()))
    code, _, _ = compare(left, right, capsys)
    assert code == (0 if variant == "identical" else 1)
    assert (tree_hashes(left), tree_hashes(right)) == before
    assert (sorted(p.name for p in left.iterdir()),
            sorted(p.name for p in right.iterdir())) == names


@pytest.mark.parametrize("variant", ["rows", "columns", "missing", "report"])
def test_no_raw_participant_value_is_ever_printed(tmp_path, capsys, variant):
    left, right = outputs(tmp_path)
    if variant == "rows":
        edit_db(right, "UPDATE silver_participants SET device_id = 'changed'",
                "UPDATE silver_wellness_survey SET participant_id = 'changed'",
                "UPDATE raw_manifest SET sha256 = 'changed'")
    elif variant == "columns":
        edit_db(right, "ALTER TABLE silver_participants ADD COLUMN extra INTEGER")
    elif variant == "missing":
        edit_db(right, "DROP TABLE silver_wellness_survey")
    else:
        (right / "quality" / "report.md").write_bytes(b"P001 changed")
    code, out, err = compare(left, right, capsys)
    assert code == 1
    assert_no_raw_values(out + err)
    assert "changed" not in out + err


def test_compare_compares_every_table_of_a_pipeline_output(tmp_path):
    left, right = outputs(tmp_path)
    con = duckdb.connect(str(left / "wearable.duckdb"), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()]
    finally:
        con.close()
    assert {"raw_manifest", "silver_participants", "quality_results", "quality_rejects",
            "gold_clinical_daily"} <= set(tables)
    checked = 0
    for table in tables:
        copy = tmp_path / f"copy-{table}"
        shutil.copytree(right, copy)
        con = duckdb.connect(str(copy / "wearable.duckdb"))
        try:
            had_rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0
            con.execute(f"DELETE FROM {table}")
        finally:
            con.close()
        if had_rows:
            checked += 1
            assert [d.table for d in compare_outputs(left, copy).table_diffs] == [table]
    assert checked >= 5
