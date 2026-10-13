"""Steps silver layer, rules and daily totals (pure; the pipeline owns all I/O)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from wearable_pipeline.normalize import parse_local_timestamp

# rule_id -> (source, handling, severity, reason)
STEPS_RULES: dict[str, tuple[str, str, str, str]] = {
    "STEPS_ORPHAN_PARTICIPANT": (
        "steps", "exclude_from_output", "medium",
        "steps participant_id is not enrolled in participants.csv"),
    "STEPS_PARTICIPANT_FOLDER_MISMATCH": (
        "steps", "exclude_from_output", "medium",
        "steps participant_id differs from its participant folder"),
    "STEPS_TIMESTAMP_UNPARSEABLE": (
        "steps", "exclude_from_output", "medium",
        "steps timestamp is missing or not a valid ISO 8601 timestamp"),
    "STEPS_TIMESTAMP_OUT_OF_WINDOW": (
        "steps", "exclude_from_output", "medium",
        "steps timestamp is outside the study window"),
    "STEPS_VALUE_INVALID": (
        "steps", "withhold_field", "medium",
        "steps is missing, not an integer, or outside the 32-bit integer range"),
    "STEPS_NEGATIVE": (
        "steps", "withhold_field", "high",
        "steps is negative"),
    "STEPS_UNIT_CONVERTED": (
        "steps", "convert", "low",
        "steps_per_hour value converted to steps per minute"),
    "STEPS_UNIT_UNSUPPORTED": (
        "steps", "withhold_field", "high",
        "steps _unit is not steps_per_hour or the hourly value is not divisible by 60"),
    "STEPS_DUPLICATE_MINUTE_CONFLICT": (
        "steps", "withhold_field", "high",
        "records for one participant-minute have different values"),
    "STEPS_DUPLICATE_MINUTE_EXACT": (
        "steps", "flag", "medium",
        "records for one participant-minute have identical values"),
    "STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED": (
        "steps", "flag", "medium",
        "steps timestamp is not aligned to a whole minute"),
}


@dataclass(frozen=True)
class StepsDay:
    """Gold step columns for one participant-day with at least one in-scope record."""

    total_steps: int | None
    steps_valid_minute_count: int
    minutes_rest_to_moderate: int
    minutes_burst: int
    steps_quality_flags: str | None


@dataclass(frozen=True)
class StepsLayer:
    """Rows of the steps silver table, rejects, rule counts and per-day gold values."""

    rows: list[tuple[Any, ...]]
    rejects: list[tuple[str, str, int, Any]]
    failed: Counter[str]
    checked: int
    daily: dict[tuple[str, date], StepsDay]

_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1
_REST_MAX = 30
_ABSENT = "\0absent"


def _flags(rule_ids: Collection[str]) -> str | None:
    return ",".join(sorted(rule_ids)) if rule_ids else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record(path: str, position: int, raw: Any, folder: str, enrolled: set[str],
            window: set[date]) -> dict[str, Any]:
    """Type one element and decide its record-level rules (duplicates are added later)."""
    obj = raw if isinstance(raw, dict) else {}
    given = obj.get("participant_id")
    named = isinstance(given, str) and bool(given.strip())
    pid = given if named else folder
    ts = parse_local_timestamp(obj.get("timestamp"))
    rules: set[str] = set()
    orphan = pid not in enrolled
    mismatch = named and given != folder
    out_of_window = ts is not None and ts.date() not in window
    if orphan:
        rules.add("STEPS_ORPHAN_PARTICIPANT")
    if mismatch:
        rules.add("STEPS_PARTICIPANT_FOLDER_MISMATCH")
    if ts is None:
        rules.add("STEPS_TIMESTAMP_UNPARSEABLE")
    if out_of_window:
        rules.add("STEPS_TIMESTAMP_OUT_OF_WINDOW")
    if ts is not None and (ts.second or ts.microsecond):
        rules.add("STEPS_TIMESTAMP_NOT_MINUTE_ALIGNED")

    value = obj.get("steps", _ABSENT)
    has_unit = "_unit" in obj
    unit_raw = obj.get("_unit")
    unit_text = _json(unit_raw) if has_unit else _ABSENT
    unit = (unit_raw if isinstance(unit_raw, str) else _json(unit_raw)) if has_unit else None
    valid = (isinstance(value, int) and not isinstance(value, bool)
             and _INT_MIN <= value <= _INT_MAX)
    norm: int | None = None
    if not valid:
        rules.add("STEPS_VALUE_INVALID")
    elif value < 0:
        rules.add("STEPS_NEGATIVE")
    elif not has_unit:
        norm = value
    elif unit_raw == "steps_per_hour" and isinstance(unit_raw, str) and value % 60 == 0:
        norm = value // 60
        rules.add("STEPS_UNIT_CONVERTED")
    else:
        rules.add("STEPS_UNIT_UNSUPPORTED")
    if norm is not None:
        signature: tuple[Any, ...] = ("value", norm, unit_text)
    else:
        signature = ("raw", _ABSENT if value is _ABSENT or "steps" not in obj else _json(value),
                     unit_text)
    return {
        "path": path, "pos": position, "raw": raw, "pid": pid, "ts": ts,
        "date": ts.date() if ts else None,
        "slot": ts.replace(second=0, microsecond=0) if ts else None,
        "steps": value if valid else None, "norm": norm, "unit": unit, "orphan": orphan,
        "mismatch": mismatch, "oow": out_of_window, "dup": False, "rules": rules,
        "signature": signature,
        "in_scope": not orphan and not mismatch and ts is not None and not out_of_window,
    }


def build_steps_layer(
    files: Mapping[str, Sequence[Any]],
    enrolled: Collection[str],
    window: Sequence[date],
) -> StepsLayer:
    """Build steps silver rows, quality outcomes and daily totals from parsed steps files.

    ``files`` maps relative path to the parsed JSON array; see ``specs/INC-004.md``.
    """
    enrolled_set, window_set = set(enrolled), set(window)
    records: list[dict[str, Any]] = []
    for path in sorted(files):
        folder = path.split("/")[-2] if path.count("/") else ""
        for position, raw in enumerate(files[path], start=1):
            records.append(_record(path, position, raw, folder, enrolled_set, window_set))

    groups: dict[tuple[str, datetime], list[dict[str, Any]]] = {}
    for rec in records:
        if rec["ts"] is not None and not rec["mismatch"]:
            groups.setdefault((rec["pid"], rec["slot"]), []).append(rec)
    for members in groups.values():
        if len(members) < 2:
            continue
        conflict = len({m["signature"] for m in members}) > 1
        for m in members:
            m["dup"] = True
            m["rules"].add("STEPS_DUPLICATE_MINUTE_CONFLICT" if conflict
                           else "STEPS_DUPLICATE_MINUTE_EXACT")
            if conflict:
                m["norm"] = None

    rows: list[tuple[Any, ...]] = []
    rejects: list[tuple[str, str, int, Any]] = []
    failed: Counter[str] = Counter()
    for rec in records:
        norm = rec["norm"]
        level = None if norm is None else (
            "at_rest_to_moderate" if norm <= _REST_MAX else "burst")
        rec["level"] = level
        rows.append((rec["pid"], rec["date"], rec["ts"], rec["steps"], norm, level, rec["unit"],
                     rec["orphan"], rec["oow"], rec["dup"], _flags(rec["rules"])))
        for rule in sorted(rec["rules"]):
            failed[rule] += 1
            if rule != "STEPS_UNIT_CONVERTED":
                rejects.append((rule, rec["path"], rec["pos"], rec["raw"]))

    scoped: dict[tuple[str, date], dict[datetime, list[dict[str, Any]]]] = {}
    for rec in records:
        if rec["in_scope"]:
            scoped.setdefault((rec["pid"], rec["date"]), {}).setdefault(
                rec["slot"], []).append(rec)
    daily: dict[tuple[str, date], StepsDay] = {}
    for key, slots in scoped.items():
        values: list[int] = []
        levels: list[str] = []
        rule_ids: set[str] = set()
        for members in slots.values():
            rule_ids.update(*(m["rules"] for m in members))
            if members[0]["norm"] is not None:
                values.append(members[0]["norm"])
                levels.append(members[0]["level"])
        daily[key] = StepsDay(
            sum(values) if values else None, len(values),
            levels.count("at_rest_to_moderate"), levels.count("burst"), _flags(rule_ids))
    return StepsLayer(rows, rejects, failed, len(records), daily)
