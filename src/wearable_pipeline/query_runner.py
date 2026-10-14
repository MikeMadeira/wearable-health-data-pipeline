"""Read-only analytical SQL runner: ``python -m wearable_pipeline.query_runner`` (specs/INC-010.md 4).

Separate from the pipeline commands: it never writes the database, never creates tables
and never imports the pipeline.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_DATABASE = Path("output/wearable.duckdb")
DEFAULT_SQL_PATH = Path("queries")


class QueryRunnerError(Exception):
    """A missing database, a missing or empty SQL path, or a query that failed."""


@dataclass(frozen=True)
class QueryResult:
    """Result of one SQL file: its file name, the result column names and all rows in order."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def collect_sql_files(sql_path: Path) -> list[Path]:
    """The SQL files to run: ``sql_path`` itself, or the ``*.sql`` files directly inside it,
    sorted by file name (not recursive). Raise ``QueryRunnerError`` when the path is missing
    or a directory holds no ``.sql`` file."""
    if sql_path.is_dir():
        files = sorted((p for p in sql_path.iterdir() if p.is_file() and p.suffix == ".sql"),
                       key=lambda p: p.name)
        if not files:
            raise QueryRunnerError(f"no .sql files in {sql_path}")
        return files
    if sql_path.is_file():
        return [sql_path]
    raise QueryRunnerError(f"sql path not found: {sql_path}")


def run_queries(database: Path, sql_path: Path) -> list[QueryResult]:
    """Run every SQL file of ``sql_path`` on ``database`` opened read-only, in file-name order.

    Raise ``QueryRunnerError`` for a missing database, a missing path, an empty SQL file or
    a failing query (the message names the file). Nothing is created or modified.
    """
    if not database.is_file():
        raise QueryRunnerError(f"database not found: {database}")
    files = collect_sql_files(sql_path)
    texts = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if not _strip_comments(text).strip():
            raise QueryRunnerError(f"query failed in {path.name}: the file holds no SQL statement")
        texts.append(text)
    try:
        con = duckdb.connect(str(database), read_only=True)
    except duckdb.Error as error:
        raise QueryRunnerError(f"cannot open database {database}: {error}") from error
    results: list[QueryResult] = []
    try:
        for path, text in zip(files, texts):
            try:
                cursor = con.execute(text)
                columns = tuple(column[0] for column in cursor.description)
                rows = tuple(tuple(row) for row in cursor.fetchall())
            except duckdb.Error as error:
                raise QueryRunnerError(f"query failed in {path.name}: {error}") from error
            results.append(QueryResult(path.name, columns, rows))
    finally:
        con.close()
    return results


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def format_results(results: Sequence[QueryResult]) -> str:
    """Deterministic text of the results (specs/INC-010.md 4.3), ending with one newline."""
    blocks = []
    for result in results:
        lines = [f"# {result.name}",
                 "| " + " | ".join(result.columns) + " |",
                 "| " + " | ".join("---" for _ in result.columns) + " |"]
        for row in result.rows:
            lines.append("| " + " | ".join("NULL" if value is None else str(value)
                                           for value in row) + " |")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; print results to stdout and return 0, or print ``error: ...`` to stderr
    and return 1 on a ``QueryRunnerError``. Nothing is printed to stdout on failure."""
    parser = argparse.ArgumentParser(
        prog="python -m wearable_pipeline.query_runner",
        description="Run read-only analytical SQL files against the pipeline DuckDB database.")
    parser.add_argument("sql_path", nargs="?", type=Path, default=DEFAULT_SQL_PATH,
                        metavar="SQL_PATH", help="a .sql file or a directory of .sql files")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE,
                        help="DuckDB database file (opened read-only)")
    args = parser.parse_args(argv)
    try:
        text = format_results(run_queries(args.database, args.sql_path))
    except QueryRunnerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
