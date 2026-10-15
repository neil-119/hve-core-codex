from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SUPPORT_SCRIPT = (
    ROOT
    / "tests/vally-custom/edge/evals/codex-port-edge-edge-01/fixtures/setup-rpi-pytest.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location("rpi_pytest_setup", SUPPORT_SCRIPT)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
RPI_SETUP = importlib.util.module_from_spec(SCRIPT_SPEC)
previous_bytecode_setting = sys.dont_write_bytecode
try:
    sys.dont_write_bytecode = True
    SCRIPT_SPEC.loader.exec_module(RPI_SETUP)
finally:
    sys.dont_write_bytecode = previous_bytecode_setting


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "output" / "workspaces" / ("a" * 64)
    workspace.mkdir(parents=True)
    return workspace


def _source_python() -> str:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pytest; print(sys.version_info >= (3, 11)); print(pytest.__version__)",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if probe.returncode != 0 or probe.stdout.splitlines() != ["True", "9.0.3"]:
        pytest.skip("requires a local Python 3.11+ interpreter with pytest 9.0.3")
    return sys.executable


def test_offline_bootstrap_creates_private_pytest_venv(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source_python = _source_python()

    environment = RPI_SETUP.setup_pytest_environment(
        workspace,
        environ={RPI_SETUP.PYTHON_ENV: source_python},
    )

    assert environment == workspace / ".venv"
    python = RPI_SETUP._venv_python(environment)
    assert "include-system-site-packages = false" in (
        environment / "pyvenv.cfg"
    ).read_text(encoding="utf-8")
    smoke = subprocess.run(
        [str(python), "-m", "pytest", "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert smoke.returncode == 0
    assert smoke.stdout.strip() == "pytest 9.0.3"
    assert not (environment / "bin" / "pip").exists()
    assert not (environment / "Scripts" / "pip.exe").exists()


def test_bootstrap_rejects_workspace_outside_hash_workspace_before_writing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ordinary-project"
    workspace.mkdir()

    with pytest.raises(RPI_SETUP.RuntimeSetupError, match="output/workspaces"):
        RPI_SETUP.setup_pytest_environment(workspace, environ={})

    assert not (workspace / ".venv").exists()


def test_bootstrap_refuses_to_overwrite_existing_venv(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    environment = workspace / ".venv"
    environment.mkdir()
    marker = environment / "owner-marker"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(RPI_SETUP.RuntimeSetupError, match="overwrite the existing"):
        RPI_SETUP.setup_pytest_environment(workspace, environ={})

    assert marker.read_text(encoding="utf-8") == "preserve"
