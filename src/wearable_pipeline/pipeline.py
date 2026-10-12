"""Walking-skeleton pipeline: manifest, silver survey/participants, quality, gold daily."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_STUDY_START = date(2026, 1, 8)
DEFAULT_STUDY_DAYS = 28


class PipelineError(Exception):
    """Raised for I/O and contract failures; never for field-level data defects."""


@dataclass(frozen=True)
class RunResult:
    """Identifiers and output locations of one pipeline run."""

    run_id: str
    database_path: Path
    report_path: Path


def run_pipeline(
    data_dir: Path,
    output_dir: Path,
    *,
    study_start: date = DEFAULT_STUDY_START,
    study_days: int = DEFAULT_STUDY_DAYS,
) -> RunResult:
    """Rebuild derived layers from raw files and MERGE gold_clinical_daily.

    Raises PipelineError before touching any table when inputs violate the contract.
    """
    raise NotImplementedError
