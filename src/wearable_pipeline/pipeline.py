"""Walking-skeleton pipeline: manifest, silver survey/participants, quality, gold daily."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from wearable_pipeline.normalize import parse_integer, parse_iso_date
from wearable_pipeline.heart_rate import HEART_RATE_RULES, HeartRateLayer, build_heart_rate_layer
from wearable_pipeline.sleep import SLEEP_RULES, SleepLayer, build_sleep_layer
from wearable_pipeline.steps import STEPS_RULES, StepsLayer, build_steps_layer

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
RULES = {**RULES, **SLEEP_RULES, **STEPS_RULES, **HEART_RATE_RULES}
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


def _read_event_json(path: Path, relative: str, kind: str) -> list[Any]:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as error:
        raise PipelineError(f"cannot read {kind} file {relative}: {error}") from error
    if not isinstance(content, list):
        raise PipelineError(f"{relative} must contain a JSON array")
    if not all(isinstance(item, dict) for item in content):
        raise PipelineError(f"{relative} must contain only JSON objects")
    return content


def _read_event_files(data_dir: Path, manifest: list[tuple[Any, ...]],
                      kind: str) -> dict[str, list[Any]]:
    return {relative: _read_event_json(data_dir / relative, relative, kind)
            for relative, source, *_ in manifest if source == kind}


STEPS_GOLD_COLUMNS = (
    ("total_steps", "BIGINT"), ("steps_valid_minute_count", "INTEGER"),
    ("minutes_rest_to_moderate", "INTEGER"), ("minutes_burst", "INTEGER"),
    ("steps_present", "BOOLEAN"), ("steps_quality_flags", "VARCHAR"),
)
HR_GOLD_COLUMNS = (
    ("hr_mean_bpm", "DOUBLE"), ("hr_min_bpm", "INTEGER"), ("hr_max_bpm", "INTEGER"),
    ("hr_valid_minute_count", "INTEGER"), ("hr_present", "BOOLEAN"),
    ("hr_quality_flags", "VARCHAR"),
)
SILVER_HR_DDL = (
    "participant_id VARCHAR, local_date DATE, minute_ts TIMESTAMP, raw_timestamp VARCHAR, "
    "heart_rate_bpm INTEGER, heart_rate_bpm_normalized INTEGER, is_orphan BOOLEAN, "
    "is_out_of_window BOOLEAN, is_duplicate_minute BOOLEAN, is_out_of_range BOOLEAN, "
    "is_stuck_sensor BOOLEAN, is_timestamp_converted BOOLEAN, quality_flags VARCHAR")
GOLD_COLUMNS = (
    ("participant_id", "VARCHAR"), ("local_date", "DATE"), ("fatigue_score", "INTEGER"),
    ("stress_score", "INTEGER"), ("readiness_score", "INTEGER"),
    ("sleep_quality_score", "INTEGER"), ("survey_present", "BOOLEAN"),
    ("survey_row_count", "INTEGER"), ("survey_valid_score_count", "INTEGER"),
    ("survey_quality_flags", "VARCHAR"),
)
SILVER_STEPS_DDL = (
    "participant_id VARCHAR, local_date DATE, minute_ts TIMESTAMP, steps INTEGER, "
    "steps_per_minute INTEGER, activity_level VARCHAR, unit VARCHAR, is_orphan BOOLEAN, "
    "is_out_of_window BOOLEAN, is_duplicate_minute BOOLEAN, quality_flags VARCHAR")
REJECTS_DDL = ("run_id VARCHAR, rule_id VARCHAR, source VARCHAR, row_ref VARCHAR, "
               "reason VARCHAR, raw_values VARCHAR")


def _csv_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace('"', '""') + '"'
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _bulk_load(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple[Any, ...]]) -> None:
    """Load rows with a temporary CSV and COPY; NULL is unquoted-empty, strings are quoted."""
    if not rows:
        return
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{table}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(",".join(map(_csv_field, row)) + "\n" for row in rows))
        con.execute(
            f"COPY {table} FROM '{path.as_posix()}' "
            "(FORMAT CSV, HEADER false, NULLSTR '', ALLOW_QUOTED_NULLS false)")


def _steps_gold(steps: StepsLayer, pid: str, day: date) -> tuple[Any, ...]:
    entry = steps.daily.get((pid, day))
    if entry is None:
        return (None, 0, 0, 0, False, None)
    return (entry.total_steps, entry.steps_valid_minute_count, entry.minutes_rest_to_moderate,
            entry.minutes_burst, True, entry.steps_quality_flags)


def _hr_gold(hr: HeartRateLayer, pid: str, day: date) -> tuple[Any, ...]:
    entry = hr.daily.get((pid, day))
    if entry is None:
        return (None, None, None, 0, False, None)
    return (entry.hr_mean_bpm, entry.hr_min_bpm, entry.hr_max_bpm,
            entry.hr_valid_minute_count, True, entry.hr_quality_flags)


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


def _report_text(run_id: str, manifest_count: int, results: list[tuple[Any, ...]]) -> str:
    lines = [
        "# Quality report", "", f"Run id: {run_id}", f"Manifest files: {manifest_count}", "",
        "| rule_id | source | handling | severity | checked | failed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[7]} | {r[8]} |")
    return "\n".join(lines) + "\n"


def _write_report(path: Path, run_id: str, manifest_count: int,
                  results: list[tuple[Any, ...]]) -> None:
    text = _report_text(run_id, manifest_count, results)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _replace(con: duckdb.DuckDBPyConnection, name: str, ddl: str,
             rows: list[tuple[Any, ...]]) -> None:
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"CREATE TABLE {name} ({ddl})")
    if rows:
        placeholders = ", ".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)


ALL_GOLD_COLUMNS = GOLD_COLUMNS + SLEEP_GOLD_COLUMNS + STEPS_GOLD_COLUMNS + HR_GOLD_COLUMNS
SLEEP_SLICE = slice(len(GOLD_COLUMNS), len(GOLD_COLUMNS) + 6)
STEPS_SLICE = slice(SLEEP_SLICE.stop, SLEEP_SLICE.stop + 6)
HR_SLICE = slice(STEPS_SLICE.stop, STEPS_SLICE.stop + 6)

TABLE_DDL = {
    "raw_manifest": (
        "relative_path VARCHAR, source VARCHAR, size_bytes BIGINT, sha256 VARCHAR, "
        "record_count BIGINT"),
    "silver_participants": (
        "participant_id VARCHAR, device_id VARCHAR, "
        "age INTEGER, height_cm INTEGER, gender VARCHAR, chronotype VARCHAR, "
        "max_heart_rate INTEGER, is_duplicate_key BOOLEAN, quality_flags VARCHAR"),
    "silver_wellness_survey": (
        "participant_id VARCHAR, "
        "local_date DATE, fatigue_score INTEGER, stress_score INTEGER, "
        "readiness_score INTEGER, sleep_quality_score INTEGER, is_orphan BOOLEAN, "
        "is_duplicate_key BOOLEAN, is_out_of_window BOOLEAN, quality_flags VARCHAR"),
    "silver_sleep_sessions": (
        "participant_id VARCHAR, local_date DATE, sleep_onset TIMESTAMP, "
        "sleep_end TIMESTAMP, supplied_efficiency_pct DOUBLE, restlessness DOUBLE, "
        "is_duplicate_key BOOLEAN, is_orphan BOOLEAN, is_out_of_window BOOLEAN, "
        "quality_flags VARCHAR"),
    "silver_sleep_stages": (
        "participant_id VARCHAR, local_date DATE, stage_index INTEGER, "
        "stage VARCHAR, start_time TIMESTAMP, end_time TIMESTAMP, "
        "duration_min INTEGER, derived_duration_min DOUBLE, quality_flags VARCHAR"),
    "quality_results": (
        "run_id VARCHAR, rule_id VARCHAR, source VARCHAR, handling VARCHAR, "
        "severity VARCHAR, reason VARCHAR, checked_count BIGINT, failed_count BIGINT"),
    "silver_steps_minute": SILVER_STEPS_DDL,
    "silver_heart_rate_minute": SILVER_HR_DDL,
    "quality_rejects": REJECTS_DDL,
    "quality_file_counts": (
        "relative_path VARCHAR, source VARCHAR, rule_id VARCHAR, checked_count BIGINT, "
        "failed_count BIGINT"),
    "silver_file_owners": "relative_path VARCHAR, silver_table VARCHAR, participant_id VARCHAR",
    "run_window": "study_start DATE, study_days INTEGER",
}
# silver tables written for each event source (specs/INC-006.md section 2)
SOURCE_TABLES = {
    "sleep": ("silver_sleep_sessions", "silver_sleep_stages"),
    "steps": ("silver_steps_minute",),
    "heart_rate": ("silver_heart_rate_minute",),
}


def _file_stats(kind: str, files: dict[str, list[Any]],
                layer: Any) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Per-file ``quality_file_counts`` and ``silver_file_owners`` rows of one event source.

    Every record yields exactly one session/minute row, in sorted-path then position order, and
    each row's last column lists all rule ids of the record.
    """
    rows = layer.sessions if kind == "sleep" else layer.rows
    rules = sorted(rule for rule, spec in RULES.items() if spec[0] == kind)
    tables = SOURCE_TABLES[kind]
    counts: list[tuple[Any, ...]] = []
    owners: list[tuple[Any, ...]] = []
    offset = 0
    for path in sorted(files):
        records = files[path]
        chunk = rows[offset:offset + len(records)]
        offset += len(records)
        failed: Counter[str] = Counter()
        for row in chunk:
            if row[-1]:
                failed.update(row[-1].split(","))
        counts.extend((path, kind, rule, len(records), failed[rule]) for rule in rules)
        owners.extend((path, tables[0], pid) for pid in sorted({row[0] for row in chunk}))
        if kind == "sleep":
            staged = {row[0] for row, record in zip(chunk, records)
                      if isinstance(record, dict) and isinstance(record.get("stages"), list)
                      and record["stages"]}
            owners.extend((path, tables[1], pid) for pid in sorted(staged))
    return counts, owners


def _ensure_gold(con: duckdb.DuckDBPyConnection) -> None:
    """Create ``gold_clinical_daily`` or bring an older layout to the current columns."""
    ddl = ("CREATE TABLE gold_clinical_daily ("
           + ", ".join(f"{name} {kind}" for name, kind in ALL_GOLD_COLUMNS)
           + ", PRIMARY KEY (participant_id, local_date))")
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = 'gold_clinical_daily'").fetchone()[0]
    if not exists:
        con.execute(ddl)
        return
    current = [(row[0], row[1]) for row in con.execute("DESCRIBE gold_clinical_daily").fetchall()]
    if current == list(ALL_GOLD_COLUMNS):
        return
    present = {name for name, _ in current}
    common = ", ".join(name for name, _ in ALL_GOLD_COLUMNS if name in present)
    con.execute("CREATE TEMP TABLE gold_previous AS SELECT * FROM gold_clinical_daily")
    con.execute("DROP TABLE gold_clinical_daily")
    con.execute(ddl)
    if common:
        con.execute(f"INSERT INTO gold_clinical_daily ({common}) SELECT {common} FROM gold_previous")
    con.execute("DROP TABLE gold_previous")


def _merge_gold(con: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]],
                prune: bool = False) -> None:
    """MERGE full gold rows on (participant_id, local_date); optionally prune other keys."""
    names = [name for name, _ in ALL_GOLD_COLUMNS]
    con.execute(
        "CREATE OR REPLACE TEMP TABLE gold_stage AS SELECT * FROM gold_clinical_daily LIMIT 0")
    if rows:
        marks = ",".join("?" * len(names))
        con.executemany(f"INSERT INTO gold_stage ({', '.join(names)}) VALUES ({marks})", rows)
    updates = ", ".join(f"{name} = s.{name}" for name in names[2:])
    con.execute(
        "MERGE INTO gold_clinical_daily AS g USING gold_stage AS s "
        "ON g.participant_id = s.participant_id AND g.local_date = s.local_date "
        f"WHEN MATCHED THEN UPDATE SET {updates} "
        "WHEN NOT MATCHED THEN INSERT BY NAME")
    if prune:
        con.execute(
            "DELETE FROM gold_clinical_daily WHERE (participant_id, local_date) NOT IN "
            "(SELECT participant_id, local_date FROM gold_stage)")
    con.execute("DROP TABLE gold_stage")


def _reject_rows(run_id: str, rejects: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [
        (run_id, rule, RULES[rule][0], f"{path}:{n}", RULES[rule][3],
         json.dumps(raw, ensure_ascii=False))
        for rule, path, n, raw in rejects
    ]


def _persist(db_path: Path, run_id: str, manifest: list[tuple[Any, ...]],
             t: dict[str, Any], results: list[tuple[Any, ...]],
             side: dict[str, list[tuple[Any, ...]]], window: tuple[date, int],
             prune: bool = False) -> None:
    from wearable_pipeline import incremental

    rejects = _reject_rows(run_id, t["rejects"])
    rejects.sort()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = None
    try:
        con = duckdb.connect(str(db_path))
        con.execute("BEGIN TRANSACTION")
        _replace(con, "raw_manifest", TABLE_DDL["raw_manifest"], manifest)
        _replace(con, "silver_participants", TABLE_DDL["silver_participants"], t["silver_p"])
        _replace(con, "silver_wellness_survey", TABLE_DDL["silver_wellness_survey"],
                 t["silver_s"])
        _replace(con, "silver_sleep_sessions", TABLE_DDL["silver_sleep_sessions"],
                 t["sleep"].sessions)
        _replace(con, "silver_sleep_stages", TABLE_DDL["silver_sleep_stages"], t["sleep"].stages)
        _replace(con, "quality_results", TABLE_DDL["quality_results"],
                 [(r[0], r[1], r[2], r[3], r[4], r[5], r[7], r[8]) for r in results])
        con.execute("DROP TABLE IF EXISTS silver_steps_minute")
        con.execute(f"CREATE TABLE silver_steps_minute ({SILVER_STEPS_DDL})")
        _bulk_load(con, "silver_steps_minute", t["steps"].rows)
        con.execute("DROP TABLE IF EXISTS silver_heart_rate_minute")
        con.execute(f"CREATE TABLE silver_heart_rate_minute ({SILVER_HR_DDL})")
        _bulk_load(con, "silver_heart_rate_minute", t["heart_rate"].rows)
        con.execute("DROP TABLE IF EXISTS quality_rejects")
        con.execute(f"CREATE TABLE quality_rejects ({REJECTS_DDL})")
        _bulk_load(con, "quality_rejects", rejects)
        _replace(con, "quality_file_counts", TABLE_DDL["quality_file_counts"], side["counts"])
        _replace(con, "silver_file_owners", TABLE_DDL["silver_file_owners"], side["owners"])
        _replace(con, "run_window", TABLE_DDL["run_window"], [window])
        _ensure_gold(con)
        _merge_gold(con, t["gold"], prune)
        for table in incremental.DERIVED_TABLES:
            table.refresh(con, None)
        con.execute("COMMIT")
    except Exception as error:
        if con is not None:
            try:
                con.execute("ROLLBACK")
            except duckdb.Error:
                pass
        if isinstance(error, duckdb.Error):
            raise PipelineError(f"database write failed: {error}") from error
        raise
    finally:
        if con is not None:
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
    return _run_full(Path(data_dir), Path(output_dir), study_start, study_days)[0]


def _read_inputs(data_dir: Path, study_days: int) -> tuple[list[dict[str, str]],
                                                           list[dict[str, str]]]:
    """Validate the arguments and read the two required CSV inputs."""
    if study_days < 1:
        raise PipelineError("study_days must be at least 1")
    if not data_dir.is_dir():
        raise PipelineError(f"data directory not found: {data_dir}")
    participants = _read_required(data_dir, PARTICIPANTS_PATH, PARTICIPANT_COLUMNS)
    survey = _read_required(data_dir, SURVEY_PATH, SURVEY_COLUMNS)
    return participants, survey


def _run_full(data_dir: Path, output_dir: Path, study_start: date, study_days: int,
              prune: bool = False) -> tuple[RunResult, int]:
    """Full rebuild; returns the result and the number of staged gold rows.

    ``prune`` (incremental shared-input path only) deletes gold rows outside the staged set.
    """
    participants, survey = _read_inputs(data_dir, study_days)
    manifest = _build_manifest(data_dir)
    sleep_files = _read_event_files(data_dir, manifest, "sleep")
    steps_files = _read_event_files(data_dir, manifest, "steps")
    hr_files = _read_event_files(data_dir, manifest, "heart_rate")
    run_id = _run_id(manifest)
    window = [study_start + timedelta(days=i) for i in range(study_days)]

    t = _transform(participants, survey, window)
    enrolled = {r["participant_id"] for r in participants}
    sleep: SleepLayer = build_sleep_layer(sleep_files, enrolled, window)
    t["sleep"] = sleep
    t["rejects"] = t["rejects"] + sleep.rejects
    t["failed"] = t["failed"] + sleep.failed
    t["checked"]["sleep"] = sleep.checked
    steps: StepsLayer = build_steps_layer(steps_files, enrolled, window)
    t["steps"] = steps
    t["rejects"] = t["rejects"] + steps.rejects
    t["failed"] = t["failed"] + steps.failed
    t["checked"]["steps"] = steps.checked
    hr: HeartRateLayer = build_heart_rate_layer(hr_files, enrolled, window)
    t["heart_rate"] = hr
    t["rejects"] = t["rejects"] + hr.rejects
    t["failed"] = t["failed"] + hr.failed
    t["checked"]["heart_rate"] = hr.checked
    t["gold"] = [
        (*row, *_sleep_gold(sleep, row[0], row[1]), *_steps_gold(steps, row[0], row[1]),
         *_hr_gold(hr, row[0], row[1]))
        for row in t["gold"]
    ]
    results = []
    for rule_id in sorted(RULES):
        source, handling, severity, reason = RULES[rule_id]
        results.append((run_id, rule_id, source, handling, severity, reason,
                        None, t["checked"][source], t["failed"][rule_id]))
    results.sort(key=lambda r: (r[1], r[2]))

    side: dict[str, list[tuple[Any, ...]]] = {"counts": [], "owners": []}
    for kind, files, layer in (("sleep", sleep_files, sleep), ("steps", steps_files, steps),
                               ("heart_rate", hr_files, hr)):
        counts, owners = _file_stats(kind, files, layer)
        side["counts"].extend(counts)
        side["owners"].extend(owners)

    database_path = output_dir / "wearable.duckdb"
    report_path = output_dir / "quality" / "report.md"
    _persist(database_path, run_id, manifest, t, results, side, (study_start, study_days), prune)
    _write_report(report_path, run_id, len(manifest), results)
    return (RunResult(run_id=run_id, database_path=database_path, report_path=report_path),
            len(t["gold"]))
