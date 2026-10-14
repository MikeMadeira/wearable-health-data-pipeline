"""INC-009: containerized end-to-end run (kind: environment).

Layers (see specs/INC-009.md section 9):

* ``test_environment_smoke``: prerequisite smoke test. It needs a PyYAML parser to read
  ``docker-compose.yml``; PyYAML is declared by the spec as a new *dev* dependency and is
  absent until the implementation phase adds it, so the planning baseline fails with
  ``ModuleNotFoundError``. Once installed it also asserts the environment files exist.
* Static contract tests of ``Dockerfile``, ``.dockerignore``, ``docker-compose.yml`` and
  ``pyproject.toml``. They skip until the compose file exists and PyYAML is importable
  (the smoke test fails, not skips, if either is still missing after implementation).
* CLI behavior tests that pin the existing fail-fast behavior the container relies on.
  They run today and must stay green (no ``src/`` change is planned).
* Opt-in Docker integration tests (``WEARABLE_DOCKER_TESTS=1``, Docker on the host). They
  use synthetic fixtures only, never ``data/``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.test_walking_skeleton import make_data
from wearable_pipeline import main

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "pipeline"
DEFAULT_CMD = ["python", "-m", "wearable_pipeline", "run",
               "--data-dir", "/data", "--output-dir", "/output"]


# --------------------------------------------------------------------------- helpers


def _dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    """Return (INSTRUCTION, arguments) pairs with continuations joined, comments removed."""
    lines: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        lines.append(buffer + stripped)
        buffer = ""
    if buffer:
        lines.append(buffer.strip())
    pairs = []
    for line in lines:
        keyword, _, rest = line.partition(" ")
        pairs.append((keyword.upper(), rest.strip()))
    return pairs


def _image_references(instructions: list[tuple[str, str]]) -> list[str]:
    refs = []
    stages = set()
    for keyword, rest in instructions:
        if keyword == "FROM":
            tokens = [t for t in rest.split() if not t.startswith("--")]
            refs.append(tokens[0])
            if len(tokens) >= 3 and tokens[1].upper() == "AS":
                stages.add(tokens[2])
        elif keyword == "COPY":
            match = re.search(r"--from=(\S+)", rest)
            if match and match.group(1) not in stages:
                refs.append(match.group(1))
    return [r for r in refs if r not in stages and r != "scratch"]


@pytest.fixture(scope="module")
def env() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML (declared dev dependency) not installed yet")
    compose_path = ROOT / "docker-compose.yml"
    if not compose_path.is_file():
        pytest.skip("docker-compose.yml not created yet (implementation phase)")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    return {
        "compose": yaml.safe_load(compose_path.read_text(encoding="utf-8")),
        "dockerfile": dockerfile,
        "instructions": _dockerfile_instructions(dockerfile),
        "dockerignore": [
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ],
        "pyproject": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")),
    }


def _service(env: dict) -> dict:
    return env["compose"]["services"][SERVICE]


# ----------------------------------------------------------------------- smoke test


def test_environment_smoke() -> None:
    """Prerequisites of the containerized run are installed and present."""
    import yaml  # declared dev dependency (spec section 8); absent at planning time

    for name in ("Dockerfile", ".dockerignore", "docker-compose.yml", "uv.lock", "pyproject.toml"):
        assert (ROOT / name).is_file(), f"{name} is missing"
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert SERVICE in compose["services"]
    tooling = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = tooling["project"]["optional-dependencies"]["dev"]
    assert any(re.match(r"(?i)pyyaml\b", item) for item in dev)


# ------------------------------------------------------------- static: compose service


def test_compose_defines_one_service_built_from_the_repo(env):
    assert list(env["compose"]["services"]) == [SERVICE]
    build = _service(env)["build"]
    context = build if isinstance(build, str) else build.get("context")
    assert context in (".", "./")


def test_compose_mounts_data_read_only_and_output_read_write(env):
    volumes = _service(env)["volumes"]
    assert all(isinstance(v, str) for v in volumes), "short syntax only (spec section 4)"
    assert sorted(volumes) == ["./data:/data:ro", "./output:/output"]


def test_compose_declares_no_named_volumes_or_resource_limits(env):
    assert "volumes" not in env["compose"]
    service = _service(env)
    for key in ("mem_limit", "memswap_limit", "cpus", "cpu_count", "deploy"):
        assert key not in service


def test_compose_does_not_override_the_image_command_or_entrypoint(env):
    service = _service(env)
    assert "command" not in service
    assert "entrypoint" not in service


def test_compose_runs_as_the_host_user_with_a_default(env):
    user = str(_service(env)["user"])
    assert re.fullmatch(r"\$\{HOST_UID:-1000\}:\$\{HOST_GID:-1000\}", user)


def test_compose_does_not_restart_or_publish_ports(env):
    service = _service(env)
    assert service.get("restart", "no") in ("no", False)
    assert "ports" not in service


# ------------------------------------------------------------ static: Dockerfile


def test_base_and_tool_images_are_pinned_to_an_exact_tag(env):
    refs = _image_references(env["instructions"])
    assert refs, "no image references found"
    for ref in refs:
        name, _, tag = ref.partition(":")
        assert tag and tag != "latest", f"{ref} is not pinned"
    python_refs = [r for r in refs if r.startswith("python:")]
    assert len(python_refs) >= 1
    for ref in python_refs:
        assert re.fullmatch(r"python:3\.12\.\d+-slim-[a-z]+(@sha256:[0-9a-f]{64})?", ref), ref


def test_dependencies_are_installed_from_the_lock_file(env):
    text = env["dockerfile"]
    assert re.search(r"uv sync\b[^\n]*--(frozen|locked)", text.replace("\\\n", " "))
    assert "--extra dev" in text.replace("\\\n", " ")
    copied = " ".join(rest for keyword, rest in env["instructions"] if keyword == "COPY")
    assert "uv.lock" in copied and "pyproject.toml" in copied
    assert not re.search(r"pip install[^\n]*\.\[dev\]", text)


def test_image_runs_as_a_non_root_user(env):
    users = [rest for keyword, rest in env["instructions"] if keyword == "USER"]
    assert users, "no USER instruction"
    final = users[-1].split(":")[0]
    assert final not in ("root", "0")


def test_default_command_runs_the_pipeline_cli_and_pytest_stays_overridable(env):
    instructions = env["instructions"]
    assert not any(k == "ENTRYPOINT" for k, _ in instructions), (
        "an ENTRYPOINT would turn `docker run image python -m pytest -q` into CLI arguments")
    commands = [rest for keyword, rest in instructions if keyword == "CMD"]
    assert commands, "no CMD instruction"
    import json

    assert json.loads(commands[-1]) == DEFAULT_CMD


def test_dockerfile_never_copies_data_or_output_explicitly(env):
    for keyword, rest in env["instructions"]:
        if keyword in ("COPY", "ADD"):
            sources = [t for t in rest.split() if not t.startswith("--")][:-1]
            for source in sources:
                assert source.strip("./").split("/")[0] not in ("data", "output"), rest


def test_dockerfile_sets_a_writable_home_and_unbuffered_output(env):
    text = env["dockerfile"]
    assert "PYTHONUNBUFFERED=1" in text
    assert re.search(r"\bHOME=", text)


# ------------------------------------------------------------ static: .dockerignore


@pytest.mark.parametrize("entry", ["data", "output", ".git", ".venv", "*.duckdb"])
def test_dockerignore_excludes_data_outputs_and_local_state(env, entry):
    assert entry in {e.rstrip("/") for e in env["dockerignore"]}


@pytest.mark.parametrize(
    "needed",
    ["Dockerfile", "docker-compose.yml", "pyproject.toml", "uv.lock", "README.md",
     "src", "tests", "scripts", "specs", "intents", "reports"],
)
def test_dockerignore_keeps_what_pytest_inside_the_container_needs(env, needed):
    ignored = {e.rstrip("/") for e in env["dockerignore"]}
    assert needed not in ignored
    assert f"{needed}/**" not in ignored


# ------------------------------------------------------- static: dependencies


def test_no_new_runtime_dependency_and_pyyaml_is_dev_only(env):
    project = env["pyproject"]["project"]
    assert project["dependencies"] == ["duckdb>=1.4"]
    dev = project["optional-dependencies"]["dev"]
    assert any(re.match(r"(?i)pyyaml\b", item) for item in dev)
    for existing in ("pytest>=8.0", "ipykernel>=6.29", "nbclient>=0.10", "nbformat>=5.10"):
        assert existing in dev


def test_lock_file_pins_duckdb_and_pyyaml(env):
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    names = {p["name"] for p in lock["package"]}
    assert {"duckdb", "pyyaml"} <= names


# ------------------------------------------------ CLI behavior the container relies on
# These pin existing behavior (no src/ change planned). They pass today.


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def test_missing_data_mount_fails_fast_with_a_clear_message(tmp_path, capsys):
    out = tmp_path / "output"
    out.mkdir()
    code = main(["run", "--data-dir", str(tmp_path / "absent"), "--output-dir", str(out)])
    captured = capsys.readouterr()
    assert code == 1
    assert "error: data directory not found" in captured.err
    assert "Traceback" not in captured.err
    assert list(out.iterdir()) == []


def test_empty_data_mount_fails_fast_and_writes_nothing(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    code = main(["run", "--data-dir", str(data), "--output-dir", str(out)])
    captured = capsys.readouterr()
    assert code == 1
    assert "error: required input missing" in captured.err
    assert list(out.iterdir()) == []


@pytest.mark.skipif(_is_root(), reason="root ignores directory permissions")
@pytest.mark.parametrize("mode_name", ["full", "incremental"])
def test_unwritable_output_directory_exits_non_zero_with_a_clear_message(
    tmp_path, capsys, mode_name
):
    data = make_data(tmp_path)
    out = tmp_path / "output"
    out.mkdir()
    out.chmod(0o500)
    try:
        code = main(["run", "--mode", mode_name, "--data-dir", str(data),
                     "--output-dir", str(out)])
    finally:
        out.chmod(0o700)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    assert list(out.iterdir()) == []


@pytest.mark.skipif(_is_root(), reason="root ignores file permissions")
def test_run_succeeds_on_a_read_only_data_tree_and_leaves_it_unchanged(tmp_path, capsys):
    data = make_data(tmp_path)

    def digest() -> dict[str, str]:
        return {p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(data.rglob("*")) if p.is_file()}

    before = digest()
    paths = sorted(data.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in paths + [data]:
        path.chmod(path.stat().st_mode & ~0o222)
    try:
        code = main(["run", "--data-dir", str(data), "--output-dir", str(tmp_path / "out")])
    finally:
        for path in [data] + sorted(data.rglob("*")):
            path.chmod(path.stat().st_mode | 0o700)
    assert code == 0
    assert digest() == before
    assert "run_id=" in capsys.readouterr().out


def test_second_run_in_a_fresh_output_is_identical_via_compare(tmp_path, capsys):
    data = make_data(tmp_path)
    assert main(["run", "--data-dir", str(data), "--output-dir", str(tmp_path / "a")]) == 0
    assert main(["run", "--data-dir", str(data), "--output-dir", str(tmp_path / "b")]) == 0
    capsys.readouterr()
    assert main(["compare", str(tmp_path / "a"), str(tmp_path / "b")]) == 0


def test_module_entry_point_propagates_the_exit_code(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "wearable_pipeline", "run", "--data-dir",
         str(tmp_path / "absent"), "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 1
    assert "data directory not found" in result.stderr


# ---------------------------------------------------- opt-in Docker integration tests

DOCKER = shutil.which("docker")
_docker_enabled = (
    os.environ.get("WEARABLE_DOCKER_TESTS") == "1"
    and DOCKER is not None
    and not Path("/.dockerenv").exists()
)
docker_only = pytest.mark.skipif(
    not _docker_enabled,
    reason="set WEARABLE_DOCKER_TESTS=1 on a host with Docker to run (builds the image)",
)
IMAGE = "wearable-pipeline-inc009-test"


def _docker(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, check=check)


def _run(data: Path | None, out: Path | None, *command: str) -> subprocess.CompletedProcess[str]:
    args = ["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}"]
    if data is not None:
        args += ["-v", f"{data}:/data:ro"]
    if out is not None:
        args += ["-v", f"{out}:/output"]
    return _docker(*args, IMAGE, *command)


@pytest.fixture(scope="module")
def image():
    if not _docker_enabled:
        pytest.skip("Docker integration tests are opt-in")
    _docker("build", "--tag", IMAGE, str(ROOT), check=True)
    return IMAGE


@docker_only
def test_docker_default_command_runs_the_pipeline_and_output_is_host_readable(image, tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "output"
    out.mkdir()
    result = _run(data, out)
    assert result.returncode == 0, result.stderr
    assert "run_id=" in result.stdout
    produced = [p for p in out.rglob("*") if p.is_file()]
    assert produced
    assert all(p.stat().st_uid == os.getuid() and os.access(p, os.R_OK) for p in produced)


@docker_only
def test_docker_second_run_is_identical_via_compare(image, tmp_path):
    data = make_data(tmp_path)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        assert _run(data, tmp_path / name).returncode == 0
    result = _docker("run", "--rm", "-v", f"{tmp_path}:/cmp:ro", IMAGE, "python", "-m",
                     "wearable_pipeline", "compare", "/cmp/a", "/cmp/b")
    assert result.returncode == 0, result.stdout + result.stderr


@docker_only
def test_docker_incremental_run_with_no_change_is_reachable_by_arguments(image, tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "output"
    out.mkdir()
    assert _run(data, out).returncode == 0
    result = _run(data, out, "python", "-m", "wearable_pipeline", "run", "--mode",
                  "incremental", "--data-dir", "/data", "--output-dir", "/output")
    assert result.returncode == 0, result.stderr
    assert "mode=" in result.stdout


@docker_only
def test_docker_read_only_mount_blocks_writes_to_data(image, tmp_path):
    data = make_data(tmp_path)
    result = _run(data, None, "sh", "-c", "touch /data/should_not_exist")
    assert result.returncode != 0
    assert not (data / "should_not_exist").exists()


@docker_only
def test_docker_missing_data_mount_and_unwritable_output_exit_non_zero(image, tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    missing = _run(None, out)
    assert missing.returncode != 0
    assert "error:" in missing.stderr and "Traceback" not in missing.stderr
    data = make_data(tmp_path)
    readonly = _docker("run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                       "-v", f"{data}:/data:ro", "-v", f"{out}:/output:ro", IMAGE)
    assert readonly.returncode != 0
    assert "error:" in readonly.stderr and "Traceback" not in readonly.stderr


@docker_only
def test_docker_pipeline_failure_exit_code_and_pytest_override(image, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    assert _run(empty, out).returncode == 1
    result = _docker("run", "--rm", IMAGE, "python", "-m", "pytest", "-q", "--co",
                     "tests/test_container.py")
    assert result.returncode == 0, result.stdout + result.stderr


@docker_only
def test_docker_image_contains_no_data_or_outputs_and_runs_non_root(image):
    listing = _docker("run", "--rm", IMAGE, "sh", "-c",
                      "ls -A /data /output 2>/dev/null; find / -xdev -name '*.duckdb' "
                      "-not -path '*/site-packages/*' 2>/dev/null; id -u")
    lines = [line for line in listing.stdout.split() if line]
    assert lines and lines[-1] != "0", "container runs as root"
    assert lines[:-1] == [], f"image contains data/outputs: {lines[:-1]}"
    app = _docker("run", "--rm", IMAGE, "sh", "-c", "ls -A /app")
    assert "data" not in app.stdout.split() and "output" not in app.stdout.split()
