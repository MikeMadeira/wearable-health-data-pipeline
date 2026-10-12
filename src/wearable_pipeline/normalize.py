"""Pure value normalizers shared by the silver builders."""

from __future__ import annotations

from datetime import date


def parse_integer(raw: str) -> int | None:
    """Return the 32-bit signed integer in ``raw``, or ``None`` when it is not one.

    Surrounding whitespace is ignored; see ``specs/INC-002-01.md`` section 4.
    """
    raise NotImplementedError


def parse_iso_date(raw: str) -> date | None:
    """Return the calendar date in ``raw`` when it is ``YYYY-MM-DD``, else ``None``.

    Surrounding whitespace is ignored; no other layout is converted.
    """
    raise NotImplementedError
