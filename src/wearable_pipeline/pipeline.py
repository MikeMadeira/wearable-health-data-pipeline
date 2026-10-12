"""Walking-skeleton pipeline: manifest, silver survey/participants, quality, gold daily."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from wearable_pipeline.normalize import parse_integer, parse_iso_date
from wearable_pipeline.sleep import SLEEP_RULES, SleepLayer, build_sleep_layer

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


PARTICIPANT_COLUMNS = (
    "participant_id", "device_id", "age", "height_cm", "gender", "chronotype", "max_heart_rate",
)
SURVEY_COLUMNS = (
    "participant_id", "date", "fatigue_score", "stress_score", "readiness_score",
    "sleep_quality_score",
)
PARTICIPANTS_PATH = "participants/participants.csv"
SURVEY_PATH = "health_summaries/wellness_survey.csv"
SCORE_RANGES = {
    "fatigue_score": (1, 5),
    "stress_score": (1, 5),
    "readiness_score": (0, 10),
    "sleep_quality_score": (1, 5),
}
SCORES = tuple(SCORE_RANGES)

# rule_id -> (source, handling, severity, reason)
RULES = {
    "PARTICIPANTS_KEY_DUPLICATE": (
        "participants", "flag", "high",
        "participant_id occurs in more than one participants row"),
    "SURVEY_ORPHAN_PARTICIPANT": (
        "wellness_survey", "exclude_from_output", "medium",
        "participant_id is not enrolled in participants.csv"),
    "SURVEY_DUPLICATE_PARTICIPANT_DAY": (
        "wellness_survey", "withhold_field", "high",
        "participant_id and date occur in more than one survey row"),
    "SURVEY_SCORE_INVALID": (
        "wellness_survey", "withhold_field", "medium",
        "a score is blank, not an integer, or outside its allowed range"),
    "PARTICIPANTS_INTEGER_INVALID": (
        "participants", "withhold_field", "medium",
        "age, height_cm or max_heart_rate is not an integer"),
    "SURVEY_DATE_UNPARSEABLE": (
        "wellness_survey", "exclude_from_output", "medium",
        "date is not a valid YYYY-MM-DD calendar date"),
    "SURVEY_DATE_OUT_OF_WINDOW": (
        "wellness_survey", "exclude_from_output", "medium",
        "date is outside the study window"),
}
RULES = {**RULES, **SLEEP_RULES}
INTEGER_FIELDS = ("age", "height_cm", "max_heart_rate")

_FIXED_SOURCES = {
    "participants/participants.csv": "participants",
    "health_summaries/device_metadata.csv": "device_metadata",
    "health_summaries/wellness_survey.csv": "wellness_survey",
    "data_dictionary.md": "documentation",
}
_EVENT_SOURCES = {"sleep.json": "sleep", "steps.json": "steps", "heart_rate.json": "heart_rate"}


def _classify(relative: str) -> str:
    if relative in _FIXED_SOURCES:
        return _FIXED_SOURCES[relative]
    parts = relative.split("/")
    if len(parts) == 3 and parts[0] == "wearable_events" and parts[2] in _EVENT_SOURCES:
        return _EVENT_SOURCES[parts[2]]
    return "unclassified"


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = [row for row in csv.reader(handle) if row]
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise PipelineError(f"cannot read {path}: {error}") from error
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _read_required(data_dir: Path, relative: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    path = data_dir / relative
    if not path.is_file():
        raise PipelineError(f"required input missing: {relative}")
    header, rows = _read_csv(path)
    missing = [column for column in columns if column not in header]
    if missing:
        raise PipelineError(f"{relative} is missing required columns: {', '.join(missing)}")
    index = {name: header.index(name) for name in header}
    return [
        {name: (row[i] if i < len(row) else "") for name, i in index.items()} for row in rows
    ]


def _build_manifest(data_dir: Path) -> list[tuple[Any, ...]]:
    manifest = []
    try:
        files = [p for p in data_dir.rglob("*") if p.is_file()]
        for path in files:
            relative = path.relative_to(data_dir).as_posix()
            content = path.read_bytes()
            count = len(_read_csv(path)[1]) if relative.endswith(".csv") else None
            manifest.append((relative, _classify(relative), len(content),
                             hashlib.sha256(content).hexdigest(), count))
    except OSError as error:
        raise PipelineError(f"cannot read raw files: {error}") from error
    return sorted(manifest)


def _run_id(manifest: list[tuple[Any, ...]]) -> str:
    text = "".join(f"{row[0]}\t{row[3]}\n" for row in manifest)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _parse_score(raw: str, low: int, high: int) -> int | None:
    number = parse_integer(raw)
    return number if number is not None and low <= number <= high else None


def _flags(rule_ids: set[str]) -> str | None:
    return ",".join(sorted(rule_ids)) if rule_ids else None


def _distinct_or_none(values: list[int | None]) -> int | None:
    distinct = {v for v in values if v is not None}
    return distinct.pop() if len(distinct) == 1 else None


def _transform(
    participants: list[dict[str, str]],
    survey: list[dict[str, str]],
    window: list[date],
) -> dict[str, Any]:
    window_set = set(window)
    id_counts = Counter(r["participant_id"] for r in participants)
    enrolled = list(dict.fromkeys(r["participant_id"] for r in participants))
    enrolled_set = set(enrolled)

    silver_p = []
    reject_rows: list[tuple[str, str, str, dict[str, str]]] = []  # rule, path, n, raw
    for n, row in enumerate(participants, start=1):
        duplicate = id_counts[row["participant_id"]] > 1
        flags = {"PARTICIPANTS_KEY_DUPLICATE"} if duplicate else set()
        if duplicate:
            reject_rows.append(("PARTICIPANTS_KEY_DUPLICATE", PARTICIPANTS_PATH, n, row))
        ints = {c: parse_integer(row[c]) for c in INTEGER_FIELDS}
        if any(v is None for v in ints.values()):
            flags.add("PARTICIPANTS_INTEGER_INVALID")
            reject_rows.append(("PARTICIPANTS_INTEGER_INVALID", PARTICIPANTS_PATH, n, row))
        silver_p.append((
            row["participant_id"], row["device_id"], ints["age"], ints["height_cm"],
            row["gender"], row["chronotype"], ints["max_heart_rate"], duplicate, _flags(flags),
        ))

    parsed = [parse_iso_date(r["date"]) for r in survey]
    day_counts = Counter(
        (r["participant_id"], d) for r, d in zip(survey, parsed) if d is not None
    )
    silver_s = []
    contributing: dict[tuple[str, date], list[tuple[list[int | None], str | None]]] = {}
    for n, (row, local) in enumerate(zip(survey, parsed), start=1):
        scores = [_parse_score(row[c], *SCORE_RANGES[c]) for c in SCORES]
        orphan = row["participant_id"] not in enrolled_set
        duplicate = local is not None and day_counts[(row["participant_id"], local)] > 1
        unparseable = local is None
        out_of_window = local is not None and local not in window_set
        flags = set()
        if orphan:
            flags.add("SURVEY_ORPHAN_PARTICIPANT")
        if duplicate:
            flags.add("SURVEY_DUPLICATE_PARTICIPANT_DAY")
        if any(s is None for s in scores):
            flags.add("SURVEY_SCORE_INVALID")
        if unparseable:
            flags.add("SURVEY_DATE_UNPARSEABLE")
        if out_of_window:
            flags.add("SURVEY_DATE_OUT_OF_WINDOW")
        for rule in flags:
            reject_rows.append((rule, SURVEY_PATH, n, row))
        flag_text = _flags(flags)
        silver_s.append((
            row["participant_id"], local, *scores,
            orphan, duplicate, out_of_window, flag_text,
        ))
        if not orphan and not unparseable and not out_of_window:
            contributing.setdefault((row["participant_id"], local), []).append((scores, flag_text))

    gold = []
    for pid in sorted(enrolled_set):
        for day in window:
            rows = contributing.get((pid, day), [])
            values = [_distinct_or_none([r[0][i] for r in rows]) for i in range(len(SCORES))]
            rule_ids = {f for _, text in rows if text for f in text.split(",")}
            gold.append((pid, day, *values, len(rows) > 0, len(rows),
                         sum(v is not None for v in values), _flags(rule_ids)))

    failed: Counter[str] = Counter()
    for rule, *_ in reject_rows:
        failed[rule] += 1
    return {
        "silver_p": silver_p, "silver_s": silver_s, "gold": gold,
        "rejects": reject_rows, "failed": failed,
        "checked": {"participants": len(participants), "wellness_survey": len(survey)},
    }


def _read_sleep_files(data_dir: Path, manifest: list[tuple[Any, ...]]) -> dict[str, list[Any]]:
    files: dict[str, list[Any]] = {}
    for relative, source, *_ in manifest:
        if source != "sleep":
            continue
        try:
            content = json.loads((data_dir / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as error:
            raise PipelineError(f"cannot read sleep file {relative}: {error}") from error
        if not isinstance(content, list):
            raise PipelineError(f"{relative} must contain a JSON array")
        if not all(isinstance(item, dict) for item in content):
            raise PipelineError(f"{relative} must contain only JSON objects")
        files[relative] = content
    return files


SLEEP_GOLD_COLUMNS = (
    ("total_sleep_min", "INTEGER"), ("deep_sleep_pct", "DOUBLE"),
    ("sleep_efficiency_pct", "DOUBLE"), ("sleep_session_count", "INTEGER"),
    ("sleep_present", "BOOLEAN"), ("sleep_quality_flags", "VARCHAR"),
)


def _sleep_gold(sleep: SleepLayer, pid: str, day: date) -> tuple[Any, ...]:
    entry = sleep.daily.get((pid, day))
    if entry is None:
        return (None, None, None, 0, False, None)
    return (entry.total_sleep_min, entry.deep_sleep_pct, entry.sleep_efficiency_pct,
            entry.sleep_session_count, entry.sleep_session_count > 0, entry.sleep_quality_flags)


def _write_report(path: Path, run_id: str, manifest_count: int,
                  results: list[tuple[Any, ...]]) -> None:
    lines = [
        "# Quality report", "", f"Run id: {run_id}", f"Manifest files: {manifest_count}", "",
        "| rule_id | source | handling | severity | checked | failed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[7]} | {r[8]} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def _replace(con: duckdb.DuckDBPyConnection, name: str, ddl: str,
             rows: list[tuple[Any, ...]]) -> None:
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"CREATE TABLE {name} ({ddl})")
    if rows:
        placeholders = ", ".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)


def _persist(db_path: Path, run_id: str, manifest: list[tuple[Any, ...]],
             t: dict[str, Any], results: list[tuple[Any, ...]]) -> None:
    rejects = [
        (run_id, rule, RULES[rule][0], f"{path}:{n}", RULES[rule][3],
         json.dumps(raw, ensure_ascii=False))
        for rule, path, n, raw in t["rejects"]
    ]
    rejects.sort()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("BEGIN TRANSACTION")
        _replace(con, "raw_manifest",
                 "relative_path VARCHAR, source VARCHAR, size_bytes BIGINT, sha256 VARCHAR, "
                 "record_count BIGINT", manifest)
        _replace(con, "silver_participants",
                 "participant_id VARCHAR, device_id VARCHAR, "
                 "age INTEGER, height_cm INTEGER, gender VARCHAR, chronotype VARCHAR, "
                 "max_heart_rate INTEGER, is_duplicate_key BOOLEAN, quality_flags VARCHAR",
                 t["silver_p"])
        _replace(con, "silver_wellness_survey",
                 "participant_id VARCHAR, "
                 "local_date DATE, fatigue_score INTEGER, stress_score INTEGER, "
                 "readiness_score INTEGER, sleep_quality_score INTEGER, is_orphan BOOLEAN, "
                 "is_duplicate_key BOOLEAN, is_out_of_window BOOLEAN, quality_flags VARCHAR",
                 t["silver_s"])
        _replace(con, "silver_sleep_sessions",
                 "participant_id VARCHAR, local_date DATE, sleep_onset TIMESTAMP, "
                 "sleep_end TIMESTAMP, supplied_efficiency_pct DOUBLE, restlessness DOUBLE, "
                 "is_duplicate_key BOOLEAN, is_orphan BOOLEAN, is_out_of_window BOOLEAN, "
                 "quality_flags VARCHAR", t["sleep"].sessions)
        _replace(con, "silver_sleep_stages",
                 "participant_id VARCHAR, local_date DATE, stage_index INTEGER, "
                 "stage VARCHAR, start_time TIMESTAMP, end_time TIMESTAMP, "
                 "duration_min INTEGER, derived_duration_min DOUBLE, quality_flags VARCHAR",
                 t["sleep"].stages)
        _replace(con, "quality_results",
                 "run_id VARCHAR, rule_id VARCHAR, source VARCHAR, handling VARCHAR, "
                 "severity VARCHAR, reason VARCHAR, checked_count BIGINT, failed_count BIGINT",
                 [(r[0], r[1], r[2], r[3], r[4], r[5], r[7], r[8]) for r in results])
        _replace(con, "quality_rejects",
                 "run_id VARCHAR, rule_id VARCHAR, source VARCHAR, row_ref VARCHAR, "
                 "reason VARCHAR, raw_values VARCHAR", rejects)
        con.execute(
            "CREATE TABLE IF NOT EXISTS gold_clinical_daily ("
            "participant_id VARCHAR, local_date DATE, fatigue_score INTEGER, "
            "stress_score INTEGER, readiness_score INTEGER, sleep_quality_score INTEGER, "
            "survey_present BOOLEAN, survey_row_count INTEGER, "
            "survey_valid_score_count INTEGER, survey_quality_flags VARCHAR, "
            + ", ".join(f"{name} {kind}" for name, kind in SLEEP_GOLD_COLUMNS)
            + ", PRIMARY KEY (participant_id, local_date))")
        for name, kind in SLEEP_GOLD_COLUMNS:
            con.execute(
                f"ALTER TABLE gold_clinical_daily ADD COLUMN IF NOT EXISTS {name} {kind}")
        con.execute(
            "CREATE OR REPLACE TEMP TABLE gold_stage AS "
            "SELECT * FROM gold_clinical_daily LIMIT 0")
        if t["gold"]:
            marks = ",".join("?" * len(t["gold"][0]))
            con.executemany(f"INSERT INTO gold_stage VALUES ({marks})", t["gold"])
        con.execute(
            "MERGE INTO gold_clinical_daily AS g USING gold_stage AS s "
            "ON g.participant_id = s.participant_id AND g.local_date = s.local_date "
            "WHEN MATCHED THEN UPDATE SET "
            "fatigue_score = s.fatigue_score, stress_score = s.stress_score, "
            "readiness_score = s.readiness_score, "
            "sleep_quality_score = s.sleep_quality_score, survey_present = s.survey_present, "
            "survey_row_count = s.survey_row_count, "
            "survey_valid_score_count = s.survey_valid_score_count, "
            "survey_quality_flags = s.survey_quality_flags, "
            "total_sleep_min = s.total_sleep_min, deep_sleep_pct = s.deep_sleep_pct, "
            "sleep_efficiency_pct = s.sleep_efficiency_pct, "
            "sleep_session_count = s.sleep_session_count, sleep_present = s.sleep_present, "
            "sleep_quality_flags = s.sleep_quality_flags "
            "WHEN NOT MATCHED THEN INSERT BY NAME")
        con.execute("DROP TABLE gold_stage")
        con.execute("COMMIT")
    except duckdb.Error as error:
        try:
            con.execute("ROLLBACK")
        except duckdb.Error:
            pass
        raise PipelineError(f"database write failed: {error}") from error
    finally:
        con.close()


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
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    if study_days < 1:
        raise PipelineError("study_days must be at least 1")
    if not data_dir.is_dir():
        raise PipelineError(f"data directory not found: {data_dir}")
    participants = _read_required(data_dir, PARTICIPANTS_PATH, PARTICIPANT_COLUMNS)
    survey = _read_required(data_dir, SURVEY_PATH, SURVEY_COLUMNS)
    manifest = _build_manifest(data_dir)
    sleep_files = _read_sleep_files(data_dir, manifest)
    run_id = _run_id(manifest)
    window = [study_start + timedelta(days=i) for i in range(study_days)]

    t = _transform(participants, survey, window)
    enrolled = {r["participant_id"] for r in participants}
    sleep: SleepLayer = build_sleep_layer(sleep_files, enrolled, window)
    t["sleep"] = sleep
    t["rejects"] = t["rejects"] + sleep.rejects
    t["failed"] = t["failed"] + sleep.failed
    t["checked"]["sleep"] = sleep.checked
    t["gold"] = [
        (*row, *_sleep_gold(sleep, row[0], row[1])) for row in t["gold"]
    ]
    results = []
    for rule_id in sorted(RULES):
        source, handling, severity, reason = RULES[rule_id]
        results.append((run_id, rule_id, source, handling, severity, reason,
                        None, t["checked"][source], t["failed"][rule_id]))
    results.sort(key=lambda r: (r[1], r[2]))

    database_path = output_dir / "wearable.duckdb"
    report_path = output_dir / "quality" / "report.md"
    _persist(database_path, run_id, manifest, t, results)
    _write_report(report_path, run_id, len(manifest), results)
    return RunResult(run_id=run_id, database_path=database_path, report_path=report_path)
