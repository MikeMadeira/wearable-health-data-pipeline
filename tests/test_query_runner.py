"""INC-010 read-only query runner tests (see specs/INC-010.md section 4).

Synthetic databases and SQL files built in ``tmp_path`` only. Every test fails with
``NotImplementedError`` from the ``query_runner`` stub until the implementation exists.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from wearable_pipeline.query_runner import (
    DEFAULT_DATABASE, DEFAULT_SQL_PATH, QueryResult, QueryRunnerError, collect_sql_files,
    format_results, main, run_queries,
)

ROOT = Path(__file__).resolve().parent.parent


def make_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE gold_demo (k VARCHAR, v INTEGER)")
        con.execute("INSERT INTO gold_demo VALUES ('b', 2), ('a', 1), ('c', NULL)")
    finally:
        con.close()
    return path


def write_sql(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def snapshot(folder: Path) -> dict[str, str]:
    """File name -> SHA-256 of every file below ``folder`` (detects any write or new file)."""
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    db_dir = tmp_path / "output"
    db_dir.mkdir()
    return make_db(db_dir / "wearable.duckdb"), tmp_path / "queries"


# --------------------------------------------------------------------------- constants


def test_defaults_are_the_pipeline_database_and_the_queries_directory():
    assert DEFAULT_DATABASE == Path("output/wearable.duckdb")
    assert DEFAULT_SQL_PATH == Path("queries")


# --------------------------------------------------------------------------- collect_sql_files


def test_collect_returns_a_single_file_as_is(tmp_path: Path):
    path = write_sql(tmp_path, "any_name.txt", "SELECT 1")
    assert collect_sql_files(path) == [path]


def test_collect_directory_is_sorted_non_recursive_and_sql_only(tmp_path: Path):
    folder = tmp_path / "q"
    for name in ("b.sql", "a.sql", "c.SQL", "notes.md"):
        write_sql(folder, name, "SELECT 1")
    write_sql(folder / "nested", "z.sql", "SELECT 1")
    assert collect_sql_files(folder) == [folder / "a.sql", folder / "b.sql"]


def test_collect_missing_path_and_directory_without_sql_raise(tmp_path: Path):
    with pytest.raises(QueryRunnerError, match="not found"):
        collect_sql_files(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(QueryRunnerError, match=r"no \.sql files"):
        collect_sql_files(tmp_path / "empty")


# --------------------------------------------------------------------------- run_queries


def test_run_returns_columns_and_rows_in_result_order(env):
    db, queries = env
    sql = write_sql(queries, "one.sql", "SELECT k, v FROM gold_demo ORDER BY k;")
    assert run_queries(db, sql) == [
        QueryResult("one.sql", ("k", "v"), (("a", 1), ("b", 2), ("c", None)))]


def test_run_executes_a_directory_in_file_name_order(env):
    db, queries = env
    write_sql(queries, "b.sql", "SELECT 2 AS n")
    write_sql(queries, "a.sql", "-- comment first\nSELECT 1 AS n")
    results = run_queries(db, queries)
    assert [(r.name, r.rows) for r in results] == [("a.sql", ((1,),)), ("b.sql", ((2,),))]


def test_run_is_read_only_a_write_query_fails_and_changes_nothing(env):
    db, queries = env
    write_sql(queries, "write.sql", "CREATE TABLE gold_new AS SELECT 1 AS x")
    before = snapshot(db.parent)
    with pytest.raises(QueryRunnerError, match="write.sql"):
        run_queries(db, queries)
    assert snapshot(db.parent) == before
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM information_schema.tables "
                           "WHERE table_name = 'gold_new'").fetchone()[0] == 0
    finally:
        con.close()


def test_a_successful_run_leaves_the_output_directory_byte_identical(env):
    db, queries = env
    sql = write_sql(queries, "one.sql", "SELECT count(*) AS n FROM gold_demo")
    before = snapshot(db.parent)
    assert run_queries(db, sql)[0].rows == ((3,),)
    assert snapshot(db.parent) == before


def test_run_with_a_missing_database_raises_and_creates_nothing(env, tmp_path: Path):
    _, queries = env
    sql = write_sql(queries, "one.sql", "SELECT 1")
    missing = tmp_path / "nowhere" / "wearable.duckdb"
    with pytest.raises(QueryRunnerError, match="database not found"):
        run_queries(missing, sql)
    assert not missing.exists() and not missing.parent.exists()


def test_run_with_a_missing_sql_path_raises(env):
    db, queries = env
    with pytest.raises(QueryRunnerError, match="not found"):
        run_queries(db, queries / "missing.sql")


def test_run_names_the_failing_file_for_an_invalid_query(env):
    db, queries = env
    write_sql(queries, "good.sql", "SELECT 1 AS n")
    write_sql(queries, "bad.sql", "SELECT * FROM gold_no_such_table")
    with pytest.raises(QueryRunnerError, match="bad.sql"):
        run_queries(db, queries)


def test_run_rejects_an_empty_or_comment_only_sql_file(env):
    db, queries = env
    write_sql(queries, "empty.sql", "-- nothing to run\n")
    with pytest.raises(QueryRunnerError, match="empty.sql"):
        run_queries(db, queries / "empty.sql")


# --------------------------------------------------------------------------- format_results


def test_format_results_exact_text_for_one_result():
    result = QueryResult("one.sql", ("a", "b", "c"), ((1, "x", None), (2.5, "y", True)))
    assert format_results([result]) == (
        "# one.sql\n"
        "| a | b | c |\n"
        "| --- | --- | --- |\n"
        "| 1 | x | NULL |\n"
        "| 2.5 | y | True |\n")


def test_format_results_separates_results_with_a_blank_line_and_keeps_empty_results():
    results = [QueryResult("a.sql", ("n",), ((1,),)), QueryResult("b.sql", ("m",), ())]
    assert format_results(results) == (
        "# a.sql\n| n |\n| --- |\n| 1 |\n"
        "\n"
        "# b.sql\n| m |\n| --- |\n")
    assert format_results([]) == ""


# --------------------------------------------------------------------------- main


def test_main_prints_the_formatted_results_and_returns_zero(env, capsys):
    db, queries = env
    sql = write_sql(queries, "one.sql", "SELECT k, v FROM gold_demo ORDER BY k")
    assert main([str(sql), "--database", str(db)]) == 0
    captured = capsys.readouterr()
    assert captured.out == format_results(run_queries(db, sql))
    assert captured.out.startswith("# one.sql\n| k | v |\n| --- | --- |\n| a | 1 |\n")
    assert captured.err == ""


def test_main_is_deterministic(env, capsys):
    db, queries = env
    write_sql(queries, "one.sql", "SELECT k, v FROM gold_demo ORDER BY k")
    assert main([str(queries), "--database", str(db)]) == 0
    first = capsys.readouterr().out
    assert main([str(queries), "--database", str(db)]) == 0
    assert capsys.readouterr().out == first != ""


def test_main_defaults_to_output_wearable_duckdb_and_the_queries_directory(env, tmp_path, monkeypatch, capsys):
    _, queries = env
    write_sql(queries, "one.sql", "SELECT count(*) AS n FROM gold_demo")
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert capsys.readouterr().out == "# one.sql\n| n |\n| --- |\n| 3 |\n"


def test_main_missing_database_returns_one_with_a_clear_error_and_no_traceback(env, tmp_path, capsys):
    _, queries = env
    sql = write_sql(queries, "one.sql", "SELECT 1")
    missing = tmp_path / "absent.duckdb"
    assert main([str(sql), "--database", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ") and "database not found" in captured.err
    assert str(missing) in captured.err and "Traceback" not in captured.err
    assert not missing.exists()


def test_main_missing_sql_path_returns_one(env, tmp_path, capsys):
    db, _ = env
    assert main([str(tmp_path / "nope.sql"), "--database", str(db)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("error: ") and "not found" in captured.err


def test_main_invalid_query_returns_one_and_prints_no_partial_results(env, capsys):
    db, queries = env
    write_sql(queries, "a_good.sql", "SELECT 1 AS n")
    write_sql(queries, "b_bad.sql", "SELEC nonsense")
    assert main([str(queries), "--database", str(db)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ") and "b_bad.sql" in captured.err
    assert "Traceback" not in captured.err


def test_main_unknown_argument_is_a_usage_error_with_exit_code_two(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--no-such-option"])
    assert caught.value.code == 2


def test_module_runs_as_python_dash_m_and_exit_codes_reach_the_shell(env):
    db, queries = env
    write_sql(queries, "one.sql", "SELECT k FROM gold_demo ORDER BY k")
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    command = [sys.executable, "-m", "wearable_pipeline.query_runner"]
    done = subprocess.run(command + [str(queries), "--database", str(db)], env=environment,
                          capture_output=True, text=True, cwd=db.parent, check=False)
    assert done.returncode == 0 and done.stderr == ""
    assert done.stdout == "# one.sql\n| k |\n| --- |\n| a |\n| b |\n| c |\n"
    failed = subprocess.run(command + [str(queries), "--database", str(db.parent / "no.duckdb")],
                            env=environment, capture_output=True, text=True, cwd=db.parent,
                            check=False)
    assert failed.returncode == 1 and failed.stdout == ""
    assert failed.stderr.startswith("error: ") and "Traceback" not in failed.stderr
