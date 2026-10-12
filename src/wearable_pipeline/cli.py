"""Command-line interface: ``python -m wearable_pipeline run``."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; return 0 on success and 1 on I/O or contract failure."""
    raise NotImplementedError
