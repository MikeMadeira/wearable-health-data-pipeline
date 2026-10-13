"""Heart-rate silver layer, rules and daily aggregates (pure; the pipeline owns all I/O)."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from wearable_pipeline.normalize import parse_local_timestamp

# rule_id -> (source, handling, severity, reason)
HEART_RATE_RULES: dict[str, tuple[str, str, str, str]] = {
    "HR_ORPHAN_PARTICIPANT": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate participant_id is not enrolled in participants.csv"),
    "HR_PARTICIPANT_FOLDER_MISMATCH": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate participant_id differs from its participant folder"),
    "HR_TIMESTAMP_UNPARSEABLE": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate timestamp is missing or not a valid ISO 8601 timestamp"),
    "HR_TIMESTAMP_OUT_OF_WINDOW": (
        "heart_rate", "exclude_from_output", "medium",
        "heart rate timestamp is outside the study window"),
    "HR_VALUE_INVALID": (
        "heart_rate", "withhold_field", "medium",
        "heart_rate_bpm is missing, not an integer, or outside the 32-bit integer range"),
    "HR_VALUE_OUT_OF_RANGE": (
        "heart_rate", "withhold_field", "high",
        "heart_rate_bpm is outside the range 40 to 200"),
    "HR_STUCK_SENSOR_RUN": (
        "heart_rate", "withhold_field", "medium",
        "heart_rate_bpm repeats for at least 60 consecutive minutes"),
    "HR_TIMESTAMP_CONVERTED": (
        "heart_rate", "convert", "low",
        "heart rate timestamp with Z or an offset converted to UTC-05:00"),
    "HR_DUPLICATE_MINUTE_CONFLICT": (
        "heart_rate", "withhold_field", "high",
        "records for one participant-minute have different values"),
    "HR_DUPLICATE_MINUTE_EXACT": (
        "heart_rate", "flag", "medium",
        "records for one participant-minute have identical values"),
    "HR_TIMESTAMP_NOT_MINUTE_ALIGNED": (
        "heart_rate", "flag", "medium",
        "heart rate timestamp is not aligned to a whole minute"),
}


@dataclass(frozen=True)
class HeartRateDay:
    """Gold heart-rate columns for one participant-day with at least one in-scope record."""

    hr_mean_bpm: float | None
    hr_min_bpm: int | None
    hr_max_bpm: int | None
    hr_valid_minute_count: int
    hr_quality_flags: str | None


@dataclass(frozen=True)
class HeartRateLayer:
    """Rows of the heart-rate silver table, rejects, rule counts and per-day gold values."""

    rows: list[tuple[Any, ...]]
    rejects: list[tuple[str, str, int, Any]]
    failed: Counter[str]
    checked: int
    daily: dict[tuple[str, date], HeartRateDay]


_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1
_LOW, _HIGH = 40, 200
_STUCK_MINUTES = 60
_ABSENT = "\0absent"
_OFFSET = re.compile(r"[+-][0-9]{2}:[0-9]{2}$")
_MINUTE = timedelta(minutes=1)


def _flags(rule_ids: Collection[str]) -> str | None:
    return ",".join(sorted(rule_ids)) if rule_ids else None


def _record(path: str, position: int, raw: Any, folder: str, enrolled: set[str],
            window: set[date]) -> dict[str, Any]:
    """Type one element and decide its record-level rules (groups and runs come later)."""
    obj = raw if isinstance(raw, dict) else {}
    given = obj.get("participant_id")
    named = isinstance(given, str) and bool(given.strip())
    pid = given if named else folder
    text = obj.get("timestamp")
    ts = parse_local_timestamp(text)
    rules: set[str] = set()
    orphan = pid not in enrolled
    mismatch = named and given != folder
    out_of_window = ts is not None and ts.date() not in window
    converted = ts is not None and (
        text.strip().endswith("Z") or _OFFSET.search(text.strip()) is not None)
    if orphan:
        rules.add("HR_ORPHAN_PARTICIPANT")
    if mismatch:
        rules.add("HR_PARTICIPANT_FOLDER_MISMATCH")
    if ts is None:
        rules.add("HR_TIMESTAMP_UNPARSEABLE")
    if out_of_window:
        rules.add("HR_TIMESTAMP_OUT_OF_WINDOW")
    if converted:
        rules.add("HR_TIMESTAMP_CONVERTED")
    if ts is not None and (ts.second or ts.microsecond):
        rules.add("HR_TIMESTAMP_NOT_MINUTE_ALIGNED")

    value = obj.get("heart_rate_bpm", _ABSENT)
    valid = (isinstance(value, int) and not isinstance(value, bool)
             and _INT_MIN <= value <= _INT_MAX)
    in_range = valid and _LOW <= value <= _HIGH
    if not valid:
        rules.add("HR_VALUE_INVALID")
    elif not in_range:
        rules.add("HR_VALUE_OUT_OF_RANGE")
    signature = _ABSENT if value is _ABSENT else json.dumps(value, ensure_ascii=False)
    return {
        "path": path, "pos": position, "raw": raw, "pid": pid, "ts": ts,
        "date": ts.date() if ts else None,
        "slot": ts.replace(second=0, microsecond=0) if ts else None,
        "raw_ts": text if converted else None, "converted": converted,
        "bpm": value if valid else None, "norm": value if in_range else None,
        "in_range": in_range, "orphan": orphan, "mismatch": mismatch, "oow": out_of_window,
        "dup": False, "stuck": False, "rules": rules, "signature": signature,
        "in_scope": not orphan and not mismatch and ts is not None and not out_of_window,
    }


def _mark_stuck(groups: dict[tuple[str, datetime], list[dict[str, Any]]]) -> None:
    """Flag every record of every slot in runs of >= 60 identical consecutive clean minutes."""
    clean: dict[str, list[tuple[datetime, int, list[dict[str, Any]]]]] = {}
    for (pid, slot), members in groups.items():
        first = members[0]
        if first["in_range"] and all(m["signature"] == first["signature"] for m in members):
            clean.setdefault(pid, []).append((slot, first["bpm"], members))
    for slots in clean.values():
        slots.sort(key=lambda item: item[0])
        run: list[tuple[datetime, int, list[dict[str, Any]]]] = []
        for item in [*slots, None]:
            if run and item is not None and item[1] == run[-1][1] \
                    and item[0] - run[-1][0] == _MINUTE:
                run.append(item)
                continue
            if len(run) >= _STUCK_MINUTES:
                for _, _, members in run:
                    for m in members:
                        m["stuck"] = True
                        m["norm"] = None
                        m["rules"].add("HR_STUCK_SENSOR_RUN")
            run = [item] if item is not None else []


def build_heart_rate_layer(
    files: Mapping[str, Sequence[Any]],
    enrolled: Collection[str],
    window: Sequence[date],
) -> HeartRateLayer:
    """Build heart-rate silver rows, quality outcomes and daily aggregates.

    ``files`` maps relative path to the parsed JSON array; see ``specs/INC-005.md``.
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
    _mark_stuck(groups)
    for members in groups.values():
        if len(members) < 2:
            continue
        conflict = len({m["signature"] for m in members}) > 1
        for m in members:
            m["dup"] = True
            m["rules"].add("HR_DUPLICATE_MINUTE_CONFLICT" if conflict
                           else "HR_DUPLICATE_MINUTE_EXACT")
            if conflict:
                m["norm"] = None

    rows: list[tuple[Any, ...]] = []
    rejects: list[tuple[str, str, int, Any]] = []
    failed: Counter[str] = Counter()
    for rec in records:
        rules = rec["rules"]
        rows.append((rec["pid"], rec["date"], rec["ts"], rec["raw_ts"], rec["bpm"], rec["norm"],
                     rec["orphan"], rec["oow"], rec["dup"], "HR_VALUE_OUT_OF_RANGE" in rules,
                     rec["stuck"], rec["converted"], _flags(rules)))
        for rule in sorted(rules):
            failed[rule] += 1
            if rule != "HR_TIMESTAMP_CONVERTED":
                rejects.append((rule, rec["path"], rec["pos"], rec["raw"]))

    scoped: dict[tuple[str, date], dict[datetime, list[dict[str, Any]]]] = {}
    for rec in records:
        if rec["in_scope"]:
            scoped.setdefault((rec["pid"], rec["date"]), {}).setdefault(
                rec["slot"], []).append(rec)
    daily: dict[tuple[str, date], HeartRateDay] = {}
    for key, slots in scoped.items():
        values: list[int] = []
        rule_ids: set[str] = set()
        for members in slots.values():
            rule_ids.update(*(m["rules"] for m in members))
            if members[0]["norm"] is not None:
                values.append(members[0]["norm"])
        daily[key] = HeartRateDay(
            sum(values) / len(values) if values else None,
            min(values) if values else None, max(values) if values else None,
            len(values), _flags(rule_ids))
    return HeartRateLayer(rows, rejects, failed, len(records), daily)


