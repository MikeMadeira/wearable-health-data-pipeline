"""Read-only logical comparison of two output directories (contract: specs/INC-006.md)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from wearable_pipeline.pipeline import PipelineError


@dataclass(frozen=True)
class TableDiff:
    """One differing table. Counts and column names only; never row values."""

    table: str
    status: str  # "missing_left", "missing_right", "columns" or "rows"
    columns: tuple[str, ...]  # columns present on one side only or typed differently (sorted)
    left_rows: int | None
    right_rows: int | None
    left_only_rows: int  # rows (multiset) only on the left; 0 unless status is "rows"
    right_only_rows: int


@dataclass(frozen=True)
class CompareResult:
    """Result of ``compare_outputs``; ``identical`` is true only when both lists are empty."""

    identical: bool
    table_diffs: tuple[TableDiff, ...]  # sorted by table name
    report_diffs: tuple[tuple[str, str], ...]  # (relative path, "differs"|"missing_left"|"missing_right")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _database(directory: Path) -> Path:
    database = Path(directory) / "wearable.duckdb"
    if not Path(directory).is_dir() or not database.is_file():
        raise PipelineError(f"no output database in {directory}")
    return database


def _tables(con: duckdb.DuckDBPyConnection, catalog: str) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog = ? "
        "AND table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY 1",
        [catalog]).fetchall()]


def _columns(con: duckdb.DuckDBPyConnection, catalog: str, table: str) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in con.execute(
        f"DESCRIBE {catalog}.main.{_quote(table)}").fetchall()]


def _column_names(left: list[tuple[str, str]], right: list[tuple[str, str]]) -> tuple[str, ...]:
    differing = set(left).symmetric_difference(right)
    names = {name for name, _ in differing}
    if not names:  # same columns in a different order: name the ones that moved
        names = {a[0] for a, b in zip(left, right) if a != b}
    return tuple(sorted(names))


def _report_files(directory: Path) -> dict[str, Path]:
    root = Path(directory) / "quality"
    if not root.is_dir():
        return {}
    return {f"quality/{p.relative_to(root).as_posix()}": p
            for p in root.rglob("*") if p.is_file()}


def compare_outputs(left: Path, right: Path) -> CompareResult:
    """Compare two output directories without modifying either.

    Raises ``PipelineError`` when either directory or its database cannot be read.
    """
    left_db, right_db = _database(left), _database(right)
    same = left_db.resolve() == right_db.resolve()
    table_diffs: list[TableDiff] = []
    con = duckdb.connect(":memory:")
    try:
        try:
            con.execute(f"ATTACH '{left_db.resolve().as_posix().replace(chr(39), chr(39) * 2)}' "
                        "AS l (READ_ONLY)")
            if not same:
                con.execute(
                    f"ATTACH '{right_db.resolve().as_posix().replace(chr(39), chr(39) * 2)}' "
                    "AS r (READ_ONLY)")
            right_alias = "l" if same else "r"
            left_tables, right_tables = set(_tables(con, "l")), set(_tables(con, right_alias))
            for table in sorted(left_tables | right_tables):
                if table not in right_tables:
                    table_diffs.append(TableDiff(table, "missing_right", (), None, None, 0, 0))
                elif table not in left_tables:
                    table_diffs.append(TableDiff(table, "missing_left", (), None, None, 0, 0))
                else:
                    diff = _compare_table(con, right_alias, table)
                    if diff is not None:
                        table_diffs.append(diff)
        except duckdb.Error as error:
            raise PipelineError(f"cannot read output database: {error}") from error
    finally:
        con.close()

    report_diffs: list[tuple[str, str]] = []
    try:
        left_files, right_files = _report_files(left), _report_files(right)
        for path in sorted(left_files.keys() | right_files.keys()):
            if path not in right_files:
                report_diffs.append((path, "missing_right"))
            elif path not in left_files:
                report_diffs.append((path, "missing_left"))
            elif left_files[path].read_bytes() != right_files[path].read_bytes():
                report_diffs.append((path, "differs"))
    except OSError as error:
        raise PipelineError(f"cannot read quality report: {error}") from error
    return CompareResult(not table_diffs and not report_diffs, tuple(table_diffs),
                         tuple(report_diffs))


def _compare_table(con: duckdb.DuckDBPyConnection, right_alias: str,
                   table: str) -> TableDiff | None:
    left_columns = _columns(con, "l", table)
    right_columns = _columns(con, right_alias, table)
    if left_columns != right_columns:
        return TableDiff(table, "columns", _column_names(left_columns, right_columns),
                         None, None, 0, 0)
    left_ref, right_ref = f"l.main.{_quote(table)}", f"{right_alias}.main.{_quote(table)}"
    left_rows = con.execute(f"SELECT count(*) FROM {left_ref}").fetchone()[0]
    right_rows = con.execute(f"SELECT count(*) FROM {right_ref}").fetchone()[0]
    left_only = con.execute(
        f"SELECT count(*) FROM (SELECT * FROM {left_ref} EXCEPT ALL SELECT * FROM {right_ref})"
    ).fetchone()[0]
    right_only = con.execute(
        f"SELECT count(*) FROM (SELECT * FROM {right_ref} EXCEPT ALL SELECT * FROM {left_ref})"
    ).fetchone()[0]
    if left_only == 0 and right_only == 0:
        return None
    return TableDiff(table, "rows", (), left_rows, right_rows, left_only, right_only)


def format_comparison(result: CompareResult) -> str:
    """Render the result as the stdout text defined in specs/INC-006.md section 8."""
    if result.identical:
        return "identical\n"
    lines = ["differ"]
    for diff in result.table_diffs:
        if diff.status == "missing_left":
            lines.append(f"table {diff.table}: missing in left")
        elif diff.status == "missing_right":
            lines.append(f"table {diff.table}: missing in right")
        elif diff.status == "columns":
            lines.append(f"table {diff.table}: columns differ: {','.join(diff.columns)}")
        else:
            lines.append(
                f"table {diff.table}: rows differ: left_rows={diff.left_rows} "
                f"right_rows={diff.right_rows} left_only={diff.left_only_rows} "
                f"right_only={diff.right_only_rows}")
    for path, status in result.report_diffs:
        text = {"differs": "differs", "missing_left": "missing in left",
                "missing_right": "missing in right"}[status]
        lines.append(f"report {path}: {text}")
    return "\n".join(lines) + "\n"
