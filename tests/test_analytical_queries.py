"""INC-010 analytical SQL tests (see specs/INC-010.md section 3).

The SQL files under ``queries/`` do NOT exist at planning time: the implementation phase
creates them. Every behavior test goes through ``wearable_pipeline.query_runner`` (a stub that
raises ``NotImplementedError`` until implemented) and reads the SQL files from ``queries/``;
the static tests fail with an assertion while ``queries/`` is absent. Expected values are
hand-computed on the small synthetic gold fixture below (no participant data).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from wearable_pipeline.cohort import COHORT_COLUMNS
from wearable_pipeline.cohort import TABLE_NAME as COHORT_TABLE
from wearable_pipeline.pipeline import ALL_GOLD_COLUMNS
from wearable_pipeline.query_runner import QueryResult, run_queries

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "queries"
CHRONO_SQL = QUERIES / "chronotype_comparison.sql"
STUDY_SQL = QUERIES / "study_wide_metrics.sql"
DAILY_TABLE = "gold_clinical_daily"

CHRONO_METRICS = ("sleep_total_min", "sleep_efficiency_pct", "deep_sleep_pct",
                  "steps_daily", "hr_daily_bpm")
CHRONO_COLUMNS = ("group_label",) + tuple(
    f"{metric}_{suffix}" for metric in CHRONO_METRICS
    for suffix in ("mean", "participants", "stddev", "valid_days"))
STUDY_COLUMNS = ("metric", "participants", "valid_days", "mean_of_participant_averages")


# --------------------------------------------------------------------------- fixture builders


def cohort_row(pid: str, cohort: str | None, flag: str | None, valid: tuple[int, int, int, int],
               means: tuple[float | None, ...]) -> dict[str, Any]:
    """One ``gold_chronotype_cohort`` row. ``valid`` = sleep, sleep-stage, steps, hr valid days;
    ``means`` = sleep total, efficiency, deep %, steps, heart rate."""
    sleep, stage, steps, hr = valid
    total, eff, deep, step_mean, hr_mean = means
    return {
        "participant_id": pid, "chronotype_cohort": cohort, "cohort_flag": flag,
        "window_days": 28, "sleep_valid_days": sleep, "sleep_stage_valid_days": stage,
        "steps_valid_days": steps, "hr_valid_days": hr, "sleep_total_min_mean": total,
        "sleep_efficiency_pct_mean": eff, "deep_sleep_pct_mean": deep,
        "steps_daily_mean": step_mean, "hr_daily_mean_bpm": hr_mean,
    }


COHORT_ROWS = [
    cohort_row("P001", "A", None, (20, 18, 20, 20), (400, 90, 20, 8000, 60)),
    cohort_row("P002", "A", None, (25, 25, 25, 25), (420, 88, 18, 10000, 64)),
    cohort_row("P003", "A", None, (14, 14, 14, 14), (440, 92, 22, 12000, 68)),
    # sleep and heart-rate means are NULL (below the coverage rule): only steps count
    cohort_row("P007", "A", None, (5, 5, 28, 10), (None, None, None, 9000, None)),
    cohort_row("P004", "B", None, (20, 20, 20, 20), (360, 80, 15, 5000, 72)),
    # heart-rate mean is NULL: cohort B has a single heart-rate participant
    cohort_row("P005", "B", None, (16, 16, 16, 3), (380, 84, 17, 7000, None)),
    # no cohort: never in A or B, even with extreme values
    cohort_row("P006", None, "CHRONOTYPE_MISSING", (28, 28, 28, 28), (999, 99, 99, 99999, 199)),
]

CHRONO_EXPECTED = {
    "A": {"sleep_total_min": (420, 3, 20, 59), "sleep_efficiency_pct": (90, 3, 2, 57),
          "deep_sleep_pct": (20, 3, 2, 57), "steps_daily": (9750, 4, 1707.83, 87),
          "hr_daily_bpm": (64, 3, 4, 59)},
    "B": {"sleep_total_min": (370, 2, 14.14, 36), "sleep_efficiency_pct": (82, 2, 2.83, 36),
          "deep_sleep_pct": (16, 2, 1.41, 36), "steps_daily": (6000, 2, 1414.21, 36),
          "hr_daily_bpm": (72, 1, None, 20)},
    # A minus B: only the mean is defined (calculated from the two cohort means)
    "A_minus_B": {"sleep_total_min": (50, None, None, None),
                  "sleep_efficiency_pct": (8, None, None, None),
                  "deep_sleep_pct": (4, None, None, None),
                  "steps_daily": (3750, None, None, None),
                  "hr_daily_bpm": (-8, None, None, None)},
}


def expected_chrono_row(label: str) -> dict[str, Any]:
    row: dict[str, Any] = {"group_label": label}
    for metric, (mean, count, stddev, days) in CHRONO_EXPECTED[label].items():
        row.update({f"{metric}_mean": mean, f"{metric}_participants": count,
                    f"{metric}_stddev": stddev, f"{metric}_valid_days": days})
    return row


def daily_row(pid: str, day: int, steps: int | None = None, sleep: int | None = None,
              hr: float | None = None) -> dict[str, Any]:
    return {"participant_id": pid, "local_date": date(2026, 1, day), "total_steps": steps,
            "total_sleep_min": sleep, "hr_mean_bpm": hr}


DAILY_ROWS = [
    daily_row("P001", 1, steps=1000, sleep=400, hr=60.0),
    daily_row("P001", 2, steps=2000, hr=70.0),
    daily_row("P001", 3, steps=3000, sleep=500),
    daily_row("P002", 1, steps=5000, hr=80.0),
    daily_row("P002", 2),
    daily_row("P003", 1, steps=9000, sleep=600),
    daily_row("P004", 1),  # present in gold but with no valid measure: never counted
]

# participant averages: steps 2000 / 5000 / 9000, sleep 450 / 600, hr 65 / 80;
# a pooled mean would be 4000, 500 and 70 (different, so it would fail)
STUDY_EXPECTED = [
    {"metric": "steps_per_day", "participants": 3, "valid_days": 5,
     "mean_of_participant_averages": 5333.33},
    {"metric": "sleep_total_min", "participants": 2, "valid_days": 3,
     "mean_of_participant_averages": 525.0},
    {"metric": "hr_mean_bpm", "participants": 2, "valid_days": 3,
     "mean_of_participant_averages": 72.5},
]


def _insert(con: duckdb.DuckDBPyConnection, table: str, columns: tuple[tuple[str, str], ...],
            rows: list[dict[str, Any]]) -> None:
    names = [name for name, _ in columns]
    con.execute(f"CREATE TABLE {table} (" + ", ".join(f"{n} {k}" for n, k in columns) + ")")
    for row in rows:
        con.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(names))})",
                    [row.get(name) for name in names])


def build_db(path: Path, cohort: list[dict[str, Any]] | None = None,
             daily: list[dict[str, Any]] | None = None) -> Path:
    """A database holding ONLY the gold tables passed (real column names from the code)."""
    con = duckdb.connect(str(path))
    try:
        if cohort is not None:
            _insert(con, COHORT_TABLE, COHORT_COLUMNS, cohort)
        if daily is not None:
            _insert(con, DAILY_TABLE, ALL_GOLD_COLUMNS, daily)
    finally:
        con.close()
    return path


def result_rows(result: QueryResult) -> list[dict[str, Any]]:
    return [dict(zip(result.columns, row)) for row in result.rows]


def assert_rows(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert list(got) == list(want)
        for key, value in want.items():
            if value is None:
                assert got[key] is None, key
            elif isinstance(value, float) or isinstance(got[key], float):
                assert got[key] == pytest.approx(value, abs=1e-9), key
            else:
                assert got[key] == value, key
                if isinstance(value, int) and not isinstance(value, bool):
                    assert type(got[key]) is int, key  # counts and day totals are integers


def run_one(db: Path, sql: Path) -> QueryResult:
    (result,) = run_queries(db, sql)
    assert result.name == sql.name
    return result


@pytest.fixture()
def full_db(tmp_path: Path) -> Path:
    return build_db(tmp_path / "wearable.duckdb", COHORT_ROWS, DAILY_ROWS)


# --------------------------------------------------------------------------- static contract


def sql_text(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(ROOT)} must be created by the implementation phase"
    return path.read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def test_queries_directory_holds_exactly_the_two_specified_sql_files():
    assert sorted(p.name for p in QUERIES.glob("*.sql")) == [
        "chronotype_comparison.sql", "study_wide_metrics.sql"]


@pytest.mark.parametrize("path", [CHRONO_SQL, STUDY_SQL], ids=lambda p: p.name)
def test_sql_reads_only_gold_tables(path: Path):
    body = strip_comments(sql_text(path))
    ctes = {m.lower() for m in re.findall(r"\b(\w+)\s+AS\s*\(", body, flags=re.I)}
    tables = {m.lower() for m in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", body, flags=re.I)}
    sources = tables - ctes
    assert sources, "the query must read a gold table"
    assert all(name.startswith("gold_") for name in sources), sources
    assert not re.search(r"\b(silver|bronze|quality)_\w+|raw_manifest|read_(csv|json|parquet)|\bglob\(",
                         body, flags=re.I)


def test_each_query_reads_only_its_specified_gold_table():
    chrono = strip_comments(sql_text(CHRONO_SQL)).lower()
    study = strip_comments(sql_text(STUDY_SQL)).lower()
    assert COHORT_TABLE in chrono and DAILY_TABLE not in chrono
    assert DAILY_TABLE in study and COHORT_TABLE not in study


@pytest.mark.parametrize("path", [CHRONO_SQL, STUDY_SQL], ids=lambda p: p.name)
def test_sql_is_one_read_only_select_with_explicit_order_and_no_significance_claim(path: Path):
    body = strip_comments(sql_text(path)).strip()
    assert body.rstrip(";").count(";") == 0, "exactly one statement"
    assert re.match(r"(?is)(select|with)\b", body)
    assert re.search(r"(?i)\border\s+by\b", body)
    assert not re.search(r"(?i)\b(insert|update|delete|create|drop|alter|copy|attach|pragma|install)\b", body)
    assert not re.search(r"(?i)significan|p_value|pvalue|t_?test", body)


# --------------------------------------------------------------------------- chronotype


def test_chronotype_columns_and_grain_are_the_three_comparison_rows(full_db: Path):
    result = run_one(full_db, CHRONO_SQL)
    assert result.columns == CHRONO_COLUMNS
    assert [row["group_label"] for row in result_rows(result)] == ["A", "B", "A_minus_B"]


def test_chronotype_comparison_exact_values(full_db: Path):
    rows = result_rows(run_one(full_db, CHRONO_SQL))
    assert_rows(rows, [expected_chrono_row(label) for label in ("A", "B", "A_minus_B")])


def test_chronotype_values_are_rounded_to_two_decimals(full_db: Path):
    for row in result_rows(run_one(full_db, CHRONO_SQL)):
        for key, value in row.items():
            if isinstance(value, float):
                assert round(value, 2) == value, key


def test_chronotype_difference_comes_from_the_unrounded_cohort_means(tmp_path: Path):
    # A 10.006 -> 10.01 and B 10.004 -> 10.00 when rounded; the unrounded difference is
    # 0.002 -> 0.00, whereas subtracting the rounded means would give 0.01.
    rows = [cohort_row("P001", "A", None, (20, 20, 20, 20), (10.006, 50.0, 10.0, 1000, 60)),
            cohort_row("P002", "B", None, (20, 20, 20, 20), (10.004, 50.0, 10.0, 1000, 60))]
    db = build_db(tmp_path / "w.duckdb", rows)
    by_label = {r["group_label"]: r for r in result_rows(run_one(db, CHRONO_SQL))}
    assert by_label["A"]["sleep_total_min_mean"] == pytest.approx(10.01)
    assert by_label["B"]["sleep_total_min_mean"] == pytest.approx(10.0)
    assert by_label["A_minus_B"]["sleep_total_min_mean"] == pytest.approx(0.0, abs=1e-9)


def test_chronotype_null_handling_excludes_null_means_from_counts_and_valid_days(full_db: Path):
    by_label = {r["group_label"]: r for r in result_rows(run_one(full_db, CHRONO_SQL))}
    # P007 (A) has NULL sleep and heart-rate means: not counted, its valid days not summed
    assert by_label["A"]["sleep_total_min_participants"] == 3
    assert by_label["A"]["sleep_total_min_valid_days"] == 59
    assert by_label["A"]["steps_daily_participants"] == 4
    # a single contributing participant has no sample standard deviation
    assert by_label["B"]["hr_daily_bpm_participants"] == 1
    assert by_label["B"]["hr_daily_bpm_stddev"] is None
    assert by_label["B"]["hr_daily_bpm_valid_days"] == 20


def test_participants_without_a_cohort_are_in_neither_group(tmp_path: Path):
    only_unassigned = [COHORT_ROWS[-1]]
    db = build_db(tmp_path / "w.duckdb", only_unassigned)
    rows = result_rows(run_one(db, CHRONO_SQL))
    assert [r["group_label"] for r in rows] == ["A", "B", "A_minus_B"]
    assert all(r[f"{m}_participants"] in (0, None) for r in rows for m in CHRONO_METRICS)
    assert all(r[f"{m}_mean"] is None for r in rows for m in CHRONO_METRICS)


def test_missing_cohort_keeps_its_row_with_zero_counts_and_null_difference(tmp_path: Path):
    only_a = [row for row in COHORT_ROWS if row["chronotype_cohort"] == "A"]
    db = build_db(tmp_path / "w.duckdb", only_a)
    by_label = {r["group_label"]: r for r in result_rows(run_one(db, CHRONO_SQL))}
    assert list(by_label) == ["A", "B", "A_minus_B"]
    for metric in CHRONO_METRICS:
        assert by_label["B"][f"{metric}_participants"] == 0
        assert by_label["B"][f"{metric}_valid_days"] == 0
        assert by_label["B"][f"{metric}_mean"] is None
        assert by_label["B"][f"{metric}_stddev"] is None
        assert by_label["A_minus_B"][f"{metric}_mean"] is None
    assert by_label["A"]["sleep_total_min_mean"] == pytest.approx(420)


def test_empty_cohort_table_gives_three_rows_of_zero_or_null(tmp_path: Path):
    db = build_db(tmp_path / "w.duckdb", [])
    rows = result_rows(run_one(db, CHRONO_SQL))
    assert [r["group_label"] for r in rows] == ["A", "B", "A_minus_B"]
    assert all(r["steps_daily_mean"] is None for r in rows)


def test_chronotype_query_needs_only_the_gold_cohort_table(tmp_path: Path):
    db = build_db(tmp_path / "w.duckdb", COHORT_ROWS)  # no gold_clinical_daily in this database
    assert_rows(result_rows(run_one(db, CHRONO_SQL)),
                [expected_chrono_row(label) for label in ("A", "B", "A_minus_B")])


# --------------------------------------------------------------------------- study-wide


def test_study_wide_columns_and_grain_are_one_row_per_metric(full_db: Path):
    result = run_one(full_db, STUDY_SQL)
    assert result.columns == STUDY_COLUMNS
    assert [r["metric"] for r in result_rows(result)] == [
        "steps_per_day", "sleep_total_min", "hr_mean_bpm"]


def test_study_wide_metrics_are_means_of_participant_averages(full_db: Path):
    assert_rows(result_rows(run_one(full_db, STUDY_SQL)), STUDY_EXPECTED)


def test_study_wide_null_days_and_all_null_participants_are_not_counted(full_db: Path):
    by_metric = {r["metric"]: r for r in result_rows(run_one(full_db, STUDY_SQL))}
    assert by_metric["sleep_total_min"]["participants"] == 2  # P002 and P004 have no sleep
    assert by_metric["hr_mean_bpm"]["participants"] == 2      # P003 and P004 have no heart rate
    assert by_metric["steps_per_day"]["participants"] == 3    # P004 has no steps


def test_study_wide_empty_daily_table_gives_zero_counts_and_null_means(tmp_path: Path):
    db = build_db(tmp_path / "w.duckdb", None, [])
    rows = result_rows(run_one(db, STUDY_SQL))
    assert [r["metric"] for r in rows] == ["steps_per_day", "sleep_total_min", "hr_mean_bpm"]
    assert all(r["participants"] == 0 and r["valid_days"] == 0 for r in rows)
    assert all(r["mean_of_participant_averages"] is None for r in rows)


def test_study_wide_query_needs_only_gold_clinical_daily(tmp_path: Path):
    db = build_db(tmp_path / "w.duckdb", None, DAILY_ROWS)  # no cohort table in this database
    assert_rows(result_rows(run_one(db, STUDY_SQL)), STUDY_EXPECTED)


def test_study_wide_valid_days_follow_the_cohort_definition_of_a_valid_day(tmp_path: Path):
    # a valid day is a gold_clinical_daily row whose metric value is not NULL; the same
    # definition as the cohort table's *_valid_days (spec 3.2), with no coverage threshold
    rows = [daily_row("P001", d, steps=100 * d) for d in range(1, 4)]  # 3 valid days < 14
    db = build_db(tmp_path / "w.duckdb", None, rows)
    steps = result_rows(run_one(db, STUDY_SQL))[0]
    assert (steps["participants"], steps["valid_days"]) == (1, 3)
    assert steps["mean_of_participant_averages"] == pytest.approx(200.0)


# --------------------------------------------------------------------------- determinism


def test_results_are_identical_across_runs_and_insertion_orders(tmp_path: Path):
    first = build_db(tmp_path / "a.duckdb", COHORT_ROWS, DAILY_ROWS)
    second = build_db(tmp_path / "b.duckdb", list(reversed(COHORT_ROWS)), list(reversed(DAILY_ROWS)))
    for sql in (CHRONO_SQL, STUDY_SQL):
        once = run_one(first, sql)
        assert run_one(first, sql) == once
        assert run_one(second, sql) == once


def test_a_directory_runs_every_query_in_file_name_order(full_db: Path):
    results = run_queries(full_db, QUERIES)
    assert [r.name for r in results] == ["chronotype_comparison.sql", "study_wide_metrics.sql"]
    assert results[0].columns == CHRONO_COLUMNS and results[1].columns == STUDY_COLUMNS
