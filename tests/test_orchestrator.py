from pathlib import Path

import pytest

from scripts.orchestrate_increment import (
    WorkflowError,
    parse_blocker_type,
    parse_report_status,
    require_retry_available,
    responsible_agent,
    resolve_artifact_id,
    should_resume_implementation,
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