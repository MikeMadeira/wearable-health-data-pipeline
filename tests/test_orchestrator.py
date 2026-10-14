import json
from pathlib import Path

import pytest

from scripts.orchestrate_increment import (
    WorkflowError,
    build_parser,
    parse_blocker_type,
    parse_report_status,
    require_retry_available,
    responsible_agent,
    resolve_artifact_id,
    run_notebooks,
    should_resume_implementation,
    run_tests,
    validate_notebook_document,
    validate_repair_paths,
    validate_phase_paths,
)


def test_parse_report_status_reads_protocol_status(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("# Report\n\nStatus: APPROVED\n", encoding="utf-8")

    assert parse_report_status(report) == "APPROVED"


def test_parse_report_status_rejects_missing_status(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("# Report\n", encoding="utf-8")

    with pytest.raises(WorkflowError, match="Status line"):
        parse_report_status(report)


def test_parse_blocker_type_reads_classification(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Status: BLOCKED\nBlocker-Type: TESTS\n", encoding="utf-8")

    assert parse_blocker_type(report) == "TESTS"


def test_parse_blocker_type_rejects_missing_or_unknown_classification(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.md"
    report.write_text("Status: BLOCKED\nBlocker-Type: UNKNOWN\n", encoding="utf-8")

    with pytest.raises(WorkflowError, match="Blocker-Type"):
        parse_blocker_type(report)


def test_responsible_agent_routes_only_auto_repairable_blockers() -> None:
    assert responsible_agent("IMPLEMENTATION") == "implementer"
    assert responsible_agent("TESTS") == "planner"
    assert responsible_agent("SPECIFICATION") == "planner"
    assert responsible_agent("SLICING") == "slicing-planner"

    with pytest.raises(WorkflowError, match="human action required"):
        responsible_agent("ENVIRONMENT")


def test_retry_budget_stops_at_maximum() -> None:
    assert require_retry_available(0, 2) == 1
    assert require_retry_available(1, 2) == 2

    with pytest.raises(WorkflowError, match="Retry limit exhausted"):
        require_retry_available(2, 2)

    with pytest.raises(WorkflowError, match="Retry limit exhausted"):
        require_retry_available(0, 0)


def test_should_resume_implementation_only_when_implementation_report_is_dirty() -> None:
    assert should_resume_implementation(
        " M src/data_pipeline/validation.py\n?? reports/INC-010/implementation.md\n",
        "reports/INC-010/implementation.md",
    )
    assert not should_resume_implementation(
        "?? reports/INC-010/planner.md\n",
        "reports/INC-010/implementation.md",
    )


def test_validate_repair_paths_limits_test_repairs() -> None:
    validate_repair_paths(
        [
            "tests/test_validation.py",
            "reports/INC-010-example/planning-recovery-01.md",
        ],
        "TESTS",
        "INC-010-example",
        "planning",
    )

    with pytest.raises(WorkflowError, match="unauthorized"):
        validate_repair_paths(
            ["src/data_pipeline/validation.py"],
            "TESTS",
            "INC-010-example",
            "planning",
        )

    with pytest.raises(WorkflowError, match="unauthorized"):
        validate_repair_paths(
            ["reports/INC-010-example/implementation.md"],
            "TESTS",
            "INC-010-example",
            "implementation",
        )


def test_resolve_artifact_id_uses_intent_slug() -> None:
    assert (
        resolve_artifact_id(
            "INC-005",
            "intents/INC-005-run-city-metrics-pipeline.md",
        )
        == "INC-005-run-city-metrics-pipeline"
    )


def test_resolve_artifact_id_rejects_mismatched_intent() -> None:
    with pytest.raises(WorkflowError, match="must start"):
        resolve_artifact_id("INC-005", "intents/INC-004-other.md")


def test_resolve_artifact_id_supports_initiative_intents() -> None:
    assert (
        resolve_artifact_id(
            "INIT-001",
            "intents/INIT-001-customer-data-quality.md",
        )
        == "INIT-001-customer-data-quality"
    )


def test_validate_phase_paths_allows_planning_outputs() -> None:
    validate_phase_paths(
        [
            "specs/INC-005-example.md",
            "tests/test_example.py",
            "src/data_pipeline/io.py",
            "reports/INC-005/planner.md",
        ],
        "planning",
        "INC-005",
    )


def test_validate_phase_paths_allows_packaging_config_during_planning() -> None:
    validate_phase_paths(["pyproject.toml"], "planning", "INC-007")


def test_validate_phase_paths_rejects_packaging_config_during_implementation() -> None:
    with pytest.raises(WorkflowError, match="unauthorized"):
        validate_phase_paths(["pyproject.toml"], "implementation", "INC-007")


def test_validate_phase_paths_rejects_implementation_test_changes() -> None:
    with pytest.raises(WorkflowError, match="planning path"):
        validate_phase_paths(
            ["tests/test_example.py"],
            "implementation",
            "INC-005",
        )


def test_validate_phase_paths_rejects_unrelated_paths() -> None:
    with pytest.raises(WorkflowError, match="unauthorized"):
        validate_phase_paths(["README.md"], "planning", "INC-005")


def test_validate_phase_paths_rejects_source_changes_during_slicing() -> None:
    with pytest.raises(WorkflowError, match="unauthorized"):
        validate_phase_paths(
            ["src/data_pipeline/validation.py"],
            "slicing",
            "INIT-001-customer-data-quality",
        )


def test_environment_increment_allows_setup_files_and_smoke_test_planning() -> None:
    validate_phase_paths(
        ["specs/INC-000-environment.md", "tests/test_environment_smoke.py"],
        "planning",
        "INC-000-environment-setup",
        "environment",
    )
    validate_phase_paths(
        [
            "Dockerfile",
            "pyproject.toml",
            "uv.lock",
            "reports/INC-000-environment-setup/implementation.md",
        ],
        "implementation",
        "INC-000-environment-setup",
        "environment",
    )
    with pytest.raises(WorkflowError, match="planning path"):
        validate_phase_paths(
            ["tests/test_environment_smoke.py"],
            "implementation",
            "INC-000-environment-setup",
            "environment",
        )
    with pytest.raises(WorkflowError, match="another work item's report"):
        validate_phase_paths(
            ["reports/INC-002-other/implementation.md"],
            "implementation",
            "INC-000-environment-setup",
            "environment",
        )


def test_notebook_increment_limits_planning_and_implementation_paths() -> None:
    validate_phase_paths(
        ["reports/INC-001-participants-profiling/planner.md"],
        "planning",
        "INC-001-participants-profiling",
        "notebook",
    )
    validate_phase_paths(
        [
            "notebooks/profiling/participants.ipynb",
            "reports/INC-001-participants-profiling/implementation.md",
        ],
        "implementation",
        "INC-001-participants-profiling",
        "notebook",
    )
    with pytest.raises(WorkflowError, match="planning path"):
        validate_phase_paths(
            ["tests/test_participants.py"],
            "implementation",
            "INC-001-participants-profiling",
            "notebook",
        )


def test_environment_red_gate_accepts_only_missing_dependency_smoke_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from scripts import orchestrate_increment

    monkeypatch.setattr(
        orchestrate_increment,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["pytest"],
            returncode=1,
            stdout=(
                "tests/test_environment_smoke.py::test_environment_smoke FAILED\n"
                "=========================== 1 failed, 23 passed ===========================\n"
            ),
            stderr="ModuleNotFoundError: No module named 'duckdb'",
        ),
    )
    assert "No module named 'duckdb'" in run_tests(tmp_path, "python", "environment-red")


def test_environment_red_gate_accepts_quiet_pytest_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from scripts import orchestrate_increment

    monkeypatch.setattr(
        orchestrate_increment,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["pytest"],
            returncode=1,
            stdout=(
                "FAILED tests/test_container.py::test_environment_smoke - "
                "ModuleNotFoundError: No module named 'yaml'\n"
                "1 failed, 937 passed, 37 skipped in 300.12s (0:05:00)\n"
            ),
            stderr="",
        ),
    )
    assert "No module named 'yaml'" in run_tests(tmp_path, "python", "environment-red")


def test_environment_red_gate_rejects_unrelated_pytest_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from scripts import orchestrate_increment

    monkeypatch.setattr(
        orchestrate_increment,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["pytest"],
            returncode=1,
            stdout="2 failed, 22 passed",
            stderr="No module named 'duckdb'",
        ),
    )
    with pytest.raises(WorkflowError, match="only test_environment_smoke"):
        run_tests(tmp_path, "python", "environment-red")


def test_notebook_document_requires_cell_language_and_unique_ids() -> None:
    notebook = {
        "cells": [
            {"cell_type": "markdown", "metadata": {"language": "markdown"}, "source": "# Profile"},
            {"cell_type": "code", "metadata": {"language": "python", "id": "cell-a"}, "source": ["1 + 1\n"]},
        ]
    }
    assert len(validate_notebook_document(notebook, "sample.ipynb")) == 2

    notebook["cells"][1]["metadata"]["language"] = "markdown"
    with pytest.raises(WorkflowError, match="metadata.language"):
        validate_notebook_document(notebook, "sample.ipynb")


def test_notebook_document_rejects_duplicate_cell_ids() -> None:
    notebook = {
        "cells": [
            {"cell_type": "markdown", "metadata": {"language": "markdown", "id": "same"}, "source": "one"},
            {"cell_type": "code", "metadata": {"language": "python", "id": "same"}, "source": "pass"},
        ]
    }
    with pytest.raises(WorkflowError, match="duplicate metadata.id"):
        validate_notebook_document(notebook, "sample.ipynb")


@pytest.mark.parametrize("path", [".dockerignore", "docker-compose.yml"])
def test_environment_implementation_allows_container_files(path: str) -> None:
    validate_phase_paths(
        [path, "reports/INC-009/implementer.md"], "implementation", "INC-009", "environment"
    )
    validate_repair_paths(
        [path], "IMPLEMENTATION", "INC-009", "implementation", "environment"
    )


@pytest.mark.parametrize("path", ["README.md", "queries/chronotype.sql"])
def test_code_implementation_allows_readme_and_queries(path: str) -> None:
    validate_phase_paths(
        [path, "reports/INC-010/implementer.md"], "implementation", "INC-010", "code"
    )
    validate_repair_paths(
        [path], "IMPLEMENTATION", "INC-010", "implementation", "code"
    )


@pytest.mark.parametrize(
    "path", ["README.md", "queries/q.sql", ".dockerignore", "docker-compose.yml", "Dockerfile"]
)
def test_new_paths_stay_rejected_where_not_granted(path: str) -> None:
    with pytest.raises(WorkflowError):
        validate_phase_paths([path], "planning", "INC-010", "code")
    with pytest.raises(WorkflowError):
        validate_phase_paths([path], "slicing", "INC-010", "code")
    if path in {"README.md", "queries/q.sql"}:
        with pytest.raises(WorkflowError):
            validate_phase_paths([path], "implementation", "INC-009", "environment")
        with pytest.raises(WorkflowError):
            validate_phase_paths([path], "implementation", "INC-007", "notebook")
        with pytest.raises(WorkflowError):
            validate_repair_paths([path], "IMPLEMENTATION", "INC-009", "implementation", "environment")
        with pytest.raises(WorkflowError):
            validate_repair_paths([path], "TESTS", "INC-010", "implementation", "code")
    else:
        with pytest.raises(WorkflowError):
            validate_phase_paths([path], "implementation", "INC-010", "code")
        with pytest.raises(WorkflowError):
            validate_repair_paths([path], "IMPLEMENTATION", "INC-010", "implementation", "code")


def test_prefix_lookalikes_of_new_paths_are_rejected() -> None:
    for path in ["README.md.bak", "README.mdx", "queries-old/q.sql", "docker-compose.yml.bak"]:
        with pytest.raises(WorkflowError):
            validate_phase_paths([path], "implementation", "INC-010", "code")
    with pytest.raises(WorkflowError):
        validate_phase_paths(["docker-compose.yml.bak"], "implementation", "INC-009", "environment")
    with pytest.raises(WorkflowError):
        validate_repair_paths(["README.md.bak"], "IMPLEMENTATION", "INC-010", "implementation", "code")


def test_repair_paths_follow_increment_kind() -> None:
    validate_repair_paths(
        ["Dockerfile", "reports/INC-000-environment-setup/implementation-recovery-01.md"],
        "IMPLEMENTATION",
        "INC-000-environment-setup",
        "implementation",
        "environment",
    )
    validate_repair_paths(
        ["notebooks/profiling/participants.ipynb", "reports/INC-001-participants-profiling/implementation-recovery-01.md"],
        "IMPLEMENTATION",
        "INC-001-participants-profiling",
        "implementation",
        "notebook",
    )


@pytest.mark.parametrize("kind", ["code", "environment", "notebook"])
def test_parser_accepts_increment_kind(kind: str) -> None:
    args = build_parser().parse_args(
        ["--kind", kind, "--increment", "INC-001", "--intent", "intents/INC-001-test.md"]
    )
    assert args.kind == kind


def test_run_notebooks_executes_python_cells_and_preserves_raw_data(
    tmp_path: Path,
) -> None:
    notebook_path = tmp_path / "notebooks" / "profiling" / "participants.ipynb"
    notebook_path.parent.mkdir(parents=True)
    notebook_path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "markdown",
                        "metadata": {"language": "markdown"},
                        "source": "# Smoke test",
                    },
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {"language": "python"},
                        "outputs": [],
                        "source": ["assert 1 + 1 == 2\n"],
                    },
                ],
                "metadata": {
                    "kernelspec": {
                        "display_name": "Python (wearable-health-data-pipeline)",
                        "language": "python",
                        "name": "wearable-health-data-pipeline",
                    }
                },
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    raw_data = tmp_path / "data" / "source.csv"
    raw_data.parent.mkdir()
    raw_data.write_text("id\n1\n", encoding="utf-8")
    original_notebook = notebook_path.read_text(encoding="utf-8")

    checks = run_notebooks(tmp_path, ["notebooks/profiling/participants.ipynb"])

    assert checks == [
        "notebooks/profiling/participants.ipynb: JSON and Python cells executed; data/ unchanged"
    ]
    assert raw_data.read_text(encoding="utf-8") == "id\n1\n"
    assert notebook_path.read_text(encoding="utf-8") == original_notebook


def test_run_notebooks_rejects_raw_data_mutation(tmp_path: Path) -> None:
    notebook_path = tmp_path / "notebooks" / "profiling" / "mutating.ipynb"
    notebook_path.parent.mkdir(parents=True)
    notebook_path.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {"language": "python"},
                        "outputs": [],
                        "source": [
                            "from pathlib import Path\n",
                            "Path('data/source.csv').write_text('changed')\n",
                        ],
                    }
                ],
                "metadata": {
                    "kernelspec": {
                        "display_name": "Python (wearable-health-data-pipeline)",
                        "language": "python",
                        "name": "wearable-health-data-pipeline",
                    }
                },
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )
    raw_data = tmp_path / "data" / "source.csv"
    raw_data.parent.mkdir()
    raw_data.write_text("original", encoding="utf-8")

    with pytest.raises(WorkflowError, match="modified files under data/"):
        run_notebooks(tmp_path, ["notebooks/profiling/mutating.ipynb"])