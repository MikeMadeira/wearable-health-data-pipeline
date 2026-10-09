#!/usr/bin/env python3
"""Run one data-pipeline increment or vertical-slicing proposal."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path


STATUS_PATTERN = re.compile(
    r"^Status:\s*(READY|BLOCKED|APPROVED|REJECTED)\s*$", re.MULTILINE
)
BLOCKER_PATTERN = re.compile(
    r"^Blocker-Type:\s*(IMPLEMENTATION|TESTS|SPECIFICATION|SLICING|ENVIRONMENT|TOOLING)\s*$",
    re.MULTILINE,
)
WORK_ITEM_PATTERN = re.compile(r"^(?:INC|INIT)-\d{3,}$")
BLOCKER_ROUTES = {
    "IMPLEMENTATION": "implementer",
    "TESTS": "planner",
    "SPECIFICATION": "planner",
    "SLICING": "slicing-planner",
}


class WorkflowError(RuntimeError):
    """Raised when an automated phase gate cannot be satisfied."""


def parse_report_status(report_path: Path) -> str:
    """Return the single protocol status from a report."""
    text = report_path.read_text(encoding="utf-8")
    matches = STATUS_PATTERN.findall(text)
    if len(matches) != 1:
        raise WorkflowError(f"Expected one valid Status line in {report_path}")
    return matches[0]


def parse_blocker_type(report_path: Path) -> str:
    """Read a single recognized blocker classification from a report."""
    text = report_path.read_text(encoding="utf-8")
    matches = BLOCKER_PATTERN.findall(text)
    if len(matches) != 1:
        raise WorkflowError(
            f"Expected exactly one recognized Blocker-Type in {report_path}"
        )
    return matches[0]


def responsible_agent(blocker_type: str) -> str:
    """Return the authorized repair role or stop for human intervention."""
    try:
        return BLOCKER_ROUTES[blocker_type]
    except KeyError as error:
        raise WorkflowError(
            f"Blocker type {blocker_type!r} is not auto-routable; human action required"
        ) from error


def require_retry_available(retries_used: int, max_retries: int) -> int:
    """Consume one retry, raising before the configured limit is exceeded."""
    if max_retries < 0:
        raise WorkflowError("--max-retries must be zero or greater")
    if retries_used >= max_retries:
        raise WorkflowError(f"Retry limit exhausted ({max_retries})")
    return retries_used + 1


def resolve_artifact_id(work_item: str, intent_path: str) -> str:
    """Use the intent filename as the durable artifact directory name."""
    artifact_id = Path(intent_path).stem
    if not artifact_id.startswith(f"{work_item}-"):
        raise WorkflowError(
            f"Intent filename {artifact_id!r} must start with {work_item}-"
        )
    return artifact_id


def changed_paths(status_output: str) -> list[str]:
    """Extract paths from Git porcelain status output."""
    paths: list[str] = []
    for line in status_output.splitlines():
        if len(line) >= 4:
            paths.append(line[3:])
    return paths


def should_resume_implementation(status_output: str, implementation_report: str) -> bool:
    """Detect a dirty implementation phase without treating planning output as execution."""
    return implementation_report in changed_paths(status_output)


def validate_phase_paths(paths: list[str], phase: str, work_item: str) -> None:
    """Reject edits outside the paths owned by the current phase."""
    report_prefix = f"reports/{work_item}/"
    allowed_prefixes = (
        ("reports/", "plans/")
        if phase == "slicing"
        else ("reports/", "specs/", "tests/", "src/")
    )

    for path in paths:
        if path.startswith("intents/"):
            raise WorkflowError(f"Phase attempted to modify a human intent: {path}")
        if phase == "planning" and path == "pyproject.toml":
            continue
        if not path.startswith(allowed_prefixes):
            raise WorkflowError(f"Phase modified an unauthorized path: {path}")
        if phase == "implementation" and not (
            path.startswith("src/") or path.startswith(report_prefix)
        ):
            raise WorkflowError(f"Implementation modified a planning path: {path}")
        if phase == "planning" and path.startswith("reports/") and not path.startswith(
            report_prefix
        ):
            raise WorkflowError(f"Planning modified another work item's report: {path}")
        if phase == "slicing" and not (
            path.startswith("plans/") or path.startswith(report_prefix)
        ):
            raise WorkflowError(f"Slicing modified a non-proposal path: {path}")
        if phase == "slicing" and path.startswith("reports/") and not path.startswith(
            report_prefix
        ):
            raise WorkflowError(f"Slicing modified another work item's report: {path}")


def run_command(
    command: list[str],
    repo_root: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return captured output without hiding failures."""
    env = {**os.environ, **extra_env} if extra_env else None
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
    except OSError as error:
        raise WorkflowError(f"Could not start command {command[0]!r}: {error}") from error
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result


def git(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a Git command in the repository."""
    return run_command(["git", *arguments], repo_root)


def require_success(result: subprocess.CompletedProcess[str], description: str) -> None:
    if result.returncode != 0:
        raise WorkflowError(f"{description} failed with exit code {result.returncode}")


def require_report(repo_root: Path, relative_path: str, expected_status: str) -> None:
    report_path = repo_root / relative_path
    if not report_path.is_file():
        raise WorkflowError(f"Required report does not exist: {relative_path}")
    actual_status = parse_report_status(report_path)
    if actual_status != expected_status:
        raise WorkflowError(
            f"{relative_path} has status {actual_status}; expected {expected_status}"
        )


def run_tests(repo_root: Path, python_executable: str, expected: str) -> str:
    """Run pytest and enforce either a genuine red or green result."""
    # tests/test_orchestrator.py imports the top-level `scripts` package, which
    # is only importable when the repo root is on PYTHONPATH.
    existing_path = os.environ.get("PYTHONPATH", "")
    repo_root_path = str(repo_root)
    python_path = (
        f"{repo_root_path}{os.pathsep}{existing_path}" if existing_path else repo_root_path
    )
    result = run_command(
        [python_executable, "-m", "pytest", "-q"],
        repo_root,
        extra_env={"PYTHONPATH": python_path},
    )
    output = f"{result.stdout}{result.stderr}"
    if expected == "red":
        if result.returncode == 0 or "NotImplementedError" not in output:
            raise WorkflowError(
                "Planning test gate was not a genuine NotImplementedError red baseline"
            )
    elif result.returncode != 0:
        raise WorkflowError("Implementation test gate failed")
    return output


def invoke_agent(
    repo_root: Path,
    copilot_command: str,
    agent: str,
    prompt: str,
) -> None:
    command = [
        copilot_command,
        "-C",
        str(repo_root),
        "--agent",
        agent,
        "--allow-all-tools",
        "--allow-all-paths",
        "-p",
        prompt,
    ]
    require_success(run_command(command, repo_root), f"Copilot {agent} phase")


def worktree_snapshot(repo_root: Path) -> dict[str, str | None]:
    """Hash currently changed files so a repair can be checked in isolation."""
    status = git(
        repo_root, "status", "--porcelain", "--untracked-files=all"
    ).stdout
    snapshot: dict[str, str | None] = {}
    for relative_path in changed_paths(status):
        file_path = repo_root / relative_path
        if file_path.is_file():
            snapshot[relative_path] = hashlib.sha256(file_path.read_bytes()).hexdigest()
        else:
            snapshot[relative_path] = None
    return snapshot


def changed_since_snapshot(
    repo_root: Path,
    before: dict[str, str | None],
) -> list[str]:
    """Return paths created or changed since a repair began."""
    after = worktree_snapshot(repo_root)
    return sorted(
        path
        for path, digest in after.items()
        if path not in before or before[path] != digest
    )


def validate_repair_paths(
    paths: list[str],
    blocker_type: str,
    artifact_id: str,
    phase: str,
) -> None:
    """Constrain a repair agent to artifacts owned by its blocker category."""
    report_prefix = f"reports/{artifact_id}/{phase}-recovery-"
    allowed = {
        "IMPLEMENTATION": ("src/",),
        "TESTS": ("tests/",),
        "SPECIFICATION": ("specs/", "tests/"),
        "SLICING": ("plans/",),
    }.get(blocker_type)
    if allowed is None:
        responsible_agent(blocker_type)
    for path in paths:
        if not path.startswith(allowed) and not path.startswith(report_prefix):
            raise WorkflowError(
                f"{blocker_type} repair modified an unauthorized path: {path}"
            )


def repair_blocker(
    repo_root: Path,
    copilot_command: str,
    artifact_id: str,
    blocker_type: str,
    source_report: str,
    phase: str,
    attempt: int,
) -> tuple[str, str]:
    """Route one classified blocker, review the repair, and commit plan fixes."""
    agent = responsible_agent(blocker_type)
    recovery_report = (
        f"reports/{artifact_id}/{phase}-recovery-{attempt:02d}.md"
    )
    recovery_review = (
        f"reports/{artifact_id}/{phase}-recovery-review-{attempt:02d}.md"
    )
    before = worktree_snapshot(repo_root)
    invoke_agent(
        repo_root,
        copilot_command,
        agent,
        f"Repair the {blocker_type} blocker documented in {source_report}. "
        f"Edit only the artifact(s) owned by this blocker. Do not implement an "
        f"unrelated feature or invoke other agents. Write {recovery_report} "
        "using the communication report format with Status: READY only if the "
        "specific correction is complete; otherwise use BLOCKED and classify it.",
    )
    require_report(repo_root, recovery_report, "READY")
    repair_paths = changed_since_snapshot(repo_root, before)
    validate_repair_paths(repair_paths, blocker_type, artifact_id, phase)
    if recovery_report not in repair_paths:
        raise WorkflowError(f"Repair agent did not create {recovery_report}")

    before_review = worktree_snapshot(repo_root)
    invoke_agent(
        repo_root,
        copilot_command,
        "reviewer",
        f"Independently review the {blocker_type} correction in {recovery_report}. "
        f"Check the changed artifact(s) against the intent and spec. Write "
        f"{recovery_review} using the communication report format. If rejected, "
        "include exactly one Blocker-Type.",
    )
    require_report(repo_root, recovery_review, "APPROVED")
    review_paths = changed_since_snapshot(repo_root, before_review)
    validate_repair_paths(review_paths, blocker_type, artifact_id, phase)

    if blocker_type in {"TESTS", "SPECIFICATION"}:
        all_repair_paths = sorted(set(repair_paths + review_paths))
        commit_repair_paths(repo_root, all_repair_paths, artifact_id, blocker_type)
    return recovery_report, recovery_review


def commit_repair_paths(
    repo_root: Path,
    paths: list[str],
    artifact_id: str,
    blocker_type: str,
) -> None:
    """Commit reviewed spec/test repairs without staging implementation changes."""
    if not paths:
        raise WorkflowError("Approved repair produced no changed files")
    require_success(git(repo_root, "add", "--", *paths), "Recovery staging")
    staged = git(repo_root, "diff", "--cached", "--name-only").stdout.splitlines()
    if sorted(staged) != sorted(paths):
        raise WorkflowError("Staged recovery paths differ from the reviewed repair paths")
    message = f"fix({artifact_id.split('-', 1)[0]}): repair {blocker_type.lower()} blocker"
    require_success(git(repo_root, "commit", "-m", message), "Recovery commit")


def write_orchestrator_report(
    repo_root: Path,
    artifact_id: str,
    filename: str,
    status: str,
    next_action: str,
    checks: list[str],
) -> str:
    relative_path = f"reports/{artifact_id}/{filename}"
    report_path = repo_root / relative_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    check_lines = "\n".join(f"- {check}" for check in checks)
    report_path.write_text(
        f"# Orchestrator Report: {artifact_id}\n\n"
        f"Status: {status}\n"
        "Inputs:\n"
        f"- intents/{artifact_id}.md\n\n"
        "Outputs:\n"
        f"- {relative_path}\n\n"
        "Checks:\n"
        f"{check_lines}\n\n"
        "Findings:\n"
        "- Automated phase routing completed without modifying unrelated paths.\n\n"
        "Next action:\n"
        f"{next_action}\n",
        encoding="utf-8",
    )
    return relative_path


def commit_phase(
    repo_root: Path,
    paths: list[str],
    phase: str,
    work_item: str,
    message: str,
) -> None:
    validate_phase_paths(paths, phase, work_item)
    unique_paths = sorted(set(paths))
    require_success(git(repo_root, "add", "--", *unique_paths), "Git staging")
    staged = git(repo_root, "diff", "--cached", "--name-only").stdout.splitlines()
    if sorted(staged) != unique_paths:
        raise WorkflowError("Staged paths differ from the validated phase paths")
    require_success(git(repo_root, "commit", "-m", message), "Git commit")


def run_slicing_workflow(args: argparse.Namespace) -> None:
    """Create and commit a reviewed vertical-slice proposal."""
    repo_root = Path(args.repo_root).resolve()
    work_item = args.increment
    if not WORK_ITEM_PATTERN.fullmatch(work_item) or not work_item.startswith("INIT-"):
        raise WorkflowError("Slicing work items must use the form INIT-001")

    intent_path = repo_root / args.intent
    if not intent_path.is_file():
        raise WorkflowError(f"Intent file does not exist: {args.intent}")
    artifact_id = resolve_artifact_id(work_item, args.intent)
    status_output = git(
        repo_root, "status", "--porcelain", "--untracked-files=all"
    ).stdout
    resume_slicing = bool(status_output)
    if resume_slicing:
        pending_paths = changed_paths(status_output)
        validate_phase_paths(pending_paths, "slicing", artifact_id)
        planner_path = repo_root / "reports" / artifact_id / "slicing-planner.md"
        if not planner_path.is_file():
            raise WorkflowError(
                "Working tree is not clean and does not contain a resumable slicing report"
            )
        require_report(repo_root, f"reports/{artifact_id}/slicing-planner.md", "READY")
        print(f"Resuming slicing phase from reports/{artifact_id}/slicing-planner.md")

    planner_report = f"reports/{artifact_id}/slicing-planner.md"
    review_report = f"reports/{artifact_id}/slicing-review.md"
    proposal_path = f"plans/{artifact_id}/vertical-slices.md"

    if not resume_slicing:
        invoke_agent(
            repo_root,
            args.copilot,
            "slicing-planner",
            f"Execute the slicing phase for {args.intent}. Create {proposal_path} and {planner_report}. Do not create specs, tests, stubs, source code, or commits.",
        )
    slicing_retries = 0
    while True:
        if not (repo_root / planner_report).is_file():
            raise WorkflowError(f"Required report does not exist: {planner_report}")
        planner_status = parse_report_status(repo_root / planner_report)
        if planner_status == "READY":
            break
        if planner_status != "BLOCKED":
            raise WorkflowError(
                f"{planner_report} has status {planner_status}; expected READY or BLOCKED"
            )
        blocker_type = parse_blocker_type(repo_root / planner_report)
        if blocker_type != "SLICING":
            responsible_agent(blocker_type)
            raise WorkflowError(f"Slicing blocker {blocker_type} is not auto-routable here")
        slicing_retries = require_retry_available(slicing_retries, args.max_retries)
        recovery_report, _ = repair_blocker(
            repo_root,
            args.copilot,
            artifact_id,
            blocker_type,
            planner_report,
            "slicing",
            slicing_retries,
        )
        invoke_agent(
            repo_root,
            args.copilot,
            "slicing-planner",
            f"Resume the slicing proposal using the approved correction in {recovery_report}. "
            f"Complete {proposal_path} and {planner_report}; do not create implementation artifacts.",
        )

    needs_slicing_review = True
    while True:
        if needs_slicing_review:
            invoke_agent(
                repo_root,
                args.copilot,
                "slicing-reviewer",
                f"Review the vertical-slice proposal for {work_item}. Read {args.intent}, {proposal_path}, and {planner_report}. Write {review_report} using the communication protocol.",
            )
        review_status = parse_report_status(repo_root / review_report)
        if review_status == "APPROVED":
            break
        if review_status != "REJECTED":
            raise WorkflowError(
                f"{review_report} has status {review_status}; expected APPROVED or REJECTED"
            )
        blocker_type = parse_blocker_type(repo_root / review_report)
        if blocker_type != "SLICING":
            responsible_agent(blocker_type)
            raise WorkflowError(f"Slicing blocker {blocker_type} is not auto-routable here")
        slicing_retries = require_retry_available(slicing_retries, args.max_retries)
        recovery_report, _ = repair_blocker(
            repo_root,
            args.copilot,
            artifact_id,
            blocker_type,
            review_report,
            "slicing",
            slicing_retries,
        )
        require_report(repo_root, recovery_report, "READY")
        needs_slicing_review = True

    slicing_paths = changed_paths(
        git(repo_root, "status", "--porcelain", "--untracked-files=all").stdout
    )
    orchestrator_report = write_orchestrator_report(
        repo_root,
        artifact_id,
        "orchestrator-slicing.md",
        "APPROVED",
        "Slicing gate passed; review the proposal commit and select the next increment intent.",
        ["slicing-planner.md: READY", "slicing-review.md: APPROVED"],
    )
    if orchestrator_report not in slicing_paths:
        slicing_paths.append(orchestrator_report)
    commit_phase(
        repo_root,
        slicing_paths,
        "slicing",
        artifact_id,
        f"plan({work_item}): propose vertical slices",
    )
    if git(repo_root, "status", "--porcelain", "--untracked-files=all").stdout:
        raise WorkflowError("Working tree is not clean after the slicing commit")


def run_implementation_phase(
    args: argparse.Namespace,
    repo_root: Path,
    work_item: str,
    artifact_id: str,
    implementation_report: str,
    implementation_review: str,
) -> None:
    """Run bounded implementation/review recovery and commit the green result."""
    implementation_retries = 0
    recovery_report: str | None = None
    while True:
        implementation_prompt = (
            f"Execute or resume the implementation phase for {work_item} against the generated spec and tests. "
            f"Read any existing {implementation_report}, write its updated result, and do not modify specs or tests."
        )
        if recovery_report:
            implementation_prompt += (
                f" Resume using the approved correction in {recovery_report}."
            )
        invoke_agent(
            repo_root,
            args.copilot,
            "implementer",
            implementation_prompt,
        )
        implementation_status = parse_report_status(repo_root / implementation_report)
        if implementation_status == "BLOCKED":
            blocker_type = parse_blocker_type(repo_root / implementation_report)
        elif implementation_status != "READY":
            raise WorkflowError(
                f"{implementation_report} has status {implementation_status}; expected READY or BLOCKED"
            )
        else:
            blocker_type = ""

        if implementation_status == "BLOCKED":
            implementation_retries = require_retry_available(
                implementation_retries, args.max_retries
            )
            recovery_report, _ = repair_blocker(
                repo_root,
                args.copilot,
                artifact_id,
                blocker_type,
                implementation_report,
                "implementation",
                implementation_retries,
            )
            continue

        invoke_agent(
            repo_root,
            args.copilot,
            "reviewer",
            f"Review the implementation phase for {work_item}. Read the spec, tests, implementation, and {implementation_report}. Write {implementation_review} and use the communication protocol.",
        )
        implementation_review_status = parse_report_status(
            repo_root / implementation_review
        )
        if implementation_review_status == "APPROVED":
            break
        if implementation_review_status != "REJECTED":
            raise WorkflowError(
                f"{implementation_review} has status {implementation_review_status}; expected APPROVED or REJECTED"
            )
        blocker_type = parse_blocker_type(repo_root / implementation_review)
        implementation_retries = require_retry_available(
            implementation_retries, args.max_retries
        )
        recovery_report, _ = repair_blocker(
            repo_root,
            args.copilot,
            artifact_id,
            blocker_type,
            implementation_review,
            "implementation",
            implementation_retries,
        )

    run_tests(repo_root, args.python, "green")

    implementation_paths = changed_paths(
        git(repo_root, "status", "--porcelain", "--untracked-files=all").stdout
    )
    execution_report = write_orchestrator_report(
        repo_root,
        artifact_id,
        "orchestrator-execution.md",
        "APPROVED",
        "Implementation gate passed; review the commit and reports.",
        ["implementation.md: READY", "implementation-review.md: APPROVED", "pytest green baseline: PASS"],
    )
    if execution_report not in implementation_paths:
        implementation_paths.append(execution_report)
    commit_phase(
        repo_root,
        implementation_paths,
        "implementation",
        artifact_id,
        f"feat({work_item}): implement increment against spec",
    )
    if git(repo_root, "status", "--porcelain", "--untracked-files=all").stdout:
        raise WorkflowError("Working tree is not clean after the final commit")


def run_increment_workflow(args: argparse.Namespace) -> None:
    """Run the existing spec, implementation, and review workflow."""
    repo_root = Path(args.repo_root).resolve()
    work_item = args.increment
    if not WORK_ITEM_PATTERN.fullmatch(work_item) or not work_item.startswith("INC-"):
        raise WorkflowError("Increment work items must use the form INC-005")

    intent_path = repo_root / args.intent
    if not intent_path.is_file():
        raise WorkflowError(f"Intent file does not exist: {args.intent}")
    artifact_id = resolve_artifact_id(work_item, args.intent)
    status_output = git(
        repo_root, "status", "--porcelain", "--untracked-files=all"
    ).stdout
    resume_planning = bool(status_output)
    implementation_report = f"reports/{artifact_id}/implementation.md"
    resume_implementation = bool(status_output) and should_resume_implementation(
        status_output, implementation_report
    )
    if resume_implementation:
        pending_paths = changed_paths(status_output)
        validate_phase_paths(pending_paths, "implementation", artifact_id)
        commit_subjects = git(
            repo_root, "log", "--format=%s", "--all"
        ).stdout.splitlines()
        if not any(subject.startswith(f"spec({work_item}):") for subject in commit_subjects):
            raise WorkflowError(
                "Cannot resume implementation: the planning baseline commit is missing"
            )
        print(f"Resuming implementation phase from {implementation_report}")
    elif resume_planning:
        pending_paths = changed_paths(status_output)
        validate_phase_paths(pending_paths, "planning", artifact_id)
        planner_path = repo_root / "reports" / artifact_id / "planner.md"
        if not planner_path.is_file():
            raise WorkflowError(
                "Working tree is not clean and does not contain a resumable planner report"
            )
        require_report(repo_root, f"reports/{artifact_id}/planner.md", "READY")
        print(f"Resuming planning phase from reports/{artifact_id}/planner.md")

    planner_report = f"reports/{artifact_id}/planner.md"
    planning_review = f"reports/{artifact_id}/planning-review.md"
    implementation_review = f"reports/{artifact_id}/implementation-review.md"

    if resume_implementation:
        run_implementation_phase(
            args,
            repo_root,
            work_item,
            artifact_id,
            implementation_report,
            implementation_review,
        )
        return

    planning_retries = 0
    if not resume_planning:
        invoke_agent(
            repo_root,
            args.copilot,
            "planner",
            f"Execute the planning phase for {args.intent}. Create the spec, red tests, stub, and {planner_report}. Do not implement production logic.",
        )
    while True:
        if not (repo_root / planner_report).is_file():
            raise WorkflowError(f"Required report does not exist: {planner_report}")
        planner_status = parse_report_status(repo_root / planner_report)
        if planner_status == "READY":
            break
        if planner_status != "BLOCKED":
            raise WorkflowError(
                f"{planner_report} has status {planner_status}; expected READY or BLOCKED"
            )
        blocker_type = parse_blocker_type(repo_root / planner_report)
        if blocker_type not in {"TESTS", "SPECIFICATION"}:
            raise WorkflowError(
                f"Planning blocker {blocker_type} cannot be auto-routed"
            )
        planning_retries = require_retry_available(
            planning_retries, args.max_retries
        )
        recovery_report, _ = repair_blocker(
            repo_root,
            args.copilot,
            artifact_id,
            blocker_type,
            planner_report,
            "planning",
            planning_retries,
        )
        invoke_agent(
            repo_root,
            args.copilot,
            "planner",
            f"Resume planning using the approved correction in {recovery_report}. "
            f"Complete the spec, red tests, stub, and {planner_report}; do not implement.",
        )

    needs_planning_review = True
    while True:
        if needs_planning_review:
            invoke_agent(
                repo_root,
                args.copilot,
                "reviewer",
                f"Review the planning phase for {work_item}. Read the intent, spec, tests, and {planner_report}. Write {planning_review} and use the communication protocol.",
            )
        review_status = parse_report_status(repo_root / planning_review)
        if review_status == "APPROVED":
            break
        if review_status != "REJECTED":
            raise WorkflowError(
                f"{planning_review} has status {review_status}; expected APPROVED or REJECTED"
            )
        blocker_type = parse_blocker_type(repo_root / planning_review)
        if blocker_type not in {"TESTS", "SPECIFICATION"}:
            raise WorkflowError(
                f"Planning blocker {blocker_type} cannot be auto-routed before implementation"
            )
        planning_retries = require_retry_available(
            planning_retries, args.max_retries
        )
        recovery_report, _ = repair_blocker(
            repo_root,
            args.copilot,
            artifact_id,
            blocker_type,
            planning_review,
            "planning",
            planning_retries,
        )
        require_report(repo_root, recovery_report, "READY")
        needs_planning_review = True
    run_tests(repo_root, args.python, "red")

    planning_paths = changed_paths(
        git(repo_root, "status", "--porcelain", "--untracked-files=all").stdout
    )
    plan_report = write_orchestrator_report(
        repo_root,
        artifact_id,
        "orchestrator-planning.md",
        "APPROVED",
        "Planning gate passed; the planning commit was created and implementation may begin.",
        ["planner.md: READY", "planning-review.md: APPROVED", "pytest red baseline: PASS"],
    )
    if plan_report not in planning_paths:
        planning_paths.append(plan_report)
    commit_phase(
        repo_root,
        planning_paths,
        "planning",
        artifact_id,
        f"spec({work_item}): add generated spec and red test baseline",
    )

    run_implementation_phase(
        args,
        repo_root,
        work_item,
        artifact_id,
        implementation_report,
        implementation_review,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("increment", "slice"),
        default="increment",
        help="Run an implementation increment or propose vertical slices",
    )
    parser.add_argument(
        "--increment",
        required=True,
        help="Work item ID, for example INC-005 or INIT-001",
    )
    parser.add_argument("--intent", required=True, help="Intent path relative to the repository")
    parser.add_argument("--repo-root", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--copilot", default="copilot", help="Copilot CLI executable")
    parser.add_argument("--python", default=sys.executable, help="Python executable used to run pytest")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Maximum blocker-repair retries per phase (default: 2; 0 disables retries)",
    )
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        if args.max_retries < 0:
            raise WorkflowError("--max-retries must be zero or greater")
        if args.mode == "slice":
            run_slicing_workflow(args)
        else:
            run_increment_workflow(args)
    except WorkflowError as error:
        print(f"orchestrate_increment: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
