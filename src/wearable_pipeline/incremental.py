"""Incremental mode: reprocess only changed raw files (contract: specs/INC-006.md)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import duckdb

from wearable_pipeline.pipeline import DEFAULT_STUDY_DAYS, DEFAULT_STUDY_START

OUTCOME_INCREMENTAL = "incremental"
OUTCOME_NOOP = "noop"
OUTCOME_FULL = "full"

# fallback_reason values (specs/INC-006.md section 5)
REASON_NO_DATABASE = "no_database"
REASON_NO_MANIFEST = "no_manifest"
REASON_OLD_SCHEMA = "old_schema"
REASON_SHARED_INPUT_CHANGED = "shared_input_changed"
REASON_WINDOW_CHANGED = "window_changed"


@dataclass(frozen=True)
class ManifestDiff:
    """Paths of the current raw manifest classified against the stored one (sorted tuples)."""

    new: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]


@dataclass(frozen=True)
class IncrementalResult:
    """Outcome of one ``run_incremental`` call."""

    run_id: str
    database_path: Path
    report_path: Path
    outcome: str  # OUTCOME_INCREMENTAL, OUTCOME_NOOP or OUTCOME_FULL
    fallback_reason: str | None  # None unless outcome is OUTCOME_FULL
    new_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    affected_gold_rows: int  # gold rows recomputed by this run (0 for a no-op)


class DerivedTable(Protocol):
    """Extension point for later gold tables (INC-007, INC-008); see specs/INC-006.md section 9."""

    name: str

    def refresh(
        self,
        con: duckdb.DuckDBPyConnection,
        affected: Mapping[str, frozenset[date]] | None,
    ) -> None:
        """Bring the table up to date inside the caller's open transaction.

        ``affected`` maps participant_id to the local dates whose gold_clinical_daily row was
        recomputed; ``None`` means every row must be rebuilt.
        """


DERIVED_TABLES: list[DerivedTable] = []


def read_event_file(path: Path, relative: str, kind: str) -> list[Any]:
    """Parse one sleep, steps or heart-rate JSON file; the only event parser in incremental mode."""
    raise NotImplementedError


def diff_manifests(
    previous: Mapping[str, str], current: Mapping[str, str]
) -> ManifestDiff:
    """Classify every path of two ``relative_path -> sha256`` mappings."""
    raise NotImplementedError


def changed_days(
    previous: Sequence[tuple[Any, ...]],
    new: Sequence[tuple[Any, ...]],
    date_index: int = 1,
) -> frozenset[date]:
    """Local dates whose multiset of silver rows differs between ``previous`` and ``new``."""
    raise NotImplementedError


def run_incremental(
    data_dir: Path,
    output_dir: Path,
    *,
    study_start: date = DEFAULT_STUDY_START,
    study_days: int = DEFAULT_STUDY_DAYS,
) -> IncrementalResult:
    """Bring ``output_dir`` to the state a full rebuild of ``data_dir`` would give.

    Raises ``PipelineError`` and leaves the previous output unchanged on any failure.
    """
    raise NotImplementedError
