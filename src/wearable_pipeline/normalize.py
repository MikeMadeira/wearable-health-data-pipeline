"""Pure value normalizers shared by the silver builders."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_INT = re.compile(r"[+-]?[0-9]+")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIMESTAMP = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,6}))?"
    r"(?:(Z)|([+-])([0-9]{2}):([0-9]{2}))?"
)
_LOCAL = timezone(timedelta(hours=-5))
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
    if not isinstance(raw, str):
        return None
    match = _TIMESTAMP.fullmatch(raw.strip())
    if match is None:
        return None
    day, clock, fraction, zulu, sign, off_h, off_m = match.groups()
    micro = int((fraction or "").ljust(6, "0"))
    try:
        naive = datetime.fromisoformat(f"{day}T{clock}").replace(microsecond=micro)
        if zulu:
            offset = timedelta(0)
        elif sign:
            if int(off_h) > 23 or int(off_m) > 59:
                return None
            offset = timedelta(hours=int(off_h), minutes=int(off_m))
            if sign == "-":
                offset = -offset
        else:
            return naive
        aware = naive.replace(tzinfo=timezone(offset))
        return aware.astimezone(_LOCAL).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None
