"""Pure value normalizers shared by the silver builders."""

from __future__ import annotations

import re
from datetime import date, datetime

_INT = re.compile(r"[+-]?[0-9]+")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1


def parse_integer(raw: str) -> int | None:
    """Return the 32-bit signed integer in ``raw``, or ``None`` when it is not one.

    Surrounding whitespace is ignored; see ``specs/INC-002-01.md`` section 4.
    """
    value = raw.strip()
    if not _INT.fullmatch(value):
        return None
    number = int(value)
    return number if _INT_MIN <= number <= _INT_MAX else None


def parse_iso_date(raw: str) -> date | None:
    """Return the calendar date in ``raw`` when it is ``YYYY-MM-DD``, else ``None``.

    Surrounding whitespace is ignored; no other layout is converted.
    """
    value = raw.strip()
    if not _ISO_DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_local_timestamp(raw: object) -> datetime | None:
    """Return a naive UTC-05:00 local ``datetime`` for an ISO 8601 timestamp, else ``None``.

    A naive value is already local; ``Z`` or an offset is converted. See
    ``specs/INC-003.md`` section 3 for the accepted layouts.
    """
    raise NotImplementedError
