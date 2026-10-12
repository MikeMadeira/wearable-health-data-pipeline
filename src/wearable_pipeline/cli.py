"""Command-line interface: ``python -m wearable_pipeline run``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from wearable_pipeline.pipeline import PipelineError, run_pipeline


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return 0 on success and 1 on I/O or contract failure."""
    parser = argparse.ArgumentParser(prog="wearable_pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="rebuild derived layers and merge gold")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--output-dir", type=Path, default=Path("output"))
    args = parser.parse_args(argv)
    try:
        result = run_pipeline(args.data_dir, args.output_dir)
    except PipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"run_id={result.run_id} database={result.database_path} report={result.report_path}")
    return 0
