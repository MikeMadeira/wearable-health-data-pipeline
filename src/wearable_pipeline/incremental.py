"""Incremental mode: reprocess only changed raw files (contract: specs/INC-006.md)."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import duckdb

from wearable_pipeline.heart_rate import build_heart_rate_layer
from wearable_pipeline.pipeline import (
    ALL_GOLD_COLUMNS, DEFAULT_STUDY_DAYS, DEFAULT_STUDY_START, HR_SLICE, RULES, SLEEP_SLICE,
    SOURCE_TABLES, STEPS_SLICE, TABLE_DDL, PipelineError, _build_manifest, _bulk_load,
    _file_stats, _hr_gold, _merge_gold, _read_event_json, _read_inputs, _reject_rows,
    _replace, _report_text, _run_full, _run_id, _sleep_gold, _steps_gold,
)
from wearable_pipeline.sleep import build_sleep_layer
from wearable_pipeline.steps import build_steps_layer
from wearable_pipeline.weekly import TABLE_NAME as WEEKLY_TABLE, WEEKLY, WEEKLY_COLUMNS

OUTCOME_INCREMENTAL = "incremental"
OUTCOME_NOOP = "noop"
OUTCOME_FULL = "full"

# fallback_reason values (specs/INC-006.md section 5)
REASON_NO_DATABASE = "no_database"
REASON_NO_MANIFEST = "no_manifest"
REASON_OLD_SCHEMA = "old_schema"
REASON_SHARED_INPUT_CHANGED = "shared_input_changed"
REASON_WINDOW_CHANGED = "window_changed"


@dataclass(frozen=True)
class ManifestDiff:
    """Paths of the current raw manifest classified against the stored one (sorted tuples)."""

    new: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]


@dataclass(frozen=True)
class IncrementalResult:
    """Outcome of one ``run_incremental`` call."""

    run_id: str
    database_path: Path
    report_path: Path
    outcome: str  # OUTCOME_INCREMENTAL, OUTCOME_NOOP or OUTCOME_FULL
    fallback_reason: str | None  # None unless outcome is OUTCOME_FULL
    new_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    affected_gold_rows: int  # gold rows recomputed by this run (0 for a no-op)


class DerivedTable(Protocol):
    """Extension point for later gold tables (INC-007, INC-008); see specs/INC-006.md section 9."""

    name: str

    def refresh(
        self,
        con: duckdb.DuckDBPyConnection,
        affected: Mapping[str, frozenset[date]] | None,
    ) -> None:
        """Bring the table up to date inside the caller's open transaction.

        ``affected`` maps participant_id to the local dates whose gold_clinical_daily row was
        recomputed; ``None`` means every row must be rebuilt.
        """


DERIVED_TABLES: list[DerivedTable] = [WEEKLY]


def read_event_file(path: Path, relative: str, kind: str) -> list[Any]:
    """Parse one sleep, steps or heart-rate JSON file; the only event parser in incremental mode."""
    return _read_event_json(Path(path), relative, kind)


def diff_manifests(
    previous: Mapping[str, str], current: Mapping[str, str]
) -> ManifestDiff:
    """Classify every path of two ``relative_path -> sha256`` mappings."""
    return ManifestDiff(
        new=tuple(sorted(p for p in current if p not in previous)),
        changed=tuple(sorted(p for p in current if p in previous and previous[p] != current[p])),
        removed=tuple(sorted(p for p in previous if p not in current)),
        unchanged=tuple(sorted(p for p in current if p in previous and previous[p] == current[p])),
    )


def changed_days(
    previous: Sequence[tuple[Any, ...]],
    new: Sequence[tuple[Any, ...]],
    date_index: int = 1,
) -> frozenset[date]:
    """Local dates whose multiset of silver rows differs between ``previous`` and ``new``."""
    def by_day(rows: Sequence[tuple[Any, ...]]) -> dict[date, Counter[tuple[Any, ...]]]:
        days: dict[date, Counter[tuple[Any, ...]]] = {}
        for row in rows:
            if row[date_index] is not None:
                days.setdefault(row[date_index], Counter())[tuple(row)] += 1
        return days

    before, after = by_day(previous), by_day(new)
    return frozenset(day for day in before.keys() | after.keys()
                     if before.get(day, Counter()) != after.get(day, Counter()))


_SHARED_SOURCES = frozenset({"participants", "wellness_survey", "device_metadata"})
_EVENT_KINDS = ("sleep", "steps", "heart_rate")
_BUILDERS = {"sleep": build_sleep_layer, "steps": build_steps_layer,
             "heart_rate": build_heart_rate_layer}
_GOLD_SLICES = {"sleep": SLEEP_SLICE, "steps": STEPS_SLICE, "heart_rate": HR_SLICE}
_GOLD_VALUES = {"sleep": _sleep_gold, "steps": _steps_gold, "heart_rate": _hr_gold}
_EXPECTED_TABLES = (*TABLE_DDL, "gold_clinical_daily", WEEKLY_TABLE)


def _layer_tables(kind: str, layer: Any) -> dict[str, list[tuple[Any, ...]]]:
    if kind == "sleep":
        return {"silver_sleep_sessions": layer.sessions, "silver_sleep_stages": layer.stages}
    return {SOURCE_TABLES[kind][0]: layer.rows}


def _expected_schema() -> dict[str, list[tuple[str, str]]]:
    """Ordered (column, type) lists of the tables the current pipeline creates."""
    con = duckdb.connect(":memory:")
    try:
        for name, ddl in TABLE_DDL.items():
            con.execute(f"CREATE TABLE {name} ({ddl})")
        con.execute("CREATE TABLE gold_clinical_daily ("
                    + ", ".join(f"{n} {k}" for n, k in ALL_GOLD_COLUMNS) + ")")
        con.execute(f"CREATE TABLE {WEEKLY_TABLE} ("
                    + ", ".join(f"{n} {k}" for n, k in WEEKLY_COLUMNS) + ")")
        return {name: [(r[0], r[1]) for r in con.execute(f"DESCRIBE {name}").fetchall()]
                for name in _EXPECTED_TABLES}
    finally:
        con.close()


@dataclass
class _Previous:
    """What the stored output says about the previous run (read-only snapshot)."""

    manifest: dict[str, tuple[str, str]]  # path -> (source, sha256)
    window: tuple[date, int]
    results: dict[str, tuple[Any, Any]]  # rule_id -> (checked_count, failed_count)
    counts: list[tuple[Any, ...]]
    owners: list[tuple[Any, ...]]


def _read_previous(db_path: Path) -> tuple[str | None, _Previous | None]:
    """Return (fallback reason, None) or (None, snapshot); never writes to the database."""
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'").fetchall()}
            if "raw_manifest" not in tables:
                return REASON_NO_MANIFEST, None
            if con.execute("SELECT count(*) FROM raw_manifest").fetchone()[0] == 0:
                return REASON_NO_MANIFEST, None
            expected = _expected_schema()
            for name in _EXPECTED_TABLES:
                if name not in tables:
                    return REASON_OLD_SCHEMA, None
                described = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {name}").fetchall()]
                if described != expected[name]:
                    return REASON_OLD_SCHEMA, None
            window = con.execute("SELECT study_start, study_days FROM run_window").fetchall()
            if len(window) != 1:
                return REASON_OLD_SCHEMA, None
            manifest = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT relative_path, source, sha256 FROM raw_manifest").fetchall()}
            results = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT rule_id, checked_count, failed_count FROM quality_results").fetchall()}
            counts = con.execute("SELECT * FROM quality_file_counts").fetchall()
            owners = con.execute("SELECT * FROM silver_file_owners").fetchall()
            return None, _Previous(manifest, (window[0][0], window[0][1]), results, counts,
                                   owners)
        finally:
            con.close()
    except duckdb.Error as error:
        raise PipelineError(f"cannot read existing output {db_path}: {error}") from error


@dataclass
class _SourcePlan:
    """Silver, reject and side-table changes of one event source."""

    kind: str
    whole: bool  # True: every file of the source was re-parsed and replaced
    touched: list[str]  # changed, new and removed paths
    pids: list[str]  # participants whose silver rows are replaced (whole: unused)
    tables: dict[str, list[tuple[Any, ...]]]
    rejects: list[tuple[Any, ...]]
    counts: list[tuple[Any, ...]]
    owners: list[tuple[Any, ...]]
    daily: dict[tuple[str, date], Any]


def _plan_source(kind: str, data_dir: Path, current: dict[str, str], removed: list[str],
                 new_or_changed: set[str], previous: _Previous, enrolled: set[str],
                 window: list[date]) -> _SourcePlan | None:
    """Parse what changed in one event source and decide the replacement scope."""
    build = _BUILDERS[kind]
    cur_files = sorted(p for p, source in current.items() if source == kind)
    changed = [p for p in cur_files if p in new_or_changed]
    gone = [p for p in removed if previous.manifest[p][0] == kind]
    if not changed and not gone:
        return None
    content = {f: read_event_file(data_dir / f, f, kind) for f in changed}
    fresh = {f: build({f: content[f]}, enrolled, window) for f in changed}
    fresh_stats = {f: _file_stats(kind, {f: content[f]}, fresh[f]) for f in changed}
    tables = SOURCE_TABLES[kind]
    stored: dict[str, set[str]] = {}
    for path, table, pid in previous.owners:
        if table in tables:
            stored.setdefault(path, set()).add(pid)
    fresh_owned = {f: {o[2] for o in fresh_stats[f][1]} for f in changed}
    holders: dict[str, set[str]] = {}
    for g in cur_files:
        for pid in (fresh_owned[g] if g in fresh_owned else stored.get(g, set())):
            holders.setdefault(pid, set()).add(g)
    touched = [*changed, *gone]
    affected: set[str] = set()
    conflict = False
    for f in touched:
        owned = stored.get(f, set()) | fresh_owned.get(f, set())
        affected |= owned
        conflict = conflict or any(holders.get(pid, set()) - {f} for pid in owned)
    if conflict:
        for g in cur_files:
            if g not in content:
                content[g] = read_event_file(data_dir / g, g, kind)
        layer = build({g: content[g] for g in cur_files}, enrolled, window)
        counts, owners = _file_stats(kind, {g: content[g] for g in cur_files}, layer)
        return _SourcePlan(kind, True, touched, [], _layer_tables(kind, layer), layer.rejects,
                           counts, owners, dict(layer.daily))
    merged: dict[str, list[tuple[Any, ...]]] = {t: [] for t in tables}
    rejects: list[tuple[Any, ...]] = []
    counts, owners, daily = [], [], {}
    for f in changed:
        for name, rows in _layer_tables(kind, fresh[f]).items():
            merged[name].extend(rows)
        rejects.extend(fresh[f].rejects)
        counts.extend(fresh_stats[f][0])
        owners.extend(fresh_stats[f][1])
        daily.update(fresh[f].daily)
    return _SourcePlan(kind, False, touched, sorted(affected), merged, rejects, counts, owners,
                       daily)


def _quality_results(run_id: str, previous: _Previous, counts: list[tuple[Any, ...]]
                     ) -> list[tuple[Any, ...]]:
    """Rebuild ``quality_results`` rows (full-rebuild layout) from per-file counts."""
    failed: Counter[str] = Counter()
    checked: Counter[str] = Counter()
    seen: set[str] = set()
    for path, source, rule, n, bad in counts:
        failed[rule] += bad
        if path not in seen:
            seen.add(path)
            checked[source] += n
    results = []
    for rule_id in sorted(RULES):
        source, handling, severity, reason = RULES[rule_id]
        if source in _EVENT_KINDS:
            total_checked, total_failed = checked[source], failed[rule_id]
        else:
            total_checked, total_failed = previous.results.get(rule_id, (0, 0))
        results.append((run_id, rule_id, source, handling, severity, reason, None,
                        total_checked, total_failed))
    results.sort(key=lambda r: (r[1], r[2]))
    return results


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    if table in ("silver_steps_minute", "silver_heart_rate_minute", "quality_rejects"):
        _bulk_load(con, table, rows)
    else:
        marks = ", ".join("?" * len(rows[0]))
        con.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)


def _select_pids(con: duckdb.DuckDBPyConnection, table: str,
                 pids: list[str] | None) -> list[tuple[Any, ...]]:
    if pids is None:
        return con.execute(f"SELECT * FROM {table}").fetchall()
    if not pids:
        return []
    return con.execute(
        f"SELECT * FROM {table} WHERE participant_id IN (SELECT unnest(?))", [pids]).fetchall()


def _apply_plans(con: duckdb.DuckDBPyConnection, plans: list[_SourcePlan], run_id: str
                 ) -> dict[tuple[str, date], list[Any]]:
    """Replace silver and rejects of each plan; return the staged gold rows."""
    staged: dict[tuple[str, date], list[Any]] = {}
    for plan in plans:
        tables = SOURCE_TABLES[plan.kind]
        scope = None if plan.whole else plan.pids
        previous_by_pid: dict[str, list[tuple[Any, ...]]] = {}
        new_by_pid: dict[str, list[tuple[Any, ...]]] = {}
        for table in tables:
            for row in _select_pids(con, table, scope):
                previous_by_pid.setdefault(row[0], []).append(row)
            for row in plan.tables[table]:
                new_by_pid.setdefault(row[0], []).append(row)
        days = {pid: changed_days(previous_by_pid.get(pid, []), new_by_pid.get(pid, []))
                for pid in previous_by_pid.keys() | new_by_pid.keys()}
        for table in tables:
            if scope is None:
                con.execute(f"DELETE FROM {table}")
            elif scope:
                con.execute(f"DELETE FROM {table} WHERE participant_id IN (SELECT unnest(?))",
                            [scope])
            _insert(con, table, plan.tables[table])
        if plan.whole:
            con.execute("DELETE FROM quality_rejects WHERE source = ?", [plan.kind])
        else:
            for path in plan.touched:
                con.execute("DELETE FROM quality_rejects WHERE starts_with(row_ref, ?)",
                            [f"{path}:"])
        _insert(con, "quality_rejects", _reject_rows(run_id, plan.rejects))
        wanted = {pid: sorted(d) for pid, d in days.items() if d}
        if not wanted:
            continue
        existing = {(r[0], r[1]): r for r in con.execute(
            "SELECT * FROM gold_clinical_daily WHERE participant_id IN (SELECT unnest(?))",
            [sorted(wanted)]).fetchall()}
        layer = SimpleNamespace(daily=plan.daily)
        for pid, pid_days in wanted.items():
            for day in pid_days:
                if (pid, day) not in existing:
                    continue
                row = staged.setdefault((pid, day), list(existing[(pid, day)]))
                row[_GOLD_SLICES[plan.kind]] = _GOLD_VALUES[plan.kind](layer, pid, day)
    return staged


def run_incremental(
    data_dir: Path,
    output_dir: Path,
    *,
    study_start: date = DEFAULT_STUDY_START,
    study_days: int = DEFAULT_STUDY_DAYS,
) -> IncrementalResult:
    """Bring ``output_dir`` to the state a full rebuild of ``data_dir`` would give.

    Raises ``PipelineError`` and leaves the previous output unchanged on any failure.
    """
    data_dir, output_dir = Path(data_dir), Path(output_dir)
    participants, _ = _read_inputs(data_dir, study_days)
    database_path = output_dir / "wearable.duckdb"
    report_path = output_dir / "quality" / "report.md"

    def rebuild(reason: str, prune: bool, diff: ManifestDiff | None = None) -> IncrementalResult:
        result, staged = _run_full(data_dir, output_dir, study_start, study_days, prune)
        return IncrementalResult(
            result.run_id, result.database_path, result.report_path, OUTCOME_FULL, reason,
            diff.new if diff else (), diff.changed if diff else (),
            diff.removed if diff else (), staged)

    if not database_path.is_file():
        return rebuild(REASON_NO_DATABASE, False)
    reason, previous = _read_previous(database_path)
    if reason is not None or previous is None:
        return rebuild(reason or REASON_OLD_SCHEMA, False)

    manifest = _build_manifest(data_dir)
    run_id = _run_id(manifest)
    current = {row[0]: row[1] for row in manifest}
    diff = diff_manifests({p: v[1] for p, v in previous.manifest.items()},
                          {row[0]: row[3] for row in manifest})
    touched_sources = ({current[p] for p in (*diff.new, *diff.changed)}
                       | {previous.manifest[p][0] for p in diff.removed})
    if touched_sources & _SHARED_SOURCES:
        return rebuild(REASON_SHARED_INPUT_CHANGED, True, diff)
    if previous.window != (study_start, study_days):
        return rebuild(REASON_WINDOW_CHANGED, True, diff)
    if not (diff.new or diff.changed or diff.removed):
        return IncrementalResult(run_id, database_path, report_path, OUTCOME_NOOP, None,
                                 (), (), (), 0)

    enrolled = {row["participant_id"] for row in participants}
    window = [study_start + timedelta(days=i) for i in range(study_days)]
    new_or_changed = set(diff.new) | set(diff.changed)
    plans = [plan for kind in _EVENT_KINDS
             if (plan := _plan_source(kind, data_dir, current, list(diff.removed), new_or_changed,
                                      previous, enrolled, window)) is not None]

    counts, owners = list(previous.counts), list(previous.owners)
    for plan in plans:
        if plan.whole:
            counts = [r for r in counts if r[1] != plan.kind]
            owners = [r for r in owners if r[1] not in SOURCE_TABLES[plan.kind]]
        else:
            touched = set(plan.touched)
            counts = [r for r in counts if r[0] not in touched]
            owners = [r for r in owners if r[0] not in touched]
        counts.extend(plan.counts)
        owners.extend(plan.owners)
    results = _quality_results(run_id, previous, counts)
    report = _report_text(run_id, len(manifest), results)

    con = None
    try:
        con = duckdb.connect(str(database_path))
        con.execute("BEGIN TRANSACTION")
        staged = _apply_plans(con, plans, run_id)
        con.execute("UPDATE quality_rejects SET run_id = ?", [run_id])
        _replace(con, "raw_manifest", TABLE_DDL["raw_manifest"], manifest)
        _replace(con, "quality_results", TABLE_DDL["quality_results"],
                 [(r[0], r[1], r[2], r[3], r[4], r[5], r[7], r[8]) for r in results])
        _replace(con, "quality_file_counts", TABLE_DDL["quality_file_counts"], sorted(counts))
        _replace(con, "silver_file_owners", TABLE_DDL["silver_file_owners"], sorted(owners))
        if staged:
            _merge_gold(con, [tuple(row) for _, row in sorted(staged.items())])
            affected: dict[str, set[date]] = {}
            for pid, day in staged:
                affected.setdefault(pid, set()).add(day)
            frozen = {pid: frozenset(days) for pid, days in affected.items()}
            for table in DERIVED_TABLES:
                table.refresh(con, frozen)
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

    try:
        temporary = report_path.with_name(report_path.name + ".tmp")
        temporary.write_text(report, encoding="utf-8", newline="\n")
        os.replace(temporary, report_path)
    except OSError as error:
        raise PipelineError(f"cannot write report: {error}") from error
    return IncrementalResult(run_id, database_path, report_path, OUTCOME_INCREMENTAL, None,
                             diff.new, diff.changed, diff.removed, len(staged))
