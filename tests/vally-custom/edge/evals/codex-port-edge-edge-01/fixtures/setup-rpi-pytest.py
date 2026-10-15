"""Create a private, offline pytest environment for the RPI fixture."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Mapping


PYTHON_ENV = "HVE_EVAL_PYTHON"
PYTEST_VERSION = "9.0.3"
WORKSPACE_HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_RUNTIME_FILES = 20_000
MAX_RUNTIME_BYTES = 128 * 1024 * 1024


class RuntimeSetupError(RuntimeError):
    """The requested workspace or local pytest runtime is unsafe or unavailable."""


_SOURCE_PROBE = r"""
import importlib.metadata as metadata
import json
import sys
from pathlib import Path, PurePosixPath
from packaging.requirements import Requirement

EXPECTED_PYTEST = "9.0.3"
MAX_FILES = 20000
MAX_BYTES = 128 * 1024 * 1024

if sys.version_info < (3, 11):
    raise SystemExit("the local pytest interpreter must be Python 3.11 or newer")
if metadata.version("pytest") != EXPECTED_PYTEST:
    raise SystemExit(f"pytest must be exactly {EXPECTED_PYTEST}")

pending = ["pytest"]
seen = set()
files = {}
total_bytes = 0
site_roots = set()
while pending:
    requested_name = pending.pop()
    key = requested_name.lower().replace("_", "-")
    if key in seen:
        continue
    seen.add(key)
    dist = metadata.distribution(requested_name)
    if not dist.files:
        raise SystemExit(f"distribution {requested_name} has no installed-file manifest")
    site_root = Path(dist.locate_file(".")).resolve(strict=True)
    site_roots.add(str(site_root))
    for entry in dist.files:
        relative = PurePosixPath(str(entry))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            continue
        if "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        source = site_root.joinpath(*relative.parts)
        if source.is_symlink():
            raise SystemExit(f"refusing symlink in runtime distribution {requested_name}")
        if not source.exists():
            raise SystemExit(f"runtime distribution {requested_name} is missing a recorded file")
        if source.is_dir():
            continue
        if not source.is_file():
            raise SystemExit(f"runtime distribution {requested_name} contains a non-file entry")
        resolved = source.resolve(strict=True)
        if site_root not in resolved.parents:
            raise SystemExit(f"runtime distribution {requested_name} escapes site-packages")
        size = source.stat().st_size
        total_bytes += size
        if total_bytes > MAX_BYTES:
            raise SystemExit("pytest runtime exceeds the 128 MiB copy limit")
        files[str(relative)] = size
        if len(files) > MAX_FILES:
            raise SystemExit("pytest runtime exceeds the 20,000-file copy limit")
    for requirement_text in dist.requires or []:
        requirement = Requirement(requirement_text)
        if requirement.marker is None or requirement.marker.evaluate():
            pending.append(requirement.name)

if len(site_roots) != 1:
    raise SystemExit("pytest dependencies resolve from multiple site-packages roots")
print(json.dumps({
    "python": sys.executable,
    "version": list(sys.version_info[:3]),
    "pytest": metadata.version("pytest"),
    "site_packages": next(iter(site_roots)),
    "files": [{"path": name, "size": size} for name, size in sorted(files.items())],
}))
"""


def _safe_workspace(workspace: Path) -> Path:
    try:
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise RuntimeSetupError("the Vally workspace does not exist") from exc
    if not resolved.is_dir():
        raise RuntimeSetupError("the Vally workspace is not a directory")
    if resolved.parent.name != "workspaces" or not WORKSPACE_HASH.fullmatch(resolved.name):
        raise RuntimeSetupError(
            "refusing setup outside output/workspaces/<64-lowercase-hex-id>"
        )
    return resolved


def _resolve_executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.absolute())
    located = shutil.which(value)
    if located:
        return str(Path(located).absolute())
    return None


def _probe_python(executable: str) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [executable, "-c", _SOURCE_PROBE],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeSetupError("the candidate Python interpreter could not be inspected") from exc
    if result.returncode != 0:
        raise RuntimeSetupError(
            "the candidate must have Python >=3.11, pytest 9.0.3, and its installed "
            "runtime dependencies"
        )
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeSetupError("the candidate Python returned an invalid runtime manifest") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeSetupError("the candidate Python returned an incomplete runtime manifest")
    return manifest


def _select_python(environ: Mapping[str, str]) -> tuple[str, dict[str, object]]:
    explicit = environ.get(PYTHON_ENV, "").strip()
    if explicit:
        executable = _resolve_executable(explicit)
        if executable is None:
            raise RuntimeSetupError(f"{PYTHON_ENV} does not name an executable Python interpreter")
        try:
            return executable, _probe_python(executable)
        except RuntimeSetupError as exc:
            raise RuntimeSetupError(
                f"{PYTHON_ENV} is not a compatible offline pytest source: {exc}"
            ) from exc

    candidates = [
        sys.executable,
        shutil.which("python3") or "",
        shutil.which("python") or "",
    ]
    seen: set[str] = set()
    failures: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        executable = _resolve_executable(candidate)
        if executable is None or executable in seen:
            continue
        seen.add(executable)
        try:
            return executable, _probe_python(executable)
        except RuntimeSetupError:
            failures.append(Path(executable).name)
    tried = ", ".join(failures) if failures else "no local interpreter"
    raise RuntimeSetupError(
        f"no compatible offline pytest 9.0.3 runtime was found ({tried}); "
        f"set {PYTHON_ENV} to its Python 3.11+ interpreter"
    )


def _venv_python(environment: Path) -> Path:
    candidates = (
        environment / "bin" / "python",
        environment / "Scripts" / "python.exe",
        environment / "Scripts" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeSetupError("standard venv creation did not produce a Python interpreter")


def _clean_subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def setup_pytest_environment(
    workspace: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Build and verify a private pytest environment without network access or overwrites."""

    workspace = _safe_workspace((workspace or Path.cwd()).absolute())
    target = workspace / ".venv"
    if os.path.lexists(target):
        raise RuntimeSetupError("refusing to overwrite the existing .venv")

    executable, manifest = _select_python(os.environ if environ is None else environ)
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise RuntimeSetupError("refusing to overwrite the existing .venv") from exc
    claimed = True
    completed = False
    try:
        created = subprocess.run(
            [executable, "-m", "venv", "--without-pip", str(target)],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            env=_clean_subprocess_environment(),
        )
        if created.returncode != 0:
            raise RuntimeSetupError("standard venv creation failed using the selected interpreter")

        python = _venv_python(target)
        path_result = subprocess.run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=_clean_subprocess_environment(),
        )
        if path_result.returncode != 0:
            raise RuntimeSetupError("the private venv could not locate its site-packages directory")
        site_packages = Path(path_result.stdout.strip()).resolve(strict=True)
        if target.resolve(strict=True) not in site_packages.parents:
            raise RuntimeSetupError("the private venv site-packages path escapes its workspace")

        source_root = Path(str(manifest["site_packages"])).resolve(strict=True)
        files = manifest["files"]
        if len(files) > MAX_RUNTIME_FILES:
            raise RuntimeSetupError("pytest runtime exceeds the 20,000-file copy limit")
        copied_bytes = 0
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise RuntimeSetupError("the selected pytest runtime manifest is malformed")
            relative = PurePosixPath(item["path"])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise RuntimeSetupError("the selected pytest runtime contains an unsafe path")
            source = source_root.joinpath(*relative.parts)
            if source.is_symlink() or not source.is_file():
                raise RuntimeSetupError("the selected pytest runtime changed during setup")
            resolved_source = source.resolve(strict=True)
            if source_root not in resolved_source.parents:
                raise RuntimeSetupError("a selected pytest runtime file escapes site-packages")
            size = source.stat().st_size
            if item.get("size") != size:
                raise RuntimeSetupError("the selected pytest runtime changed during setup")
            copied_bytes += size
            if copied_bytes > MAX_RUNTIME_BYTES:
                raise RuntimeSetupError("pytest runtime exceeds the 128 MiB copy limit")
            destination = site_packages.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(destination):
                raise RuntimeSetupError("refusing to overwrite a file in the private venv")
            shutil.copy2(source, destination)

        smoke = subprocess.run(
            [str(python), "-m", "pytest", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=_clean_subprocess_environment(),
        )
        if smoke.returncode != 0 or smoke.stdout.strip() != f"pytest {PYTEST_VERSION}":
            raise RuntimeSetupError("the copied private pytest runtime failed its version check")

        final_smoke = subprocess.run(
            [str(_venv_python(target)), "-m", "pytest", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=_clean_subprocess_environment(),
        )
        if (
            final_smoke.returncode != 0
            or final_smoke.stdout.strip() != f"pytest {PYTEST_VERSION}"
        ):
            raise RuntimeSetupError("the published private pytest runtime failed its version check")
        completed = True
        return target
    except subprocess.TimeoutExpired as exc:
        raise RuntimeSetupError("offline pytest environment setup exceeded its time limit") from exc
    finally:
        if claimed and not completed and os.path.lexists(target):
            shutil.rmtree(target)


def main() -> int:
    try:
        environment = setup_pytest_environment()
    except RuntimeSetupError as exc:
        print(f"RPI pytest setup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared private pytest {PYTEST_VERSION} in {environment}")
    print("Run tests with .venv/bin/python -m pytest or .venv/Scripts/python.exe on Windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
