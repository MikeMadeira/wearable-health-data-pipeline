"""Read-only logical comparison of two output directories (contract: specs/INC-006.md)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


def compare_outputs(left: Path, right: Path) -> CompareResult:
    """Compare two output directories without modifying either.

    Raises ``PipelineError`` when either directory or its database cannot be read.
    """
    raise NotImplementedError


def format_comparison(result: CompareResult) -> str:
    """Render the result as the stdout text defined in specs/INC-006.md section 8."""
    raise NotImplementedError
