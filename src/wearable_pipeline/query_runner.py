"""Read-only analytical SQL runner: ``python -m wearable_pipeline.query_runner`` (specs/INC-010.md 4).

Separate from the pipeline commands: it never writes the database, never creates tables
and never imports the pipeline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    raise NotImplementedError


def run_queries(database: Path, sql_path: Path) -> list[QueryResult]:
    """Run every SQL file of ``sql_path`` on ``database`` opened read-only, in file-name order.

    Raise ``QueryRunnerError`` for a missing database, a missing path, an empty SQL file or
    a failing query (the message names the file). Nothing is created or modified.
    """
    raise NotImplementedError


def format_results(results: Sequence[QueryResult]) -> str:
    """Deterministic text of the results (specs/INC-010.md 4.3), ending with one newline."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; print results to stdout and return 0, or print ``error: ...`` to stderr
    and return 1 on a ``QueryRunnerError``. Nothing is printed to stdout on failure."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
