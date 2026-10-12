"""Sleep silver layer, rules and daily metrics (pure; the pipeline owns all I/O)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from wearable_pipeline.normalize import parse_iso_date, parse_local_timestamp

# rule_id -> (source, handling, severity, reason)
SLEEP_RULES: dict[str, tuple[str, str, str, str]] = {
    "SLEEP_ORPHAN_PARTICIPANT": (
        "sleep", "exclude_from_output", "medium",
        "sleep participant_id is not enrolled in participants.csv"),
    "SLEEP_DUPLICATE_PARTICIPANT_DAY": (
        "sleep", "withhold_field", "high",
        "participant_id and date occur in more than one sleep session"),
    "SLEEP_DATE_UNPARSEABLE": (
        "sleep", "exclude_from_output", "medium",
        "sleep date is not a valid YYYY-MM-DD calendar date"),
    "SLEEP_DATE_OUT_OF_WINDOW": (
        "sleep", "exclude_from_output", "medium",
        "sleep date is outside the study window"),
    "SLEEP_SESSION_FIELD_INVALID": (
        "sleep", "withhold_field", "medium",
        "sleep_onset, sleep_end, efficiency_pct or restlessness is missing or invalid"),
    "SLEEP_STAGE_INVALID": (
        "sleep", "withhold_field", "medium",
        "a sleep stage has an unknown name, an invalid duration or an unparseable time"),
    "SLEEP_STAGE_DURATION_MISMATCH": (
        "sleep", "flag", "medium",
        "a sleep stage duration differs from its timestamp interval"),
    "SLEEP_STAGE_GAP_OR_OVERLAP": (
        "sleep", "flag", "medium",
        "consecutive sleep stages have a gap or overlap"),
    "SLEEP_SPAN_OUTLIER": (
        "sleep", "flag", "medium",
        "sleep session span is longer than 12 hours"),
    "SLEEP_FINAL_STAGE_MISALIGNED": (
        "sleep", "flag", "medium",
        "final sleep stage does not start at sleep_end"),
    "SLEEP_EFFICIENCY_MISMATCH": (
        "sleep", "flag", "medium",
        "supplied efficiency_pct differs from the derived efficiency by more than 1 point"),
    "SLEEP_ONSET_NOT_MINUTE_ALIGNED": (
        "sleep", "flag", "medium",
        "sleep_onset is not aligned to a whole minute"),
}


@dataclass(frozen=True)
class SleepMetrics:
    """Session or day level sleep metrics; ``None`` marks a withheld value."""

    total_sleep_min: int | None
    deep_sleep_pct: float | None
    sleep_efficiency_pct: float | None


@dataclass(frozen=True)
class SleepDay:
    """Gold sleep columns for one participant-day with at least one contributing session."""

    total_sleep_min: int | None
    deep_sleep_pct: float | None
    sleep_efficiency_pct: float | None
    sleep_session_count: int
    sleep_quality_flags: str | None


@dataclass(frozen=True)
class SleepLayer:
    """Rows of the sleep silver tables, rejects, rule counts and per-day gold values."""

    sessions: list[tuple[Any, ...]]
    stages: list[tuple[Any, ...]]
    rejects: list[tuple[str, str, int, Any]]
    failed: Counter[str]
    checked: int
    daily: dict[tuple[str, date], SleepDay]


_KNOWN = frozenset({"light", "deep", "rem", "awake"})
_NONE = SleepMetrics(None, None, None)
_INT_MAX = 2_147_483_647  # INTEGER column bound, same 32-bit convention as parse_integer


def compute_sleep_metrics(stages: Sequence[tuple[str, int | None]]) -> SleepMetrics:
    """Derive total sleep minutes, deep-sleep share and efficiency from stage durations.

    Each pair is ``(stage name, duration or None when invalid)``; see ``specs/INC-003.md``
    section 5.
    """
    if not stages:
        return _NONE
    total = asleep = deep = 0
    any_invalid = non_awake_invalid = False
    for name, duration in stages:
        if duration is None or name not in _KNOWN:
            any_invalid = True
            if name != "awake":
                non_awake_invalid = True
            continue
        total += duration
        if name != "awake":
            asleep += duration
        if name == "deep":
            deep += duration
    if asleep > _INT_MAX:  # total does not fit the INTEGER column: withhold, never raise
        return _NONE
    total_sleep = None if non_awake_invalid else asleep
    if any_invalid or asleep == 0:
        return SleepMetrics(total_sleep, None, None)
    return SleepMetrics(total_sleep, deep / asleep * 100, asleep / total * 100)


def _number(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if low <= value <= high else None


def _flags(rule_ids: Collection[str]) -> str | None:
    return ",".join(sorted(rule_ids)) if rule_ids else None


def _stage(raw: Any) -> tuple[tuple[Any, ...], set[str], tuple[str, int | None],
                                              datetime | None, datetime | None]:
    """Return (row without keys, rules, metric pair, start, end) for one stage element."""
    if not isinstance(raw, dict):
        return (None,) * 5, {"SLEEP_STAGE_INVALID"}, ("", None), None, None
    name = raw.get("stage")
    name = name if isinstance(name, str) else None
    duration = raw.get("duration_min")
    if (isinstance(duration, bool) or not isinstance(duration, int)
            or not 1 <= duration <= _INT_MAX):
        duration = None
    start = parse_local_timestamp(raw.get("start_time"))
    end = parse_local_timestamp(raw.get("end_time"))
    rules: set[str] = set()
    if name not in _KNOWN or duration is None or start is None or end is None:
        rules.add("SLEEP_STAGE_INVALID")
    derived = (end - start).total_seconds() / 60 if start and end else None
    if duration is not None and start and end and end - start != timedelta(minutes=duration):
        rules.add("SLEEP_STAGE_DURATION_MISMATCH")
    pair = (name if name is not None else "",
            duration if start is not None and end is not None else None)
    return (name, start, end, duration, derived), rules, pair, start, end


def build_sleep_layer(
    files: Mapping[str, Sequence[Any]],
    enrolled: Collection[str],
    window: Sequence[date],
) -> SleepLayer:
    """Build sleep silver rows, quality outcomes and daily metrics from parsed sleep files.

    ``files`` maps relative path to the parsed JSON array; see ``specs/INC-003.md``.
    """
    enrolled_set, window_set = set(enrolled), set(window)
    records: list[dict[str, Any]] = []
    for path in sorted(files):
        folder = path.split("/")[-2] if path.count("/") else ""
        for position, raw in enumerate(files[path], start=1):
            obj = raw if isinstance(raw, dict) else {}
            pid = obj.get("participant_id")
            pid = pid if isinstance(pid, str) and pid.strip() else folder
            raw_date = obj.get("date")
            records.append({"path": path, "pos": position, "raw": raw, "obj": obj, "pid": pid,
                            "date": parse_iso_date(raw_date) if isinstance(raw_date, str)
                            else None})
    counts = Counter((r["pid"], r["date"]) for r in records if r["date"] is not None)

    sessions: list[tuple[Any, ...]] = []
    stage_rows: list[tuple[Any, ...]] = []
    rejects: list[tuple[str, str, int, Any]] = []
    failed: Counter[str] = Counter()
    contributing: dict[tuple[str, date], list[tuple[SleepMetrics, str | None]]] = {}

    for r in records:
        obj, pid, local = r["obj"], r["pid"], r["date"]
        onset = parse_local_timestamp(obj.get("sleep_onset"))
        end = parse_local_timestamp(obj.get("sleep_end"))
        efficiency = _number(obj.get("efficiency_pct"), 0, 100)
        restless = _number(obj.get("restlessness"), 0, 1)
        raw_stages = obj.get("stages")
        rules: set[str] = set()
        orphan = pid not in enrolled_set
        duplicate = local is not None and counts[(pid, local)] > 1
        out_of_window = local is not None and local not in window_set
        if orphan:
            rules.add("SLEEP_ORPHAN_PARTICIPANT")
        if duplicate:
            rules.add("SLEEP_DUPLICATE_PARTICIPANT_DAY")
        if local is None:
            rules.add("SLEEP_DATE_UNPARSEABLE")
        if out_of_window:
            rules.add("SLEEP_DATE_OUT_OF_WINDOW")
        if onset is None or end is None or efficiency is None or restless is None:
            rules.add("SLEEP_SESSION_FIELD_INVALID")

        pairs: list[tuple[str, int | None]] = []
        parsed: list[tuple[datetime | None, datetime | None] | None] = []
        built: list[tuple[tuple[Any, ...], set[str]]] = []
        if isinstance(raw_stages, list):
            for index, element in enumerate(raw_stages):
                row, st_rules, pair, start, st_end = _stage(element)
                pairs.append(pair)
                parsed.append((start, st_end) if isinstance(element, dict) else None)
                built.append((row, st_rules))
            for index in range(1, len(parsed)):
                before, after = parsed[index - 1], parsed[index]
                if (before and after and before[1] is not None and after[0] is not None
                        and after[0] != before[1]):
                    built[index][1].add("SLEEP_STAGE_GAP_OR_OVERLAP")
        else:
            rules.add("SLEEP_STAGE_INVALID")
        for _, st_rules in built:
            rules |= st_rules
        metrics = compute_sleep_metrics(pairs)

        if onset is not None and end is not None and end - onset > timedelta(hours=12):
            rules.add("SLEEP_SPAN_OUTLIER")
        if parsed and parsed[-1] and parsed[-1][0] is not None and end is not None \
                and parsed[-1][0] != end:
            rules.add("SLEEP_FINAL_STAGE_MISALIGNED")
        if (efficiency is not None and metrics.sleep_efficiency_pct is not None
                and abs(efficiency - metrics.sleep_efficiency_pct) > 1):
            rules.add("SLEEP_EFFICIENCY_MISMATCH")
        if onset is not None and (onset.second or onset.microsecond):
            rules.add("SLEEP_ONSET_NOT_MINUTE_ALIGNED")

        flag_text = _flags(rules)
        sessions.append((pid, local, onset, end, efficiency, restless, duplicate, orphan,
                         out_of_window, flag_text))
        for index, (row, st_rules) in enumerate(built, start=1):
            stage_rows.append((pid, local, index, *row, _flags(st_rules)))
        for rule in sorted(rules):
            failed[rule] += 1
            rejects.append((rule, r["path"], r["pos"], r["raw"]))
        if not orphan and local is not None and not out_of_window:
            contributing.setdefault((pid, local), []).append((metrics, flag_text))

    daily: dict[tuple[str, date], SleepDay] = {}
    for key, items in contributing.items():
        distinct = {m for m, _ in items}
        metric = distinct.pop() if len(distinct) == 1 else _NONE
        rule_ids = {f for _, text in items if text for f in text.split(",")}
        daily[key] = SleepDay(metric.total_sleep_min, metric.deep_sleep_pct,
                              metric.sleep_efficiency_pct, len(items), _flags(rule_ids))
    return SleepLayer(sessions, stage_rows, rejects, failed, len(records), daily)
