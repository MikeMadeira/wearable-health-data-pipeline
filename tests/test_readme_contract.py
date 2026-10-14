"""INC-010 README contract tests (see specs/INC-010.md section 5).

``README.md`` is rewritten by the implementation phase; these tests only read it. They fail
with assertions until the README has the specified sections, diagrams, commands and captured
output. Table and column names are cross-checked against the code, never against a copy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_analytical_queries import CHRONO_COLUMNS, STUDY_COLUMNS
from wearable_pipeline import cohort, pipeline, weekly

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

PROPOSED = "proposed, not implemented"
CLOUD_TERMS = ("S3", "Iceberg", "Glue", "Athena", "EventBridge", "Step Functions", "AWS Batch",
               "ECS", "Parquet")
GOLD_TABLES = ("gold_clinical_daily", "gold_biostat_weekly", "gold_chronotype_cohort")
QUERY_COMMAND = "python -m wearable_pipeline.query_runner queries --database output/wearable.duckdb"
# INC-009 verified commands (reports/INC-009/implementer.md; compare form: specs/INC-009.md 4)
RUN_COMMAND = "docker compose run --rm pipeline"
HOST_COMMAND = "HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose run --rm pipeline"
INCREMENTAL_COMMAND = ("docker compose run --rm pipeline python -m wearable_pipeline run "
                       "--mode incremental --data-dir /data --output-dir /output")
TEST_COMMAND = "docker compose run --rm pipeline python -m pytest -q"
COMPARE_PATTERN = (r"docker compose run --rm (?:-v \S+ )*pipeline "
                   r"python -m wearable_pipeline compare /output /other")
ALLOWED_RUN_PATTERNS = (
    re.escape(RUN_COMMAND), re.escape(INCREMENTAL_COMMAND), re.escape(TEST_COMMAND), COMPARE_PATTERN)


# --------------------------------------------------------------------------- helpers


def sections() -> dict[str, str]:
    """Level-2 heading text -> section body (fenced code is kept; ``##`` inside fences ignored)."""
    result: dict[str, list[str]] = {}
    current = None
    fenced = False
    for line in README.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = line[3:].strip()
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return {name: "\n".join(lines) for name, lines in result.items()}


def section(name: str) -> str:
    found = sections()
    assert name in found, f"README needs a '## {name}' section"
    return found[name]


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group(1).strip(), m.group(2))
            for m in re.finditer(r"```([^\n]*)\n(.*?)```", text, flags=re.S)]


def prose(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def mermaid_blocks() -> list[str]:
    return [body for lang, body in fenced_blocks(README) if lang == "mermaid"]


def commands() -> list[str]:
    """Logical command lines of every non-mermaid fenced block (continuations joined)."""
    lines: list[str] = []
    for lang, body in fenced_blocks(README):
        if lang == "mermaid":
            continue
        lines += [line.strip() for line in body.replace("\\\n", " ").splitlines() if line.strip()]
    return [re.sub(r"\s+", " ", line) for line in lines]


def has_term(text: str, term: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text) is not None


def table_rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("|")]


def ddl_columns(ddl: str) -> list[str]:
    return [part.strip().split()[0] for part in ddl.split(",") if part.strip()]


def known_tables_and_columns() -> tuple[dict[str, set[str]], set[str]]:
    tables = {name: set(ddl_columns(ddl)) for name, ddl in pipeline.TABLE_DDL.items()}
    tables[pipeline_gold()] = {name for name, _ in pipeline.ALL_GOLD_COLUMNS}
    tables[weekly.TABLE_NAME] = {name for name, _ in weekly.WEEKLY_COLUMNS}
    tables[cohort.TABLE_NAME] = {name for name, _ in cohort.COHORT_COLUMNS}
    return tables, set().union(*tables.values())


def pipeline_gold() -> str:
    return "gold_clinical_daily"


def known_names() -> set[str]:
    tables, columns = known_tables_and_columns()
    modules = {p.stem for p in (ROOT / "src" / "wearable_pipeline").glob("*.py")}
    scripts = {p.stem for p in (ROOT / "scripts").glob("*.py")}
    return set(tables) | columns | modules | scripts | {"run_id"}


# --------------------------------------------------------------------------- structure


def test_readme_has_the_specified_level_two_sections():
    found = set(sections())
    required = {"Project structure", "Local setup", "Agent workflow", "Running the pipeline",
                "Architecture", "Analytical query output", "Decisions and trade-offs",
                "Assumptions", "Scaling", "AI usage disclosure"}
    assert required <= found, sorted(required - found)


def test_existing_setup_and_agent_workflow_sections_are_preserved():
    kept = {
        "Project structure": ["├── src/wearable_pipeline/", "├── data/", "└── Dockerfile"],
        "Local setup": [
            "pyenv install 3.12.6",
            "uv sync --active --python 3.12.6 --locked --extra dev",
            "uv run --active --python 3.12.6 --locked python -m pytest -q",
            "aws s3 cp s3://de-tech-assessment-396587179375-us-east-1-an/data/ ./data/",
            "uv lock --python 3.12.6"],
        "Agent workflow": [
            "python scripts/orchestrate_increment.py", "--mode slice", "--kind environment",
            "--kind notebook", "AGENTS.md"],
    }
    for name, needles in kept.items():
        body = section(name)
        for needle in needles:
            assert needle in body, f"'{needle}' missing from '{name}'"


# --------------------------------------------------------------------------- run instructions


def test_readme_documents_the_verified_inc009_commands_exactly():
    found = commands()
    for command in (RUN_COMMAND, HOST_COMMAND, INCREMENTAL_COMMAND, TEST_COMMAND):
        assert command in found, command
    assert any(re.fullmatch(COMPARE_PATTERN, line) for line in found)


def test_readme_runs_no_unverified_container_command():
    for line in commands():
        line = re.sub(r"^(?:\w+=\S+ )+", "", line)
        assert not line.startswith(("docker run", "docker-compose", "docker compose up")), line
        if line.startswith("docker compose run"):
            assert any(re.fullmatch(p, line) for p in ALLOWED_RUN_PATTERNS), line


def test_run_section_explains_the_output_directory_requirement():
    body = section("Running the pipeline")
    assert "mkdir -p output" in body
    assert re.search(r"(?i)owned by", body)
    assert "incremental" in body and "compare" in body


# --------------------------------------------------------------------------- diagrams


def test_two_mermaid_diagrams_one_local_one_proposed_cloud():
    blocks = mermaid_blocks()
    assert len(blocks) == 2
    cloud = [b for b in blocks if any(has_term(b, t) for t in CLOUD_TERMS)]
    local = [b for b in blocks if b not in cloud]
    assert len(cloud) == 1 and len(local) == 1
    assert PROPOSED in cloud[0].lower()


def test_every_cloud_label_in_the_cloud_diagram_is_marked_proposed_not_implemented():
    cloud = next(b for b in mermaid_blocks() if any(has_term(b, t) for t in CLOUD_TERMS))
    labelled = [line for line in cloud.splitlines()
                if any(has_term(line, t) for t in CLOUD_TERMS)]
    assert labelled
    for line in labelled:
        assert PROPOSED in line.lower(), line


def test_local_diagram_shows_the_implemented_components_and_flows():
    local = next(b for b in mermaid_blocks() if not any(has_term(b, t) for t in CLOUD_TERMS))
    lowered = local.lower()
    for table in GOLD_TABLES + ("raw_manifest",):
        assert table in local, table
    for text in ("raw", "bronze", "silver", "quality flags", "exclusions", "run-failing checks",
                 "quality report", "full", "incremental", "run state", "read-only", "/data",
                 "/output", "daily clinical", "weekly biostatistics", "chronotype cohort"):
        assert text in lowered, text


def test_mapping_table_pairs_every_local_component_with_its_proposed_cloud_equivalent():
    rows = table_rows(prose(section("Architecture")))
    pairs = [
        (r"(?i)\braw\b", ["S3"]), (r"(?i)bronze", ["S3"]), (r"(?i)silver", ["S3"]),
        (r"(?i)gold", ["Iceberg"]), (r"(?i)container|compose|docker", ["AWS Batch"]),
        (r"(?i)catalog|\bsql\b", ["Glue Catalog", "Athena"]),
        (r"(?i)schedul|orchestrat", ["EventBridge", "Step Functions"]),
    ]
    for local, cloud in pairs:
        matching = [r for r in rows if re.search(local, r) and all(c in r for c in cloud)]
        assert matching, (local, cloud)
        assert all(PROPOSED in r.lower() for r in matching), (local, cloud)


def test_every_table_row_naming_a_cloud_component_says_it_is_proposed_or_an_alternative():
    for row in table_rows(prose(README)):
        if any(has_term(row, t) for t in CLOUD_TERMS):
            assert re.search(r"(?i)proposed|alternative|rejected", row), row


def test_architecture_has_a_legend_an_iceberg_justification_and_the_alternatives():
    body = section("Architecture")
    text = prose(body)
    assert re.search(r"(?i)legend", text)
    assert re.search(r"(?i)implemented", text) and PROPOSED in text.lower()
    reasons = [line for line in text.splitlines()
               if "Iceberg" in line and not line.lstrip().startswith("|")
               and re.search(r"(?i)\b(because|since|so that|allows?|supports?|enables?)\b", line)]
    assert reasons, "Iceberg needs a one-line justification"
    for term in ("ECS", "Parquet"):
        assert any(term in line and re.search(r"(?i)alternative", line)
                   for line in text.splitlines()), term


def test_readme_never_presents_cloud_components_as_built_or_tested():
    assert not re.search(
        r"(?i)\b(deployed|running|runs|tested|built) (on|to|in|against) (aws|s3|athena|glue)",
        README)
    assert not re.search(r"(?i)statistically significant|p-value", README)


# --------------------------------------------------------------------------- names exist in code


def test_readme_names_every_gold_table_and_the_bronze_manifest():
    tables, _ = known_tables_and_columns()
    for table in GOLD_TABLES + ("raw_manifest",):
        assert table in tables, f"{table} must exist in code"
        assert f"`{table}`" in README, table


def test_every_table_and_column_named_in_the_readme_exists_in_the_code():
    tables, _ = known_tables_and_columns()
    names = known_names()
    skipped = {"Project structure", "Local setup", "Agent workflow"}
    text = "\n".join(body for name, body in sections().items() if name not in skipped)
    spans = re.findall(r"`([^`\n]+)`", prose(text))
    checked = 0
    for span in spans:
        dotted = re.fullmatch(r"([a-z_]+)\.([a-z_0-9]+)", span)
        if dotted and re.match(r"(gold|silver|quality|raw)_", dotted.group(1)):
            table, column = dotted.groups()
            assert table in tables and column in tables[table], span
            checked += 1
        elif span == "gold_participant_devices":
            continue  # a client requirement the code does not define (spec 6, decision D8)
        elif re.fullmatch(r"(?:gold|silver|quality)_\w+|raw_manifest|run_window", span):
            assert span in tables, span
            checked += 1
        elif re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", span):
            assert span in names, f"`{span}` is not a table, column, module or script"
            checked += 1
    assert checked >= 6, "the README must actually name tables and columns"


def test_mermaid_blocks_name_only_existing_tables():
    tables, _ = known_tables_and_columns()
    for block in mermaid_blocks():
        for name in re.findall(r"\b(?:gold|silver)_[a-z0-9_]+\b|\braw_manifest\b", block):
            assert name in tables, name


def test_gold_participant_devices_is_recorded_as_a_requirement_the_code_does_not_produce():
    tables, _ = known_tables_and_columns()
    assert "gold_participant_devices" not in tables
    mentions = [p for p in re.split(r"\n\s*\n", README) if "gold_participant_devices" in p]
    assert mentions, "the client requirement must be recorded"
    for paragraph in mentions:
        assert re.search(r"(?i)not (produced|implemented|delivered)", paragraph), paragraph
        assert not re.search(r"(?i)delivered (in|by) INC-002", paragraph), paragraph


# --------------------------------------------------------------------------- query output


def test_query_output_section_has_the_command_run_id_and_captured_results():
    body = section("Analytical query output")
    assert QUERY_COMMAND in commands()
    assert re.search(r"run_id=[0-9a-f]{16}\b", body)
    assert any(line.startswith(("docker compose run --rm pipeline", "python -m wearable_pipeline run"))
               for line in (l.strip() for l in body.splitlines()))
    captured = "\n".join(b for lang, b in fenced_blocks(body) if lang != "mermaid")
    for name in ("chronotype_comparison.sql", "study_wide_metrics.sql"):
        assert f"# {name}" in captured, name


def test_captured_output_has_the_runner_format_and_the_real_column_names():
    captured = "\n".join(b for _, b in fenced_blocks(section("Analytical query output")))
    lines = [line.strip() for line in captured.splitlines()]

    def header(columns: tuple[str, ...]) -> str:
        return "| " + " | ".join(columns) + " |"

    for columns, labels in ((CHRONO_COLUMNS, ("A", "B", "A_minus_B")),
                            (STUDY_COLUMNS, ("steps_per_day", "sleep_total_min", "hr_mean_bpm"))):
        start = lines.index(header(columns))
        assert lines[start + 1] == "| " + " | ".join("---" for _ in columns) + " |"
        rows = []
        for line in lines[start + 2:]:
            if not line.startswith("|"):
                break
            rows.append(line)
        assert [r.split(" | ")[0].lstrip("| ") for r in rows] == list(labels)
        assert all(len(r.strip("|").split(" | ")) == len(columns) for r in rows)


def test_one_query_output_section_and_no_significance_claim():
    assert sum(1 for name in sections() if "query output" in name.lower()) == 1
    assert not re.search(r"(?i)statistically significant", section("Analytical query output"))


# --------------------------------------------------------------------------- decisions, assumptions, scale


def test_decisions_document_each_required_decision_with_rejected_alternatives():
    body = section("Decisions and trade-offs")
    for keyword in ("Spark", "DuckDB", "Iceberg", "Parquet", "Step Functions", "silver", "shared",
                    "quality", "run_id", "compare", "pinned"):
        assert keyword in body, keyword
    assert len(re.findall(r"(?i)rejected", body)) >= 6


def test_assumptions_are_labelled_observed_implemented_or_assumed():
    lines = section("Assumptions").splitlines()
    text = "\n".join(lines).lower()
    for label in ("observed", "implemented", "assumed"):
        assert label in text, label
    for keyword in ("A-02", "stuck", "coverage"):
        matching = [l for l in lines if keyword in l]
        assert matching, keyword
        assert all("assumed" in l.lower() for l in matching), keyword
    for keyword in ("timezone", "week", "quality handling", "incremental correction"):
        matching = [l for l in lines if keyword in l.lower()]
        assert matching, keyword
        assert all("implemented" in l.lower() for l in matching), keyword


def test_scaling_is_qualitative_and_uses_the_measured_inc009_numbers_only():
    body = section("Scaling")
    for number in ("4.9 GiB", "19.4 MB"):
        assert number in body, number
    assert re.search(r"\b7[01] ?s\b", body)
    assert not re.search(r"\b[TP]B\b|\d\s*(?:x|×)\s", body)


# --------------------------------------------------------------------------- AI disclosure


def test_ai_disclosure_states_only_what_the_repository_supports():
    body = section("AI usage disclosure")
    for fact in ("GitHub Copilot CLI", "scripts/orchestrate_increment.py", "interviewer", "planner",
                 "reviewer", "implementer", "ChatGPT", "design"):
        assert fact in body, fact
    assert re.search(r"(?i)not recorded", body)
    assert not re.search(
        r"(?i)\b(gpt-?\d[\w.-]*|claude|gemini|sonnet|opus|haiku|grok|llama|mistral)\b", README)


def test_ai_disclosure_states_human_oversight_controls_and_known_slips():
    body = section("AI usage disclosure")
    for fact in ("heredoc", "INC-006", "INC-007", "red/green", "adversarial", "commit"):
        assert fact in body, fact
    assert re.search(r"(?i)real[- ]data", body)
    assert re.search(r"(?i)human review", body)
    assert re.search(r"(?i)deviations?", body)


@pytest.mark.parametrize("path", ["queries/chronotype_comparison.sql", "queries/study_wide_metrics.sql"])
def test_readme_lists_each_query_file(path: str):
    assert path in README
