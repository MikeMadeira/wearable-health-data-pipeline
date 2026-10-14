"""Command-line interface: ``python -m wearable_pipeline run``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from wearable_pipeline.compare import compare_outputs, format_comparison
from wearable_pipeline.incremental import run_incremental
from wearable_pipeline.pipeline import PipelineError, run_pipeline


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return 0 on success and 1 on I/O or contract failure."""
    parser = argparse.ArgumentParser(prog="wearable_pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="rebuild derived layers and merge gold")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--output-dir", type=Path, default=Path("output"))
    run.add_argument("--mode", choices=("full", "incremental"), default="full",
                     help="full rebuild (default) or reprocess only changed raw files")
    compare = commands.add_parser("compare", help="check two output directories are identical")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            result = compare_outputs(args.left, args.right)
            text = format_comparison(result)
        elif args.mode == "incremental":
            outcome = run_incremental(args.data_dir, args.output_dir)
        else:
            result = run_pipeline(args.data_dir, args.output_dir)
    except PipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.command == "compare":
        print(text, end="")
        return 0 if result.identical else 1
    if args.mode == "incremental":
        print(f"run_id={outcome.run_id} database={outcome.database_path} "
              f"report={outcome.report_path} mode={outcome.outcome}")
        if outcome.fallback_reason is not None:
            print(f"fallback: full rebuild ({outcome.fallback_reason})")
        return 0
    print(f"run_id={result.run_id} database={result.database_path} report={result.report_path}")
    return 0
