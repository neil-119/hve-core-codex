"""Custom Codex host runtime checks; upstream workflow evaluations use Vally."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import re
import shlex
import shutil
import signal
import subprocess
import threading
import tempfile
import time
import tomllib
from contextlib import nullcontext
from pathlib import Path
from typing import Any


from .common import PortError, digest, dump_json, safe_path
from .eval_usage import summarize_usage, usage_observations
# Re-export the shared helper names for the existing runner and its focused tests.
from .host_acceptance import (
    _MAX_CHECKPOINT_FILE_BYTES,
    _MAX_CHECKPOINT_TOTAL_BYTES,
    _MAX_CHECKPOINT_FILES,
    _MAX_NATIVE_WORKSPACE_FILES,
    _MAX_NATIVE_WORKSPACE_BYTES,
    _check_result,  # noqa: F401 - retained for legacy focused tests
    _fixture_baseline,
    _init_isolated_repo,
    _install_scenario,
    _is_link_or_reparse,
    _is_native_windows,
    _json_pointer,  # noqa: F401 - retained for legacy focused tests
    _native_relative_path_parts,
    _native_workspace_read_files,
    _native_workspace_response,
    _pin_staged_model_settings,
    _resolve_entrypoints,
    _scenario_checks,
    _scenario_data,
    _scenario_report,
    _scenario_stage_preconditions,
    _source_artifact_evidence,  # noqa: F401 - retained for legacy focused tests
    _stage_fixtures,
    _validate_native_attested_path,
    _validate_native_relative_path,  # noqa: F401 - retained for legacy focused tests
)


_MAX_SESSION_OUTPUT_BYTES = 4 * 1024 * 1024
_WINDOWS_SANDBOX_PREFLIGHT_TIMEOUT_SECONDS = 12
_MAX_APP_SERVER_PROTOCOL_LINE_BYTES = 256 * 1024
_MAX_APP_SERVER_PROTOCOL_LINES = 32
_NATIVE_STARTUP_FAILURE_SIGNATURE = (
    "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"
)
DEFAULT_EVAL_MODEL = "gpt-6-luna"
DEFAULT_EVAL_REASONING_EFFORT = "max"
_SUPPORTED_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
_NATIVE_TRUST_CHECKS = {"native_hook_context", "native_delegation_context"}
_AGENT_DEFAULT_CONFIG_CAPABILITIES = {
    "codex-cli 0.144.6": {
        "status": "unsupported",
        "evidence": "model-free app-server config parse rejected agents.default_subagent_model as AgentRoleToml",
    },
    "codex-cli 0.162.0-alpha.17.2": {
        "status": "supported",
        "evidence": "known version with verified current agent-default config support",
    },
    "codex-cli 0.162.1": {
        "status": "supported",
        "evidence": (
            "model-free app-server config/read with --strict-config read back both agent-default "
            "settings from process-only config overrides; no model sessions started"
        ),
    },
}


def _agent_default_config_capability(version: Any) -> dict[str, Any]:
    """Return the narrowly versioned support status for global agent defaults."""
    normalized_version = version.strip() if isinstance(version, str) else None
    known = _AGENT_DEFAULT_CONFIG_CAPABILITIES.get(normalized_version or "")
    if known is None:
        return {
            "status": "unverified",
            "version": normalized_version,
            "method": "exact-version-allowlist",
            "evidence": "No model-free compatibility evidence is recorded for this exact CLI version.",
        }
    return {
        "status": known["status"],
        "version": normalized_version,
        "method": "exact-version-allowlist",
        "evidence": known["evidence"],
    }






















































_MAX_NPM_PACKAGE_JSON_BYTES = 64 * 1024


def _read_npm_codex_package(package_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        resolved_root = package_root.resolve(strict=True)
        if resolved_root != package_root:
            return None, "npm Codex package directories may not be symlinks"
        package_json = resolved_root / "package.json"
        if package_json.resolve(strict=True).parent != resolved_root:
            return None, "npm Codex package metadata must remain inside its package directory"
        with package_json.open("rb") as stream:
            payload = stream.read(_MAX_NPM_PACKAGE_JSON_BYTES + 1)
    except OSError as exc:
        return None, f"Could not read npm Codex package metadata: {exc}"
    if len(payload) > _MAX_NPM_PACKAGE_JSON_BYTES:
        return None, "npm Codex package metadata exceeds the size limit"
    try:
        metadata = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"npm Codex package metadata is invalid: {exc}"
    if not isinstance(metadata, dict) or metadata.get("name") != "@openai/codex":
        return None, "npm package is not the official @openai/codex package"
    return metadata, None


def _windows_npm_codex_native_executable(shim: Path) -> tuple[str | None, str | None]:
    if shim.name.casefold() not in {"codex.cmd", "codex.ps1"}:
        return None, "Only the npm codex.cmd and codex.ps1 shims are supported on Windows"
    if not shim.is_file():
        return None, f"Codex npm shim was not found: {shim}"

    try:
        npm_prefix = shim.parent.resolve(strict=True)
        package_root = npm_prefix / "node_modules" / "@openai" / "codex"
        if package_root.resolve(strict=True) != package_root:
            return None, "Adjacent npm Codex package directories may not be symlinks"
    except OSError as exc:
        return None, f"Adjacent npm Codex package was not found: {exc}"

    package, error = _read_npm_codex_package(package_root)
    if error:
        return None, error
    assert package is not None
    bin_field = package.get("bin")
    bin_target = bin_field.get("codex") if isinstance(bin_field, dict) else bin_field
    if bin_target != "bin/codex.js":
        return None, "npm @openai/codex package must map codex to bin/codex.js"
    try:
        script_path = (package_root / "bin" / "codex.js").resolve(strict=True)
        script_path.relative_to(package_root)
    except (OSError, ValueError):
        return None, "npm @openai/codex bin script is missing or outside its package directory"
    if not script_path.is_file():
        return None, "npm @openai/codex bin script is not a file"

    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        architecture = "x64"
        target_triple = "x86_64-pc-windows-msvc"
    elif machine in {"arm64", "aarch64"}:
        architecture = "arm64"
        target_triple = "aarch64-pc-windows-msvc"
    else:
        return None, f"Unsupported Windows Codex architecture: {platform.machine()}"

    optional_package = package_root / "node_modules" / "@openai" / f"codex-win32-{architecture}"
    optional_package_json = optional_package / "package.json"
    if optional_package_json.is_file():
        try:
            if optional_package.resolve(strict=True) != optional_package:
                return None, "Optional npm Codex package directories may not be symlinks"
        except OSError as exc:
            return None, f"Optional npm Codex package could not be resolved: {exc}"
        optional_metadata, error = _read_npm_codex_package(optional_package)
        if error:
            return None, error
        assert optional_metadata is not None
        expected_version = f"{package.get('version', '')}-win32-{architecture}"
        if not isinstance(package.get("version"), str) or optional_metadata.get("version") != expected_version:
            return None, "Optional npm Codex package version does not match the adjacent Codex package"
        vendor_root = optional_package / "vendor"
    else:
        # Match the official npm launcher: use the package-local vendor tree
        # only when the platform optional dependency cannot be resolved.
        vendor_root = package_root / "vendor"

    native_binary = vendor_root / target_triple / "bin" / "codex.exe"
    try:
        resolved_vendor_root = vendor_root.resolve(strict=True)
        resolved_vendor_root.relative_to(package_root)
        resolved_binary = native_binary.resolve(strict=True)
        resolved_binary.relative_to(resolved_vendor_root)
    except (OSError, ValueError):
        return None, f"Supported native Windows Codex binary was not found: {native_binary}"
    if not resolved_binary.is_file() or resolved_binary.suffix.casefold() != ".exe":
        return None, f"Supported native Windows Codex binary is not a file: {native_binary}"
    return str(resolved_binary), None


def _codex_executable(command: str) -> tuple[str | None, str | None]:
    raw = str(command).strip()
    if not raw:
        return None, "codex_command is empty"
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        token = raw[1:-1]
    elif Path(raw).expanduser().is_file():
        # An executable path is data, not POSIX shell syntax. In particular,
        # shlex would remove backslashes from ordinary Windows paths.
        token = raw
    else:
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError as exc:
            return None, f"Could not parse codex_command: {exc}"
        if len(tokens) != 1:
            return None, "codex_command must identify one executable; arguments are not accepted"
        token = tokens[0]
    located = shutil.which(token)
    if located:
        if _is_native_windows() and Path(located).suffix.casefold() in {".cmd", ".ps1"}:
            return _windows_npm_codex_native_executable(Path(located))
        if _is_native_windows() and Path(located).suffix.casefold() == ".bat":
            return None, "Batch command shims are not invoked because this probe does not start a shell"
        if _is_native_windows():
            try:
                return str(Path(located).resolve()), None
            except (OSError, RuntimeError):
                pass
        return located, None
    candidate = Path(token).expanduser()
    if candidate.is_file():
        if _is_native_windows() and candidate.suffix.casefold() in {".cmd", ".ps1"}:
            return _windows_npm_codex_native_executable(candidate)
        if _is_native_windows() and candidate.suffix.casefold() == ".bat":
            return None, "Batch command shims are not invoked because this probe does not start a shell"
        return str(candidate.resolve()), None
    return None, f"Codex executable was not found: {token}"


def _probe_codex(command: str, timeout_seconds: int, *, deadline: float | None = None) -> dict[str, Any]:
    executable, error = _codex_executable(command)
    if error:
        return {
            "available": False,
            "requested_command": command,
            "error": error,
            "probes": [],
            "version": None,
            "exec_help_available": False,
            "resume_help_available": False,
            "features": {
                "json_events": False,
                "workspace_write_sandbox": False,
                "ignore_user_config": False,
                "resume": False,
                "model_override": False,
                "config_override": False,
                "resume_model_override": False,
                "resume_config_override": False,
            },
        }
    probes: list[dict[str, Any]] = []
    commands = [
        ("version", ["--version"]),
        ("root_help", ["--help"]),
        ("exec_help", ["exec", "--help"]),
        ("resume_help", ["exec", "--json", "resume", "--help"]),
    ]
    help_text: dict[str, str] = {}
    for name, arguments in commands:
        remaining = min(max(timeout_seconds, 1), 30)
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
        if remaining <= 0:
            probes.append({"name": name, "arguments": arguments, "status": "budget-exhausted"})
            continue
        try:
            completed = subprocess.run(
                [executable, *arguments],
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=remaining,
                check=False,
            )
            output = "\n".join(part for part in (completed.stdout or "", completed.stderr or "") if part).strip()
            if name in {"exec_help", "resume_help"}:
                help_text[name] = output
            probes.append({
                "name": name,
                "arguments": arguments,
                "returncode": completed.returncode,
                "status": "available" if completed.returncode == 0 else "failed",
                "first_line": output.splitlines()[0][:240] if output else None,
            })
        except (OSError, subprocess.TimeoutExpired) as exc:
            probes.append({"name": name, "arguments": arguments, "status": "error", "error": str(exc)[:500]})
    version_probe = next((item for item in probes if item["name"] == "version" and item.get("status") == "available"), None)
    exec_probe = next((item for item in probes if item["name"] == "exec_help" and item.get("status") == "available"), None)
    resume_probe = next((item for item in probes if item["name"] == "resume_help" and item.get("status") == "available"), None)
    exec_help = help_text.get("exec_help", "")
    resume_help = help_text.get("resume_help", "")
    features = {
        "json_events": "--json" in exec_help,
        "workspace_write_sandbox": "--sandbox" in exec_help and "workspace-write" in exec_help,
        "ignore_user_config": "--ignore-user-config" in exec_help,
        "model_override": "--model" in exec_help,
        "config_override": "--config" in exec_help or re.search(r"(?:^|[\s,])-c(?:[\s,]|$)", exec_help) is not None,
        "resume_model_override": "--model" in resume_help,
        "resume_config_override": "--config" in resume_help or re.search(r"(?:^|[\s,])-c(?:[\s,]|$)", resume_help) is not None,
        "resume": resume_probe is not None and "resume" in resume_help.casefold() and "--json" in resume_help,
    }
    return {
        "available": any(item.get("status") == "available" for item in probes),
        "requested_command": command,
        "version": version_probe.get("first_line") if version_probe else None,
        "exec_help_available": exec_probe is not None,
        "resume_help_available": resume_probe is not None,
        "features": features,
        "probes": probes,
    }


def _codex_event_summary(output: bytes, fallback_session_id: str | None = None) -> dict[str, Any]:
    thread_id: str | None = fallback_session_id
    event_types: list[str] = []
    parse_errors = 0
    turn_completed = False
    native_startup_failure = False
    observations: list[dict[str, Any]] = []
    observed_models: set[str] = set()
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            parse_errors += 1
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if isinstance(event_type, str) and event_type not in event_types:
            event_types.append(event_type)
        if event_type == "turn.completed":
            turn_completed = True
        item = event.get("item")
        if (
            event_type == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "command_execution"
            and item.get("status") == "failed"
            and item.get("exit_code") == -1
            and item.get("aggregated_output") == _NATIVE_STARTUP_FAILURE_SIGNATURE
        ):
            native_startup_failure = True
        if event_type in {"thread.started", "thread.resumed"}:
            candidate = event.get("thread_id") or event.get("session_id")
            if isinstance(candidate, str) and candidate:
                thread_id = candidate
        observations.extend(usage_observations(event, thread_id))
        for observation in observations[-1:]:
            model = observation.get("observed_model")
            if isinstance(model, str) and model:
                observed_models.add(model)
    return {
        "session_id": thread_id,
        "event_types": event_types,
        "unparsed_lines": parse_errors,
        "turn_completed": turn_completed,
        "native_startup_failure": native_startup_failure,
        "usage_observations": observations,
        "observed_models": sorted(observed_models),
    }


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        if taskkill.is_file():
            try:
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass




def _existing_project_is_trusted(workspace: Path, config_home: Path | None = None) -> bool:
    """Check existing user trust without modifying it or reading account credentials."""
    if config_home is None:
        configured_home = os.environ.get("CODEX_HOME")
        config_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    try:
        with (config_home / "config.toml").open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return False
        projects = tomllib.loads(raw.decode("utf-8")).get("projects", {})
        if not isinstance(projects, dict):
            return False
        workspace_path = os.path.normcase(str(workspace.resolve()))
        matches: list[Any] = []
        for project, settings in projects.items():
            if not isinstance(settings, dict) or not os.path.isabs(project):
                continue
            project_path = os.path.normcase(os.path.abspath(project))
            try:
                if workspace_path == project_path:
                    matches.append(settings.get("trust_level"))
            except ValueError:
                continue
        if not matches:
            return False
        return all(level == "trusted" for level in matches)
    except (OSError, ValueError):
        return False


def _read_windows_sandbox_mode(config_home: Path) -> tuple[str | None, str | None]:
    """Read only the existing native Windows sandbox mode from user config."""
    config_path = Path(config_home).expanduser() / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, (
            "Windows evals require an existing `windows.sandbox` setting of `elevated` or `unelevated` "
            "in the Codex user config. Add the setting before running; the evaluator will not install "
            "or change Windows sandbox setup."
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None, (
            "The Codex user config could not be read as valid TOML, so the evaluator cannot verify "
            "an existing Windows sandbox mode. Fix the user config before running; no setup will be attempted."
        )
    windows = config.get("windows")
    mode = windows.get("sandbox") if isinstance(windows, dict) else None
    if not isinstance(mode, str) or mode not in {"elevated", "unelevated"}:
        return None, (
            "Windows evals require `[windows] sandbox = \"elevated\"` or "
            "`[windows] sandbox = \"unelevated\"` in the existing Codex user config. "
            "The evaluator will not choose a mode, install a sandbox, or fall back to a broader permission mode."
        )
    return mode, None


def _existing_windows_sandbox_mode() -> tuple[str | None, str | None]:
    if not _is_native_windows():
        return None, None
    codex_home = os.environ.get("CODEX_HOME")
    config_home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return _read_windows_sandbox_mode(config_home)


def _windows_native_sandbox_config_args(
    mode: str | None,
    *,
    is_windows: bool | None = None,
) -> list[str]:
    """Forward only a configured Windows sandbox implementation mode."""
    native_windows = _is_native_windows() if is_windows is None else is_windows
    if not native_windows:
        return []
    if not isinstance(mode, str) or mode not in {"elevated", "unelevated"}:
        raise PortError("Windows evals require a verified existing `windows.sandbox` user-config mode")
    return ["--config", f"windows.sandbox={json.dumps(mode)}"]
















def _probe_windows_sandbox_readiness(
    executable: str | None,
    mode: str | None,
    timeout_seconds: float,
    *,
    is_windows: bool | None = None,
) -> dict[str, Any]:
    """Read native Windows sandbox readiness without creating a session or running a turn."""
    native_windows = _is_native_windows() if is_windows is None else is_windows
    base = {
        "method": "app-server/windowsSandbox/readiness",
        "model_turns_started": 0,
    }
    if not native_windows:
        return {**base, "status": "not-applicable", "readiness_status": None, "reason": None}
    if not isinstance(mode, str) or mode not in {"elevated", "unelevated"}:
        return {
            **base,
            "status": "blocked-config-mode-unavailable",
            "readiness_status": None,
            "reason": "No valid existing Windows sandbox mode is available for a read-only readiness probe.",
        }
    if not executable:
        return {
            **base,
            "status": "blocked-probe-unavailable",
            "readiness_status": None,
            "reason": "Codex CLI is unavailable, so Windows sandbox readiness could not be checked.",
        }
    timeout = min(
        _WINDOWS_SANDBOX_PREFLIGHT_TIMEOUT_SECONDS,
        max(0.0, float(timeout_seconds)),
    )
    if timeout <= 0:
        return {
            **base,
            "status": "blocked-probe-time-budget-exhausted",
            "readiness_status": None,
            "reason": "No eval time budget remained for the read-only Windows sandbox readiness check.",
        }

    command = [
        executable,
        "app-server",
        "--stdio",
        "--config",
        f"windows.sandbox={json.dumps(mode)}",
    ]
    process: subprocess.Popen[bytes] | None = None
    output_lines: queue.Queue[bytes] = queue.Queue(maxsize=_MAX_APP_SERVER_PROTOCOL_LINES)
    stdout_finished = threading.Event()
    protocol_overflow = threading.Event()
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(Path.home()),
            shell=False,
        )
        if process.stdin is None or process.stdout is None:
            return {
                **base,
                "status": "blocked-probe-unavailable",
                "readiness_status": None,
                "reason": "Codex app-server did not expose the expected JSON-RPC stdio pipes.",
            }

        def collect_stdout() -> None:
            assert process is not None and process.stdout is not None
            try:
                for _ in range(_MAX_APP_SERVER_PROTOCOL_LINES):
                    line = process.stdout.readline(_MAX_APP_SERVER_PROTOCOL_LINE_BYTES + 1)
                    if not line:
                        break
                    if len(line) > _MAX_APP_SERVER_PROTOCOL_LINE_BYTES:
                        protocol_overflow.set()
                        break
                    try:
                        output_lines.put_nowait(line)
                    except queue.Full:
                        protocol_overflow.set()
                        break
            finally:
                stdout_finished.set()

        stdout_thread = threading.Thread(target=collect_stdout, daemon=True)
        stdout_thread.start()

        def send(message: dict[str, Any]) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            process.stdin.flush()

        def receive_response(request_id: int) -> dict[str, Any] | None:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    line = output_lines.get(timeout=remaining)
                except queue.Empty:
                    if stdout_finished.is_set() or protocol_overflow.is_set():
                        return None
                    continue
                try:
                    message = json.loads(line.decode("utf-8", errors="replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message

        send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "hve-eval-windows-sandbox-preflight",
                    "title": "HVE eval Windows sandbox preflight",
                    "version": "1.0.0",
                },
            },
        })
        initialized = receive_response(1)
        if initialized is None:
            return {
                **base,
                "status": "blocked-probe-timeout",
                "readiness_status": None,
                "reason": "Codex app-server did not complete initialization within the bounded preflight timeout.",
            }
        if "error" in initialized:
            return {
                **base,
                "status": "blocked-probe-error",
                "readiness_status": None,
                "reason": "Codex app-server rejected the read-only Windows sandbox readiness probe.",
            }

        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "windowsSandbox/readiness", "params": None})
        readiness = receive_response(2)
        if readiness is None:
            return {
                **base,
                "status": "blocked-probe-timeout",
                "readiness_status": None,
                "reason": "Codex app-server did not return Windows sandbox readiness within the bounded preflight timeout.",
            }
        if "error" in readiness:
            return {
                **base,
                "status": "blocked-probe-error",
                "readiness_status": None,
                "reason": "Codex app-server could not read Windows sandbox readiness.",
            }
        result = readiness.get("result")
        readiness_status = result.get("status") if isinstance(result, dict) else None
        if readiness_status == "ready":
            return {
                **base,
                "status": "ready",
                "readiness_status": readiness_status,
                "reason": None,
            }
        if readiness_status == "updateRequired":
            return {
                **base,
                "status": "blocked-readiness-update-required",
                "readiness_status": readiness_status,
                "reason": (
                    "Codex reports that the native Windows sandbox needs an update or setup. Complete the "
                    "reviewed host setup in Codex, then rerun the model-free readiness check; no setup was attempted."
                ),
            }
        if readiness_status == "notConfigured":
            return {
                **base,
                "status": "blocked-readiness-not-configured",
                "readiness_status": readiness_status,
                "reason": "Codex reports that the native Windows sandbox is not configured; no setup was attempted.",
            }
        return {
            **base,
            "status": "blocked-readiness-unknown",
            "readiness_status": readiness_status if isinstance(readiness_status, str) else None,
            "reason": "Codex did not explicitly report the native Windows sandbox as ready.",
        }
    except subprocess.TimeoutExpired:
        return {
            **base,
            "status": "blocked-probe-timeout",
            "readiness_status": None,
            "reason": "Codex app-server readiness probe exceeded its bounded timeout.",
        }
    except (OSError, ValueError, BrokenPipeError):
        return {
            **base,
            "status": "blocked-probe-error",
            "readiness_status": None,
            "reason": "Codex app-server readiness probe could not be completed safely.",
        }
    finally:
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


def _computer_use_isolation_args() -> list[str]:
    """Process-only opt-in isolation; never edit account, trust, or sandbox settings."""
    args = ["--config", "mcp_servers.node_repl.enabled=false"]
    names = (
        "computer-use@openai-bundled", "unified-computer-use@openai-bundled",
        "browser@openai-bundled", "chrome@openai-bundled",
    )
    table = ",".join(f"{json.dumps(name)}={{enabled=false}}" for name in names)
    args.extend(["--config", "plugins={" + table + "}"])
    return args


def _execute_codex_turn(
    executable: str,
    workspace: Path,
    prompt: str,
    *,
    session_id: str | None,
    timeout_seconds: float,
    output_path: Path,
    model: str = DEFAULT_EVAL_MODEL,
    reasoning_effort: str = DEFAULT_EVAL_REASONING_EFFORT,
    windows_sandbox_mode: str | None = None,
    use_project_config: bool = False,
    supports_agent_default_config: bool = True,
    isolate_computer_use: bool = False,
) -> dict[str, Any]:
    try:
        windows_sandbox_args = _windows_native_sandbox_config_args(windows_sandbox_mode)
        if use_project_config:
            config = tomllib.loads(safe_path(workspace, ".codex/config.toml").read_text(encoding="utf-8"))
            if config.get("sandbox_mode") != "workspace-write":
                raise PortError("Reviewed eval project must configure workspace-write as its sandbox default")
            if config.get("model") != model or config.get("model_reasoning_effort") != reasoning_effort:
                raise PortError("Reviewed eval project model settings do not match the requested model")
            if not supports_agent_default_config:
                agents = config.get("agents")
                if isinstance(agents, dict) and any(
                    key in agents
                    for key in ("default_subagent_model", "default_subagent_reasoning_effort")
                ):
                    raise PortError(
                        "Reviewed eval project contains agent-default settings unsupported by this CLI version"
                    )
            if not _existing_project_is_trusted(workspace):
                raise PortError("Eval fixture requires an existing exact-path Codex project trust entry; no trust settings were changed")
    except (PortError, OSError, ValueError) as exc:
        return {
            "status": "launch-error",
            "returncode": None,
            "session_id": None,
            "event_types": [],
            "output_bytes": 0,
            "output_sha256": digest(b""),
            "output_truncated": False,
            "output_file": None,
            "error": str(exc),
            "prompt_sha256": digest(prompt.encode("utf-8")),
        }
    configuration_options = [] if use_project_config else ["--ignore-user-config"]
    common_options = [
        *(_computer_use_isolation_args() if isolate_computer_use else []),
        *configuration_options,
        "--json",
        *([] if use_project_config else ["--sandbox", "workspace-write"]),
        *windows_sandbox_args,
        "--model",
        model,
        "--config",
        f"model_reasoning_effort={json.dumps(reasoning_effort)}",
        *(
            [
                "--config",
                f"agents.default_subagent_model={json.dumps(model)}",
                "--config",
                f"agents.default_subagent_reasoning_effort={json.dumps(reasoning_effort)}",
            ]
            if supports_agent_default_config
            else []
        ),
    ]
    if session_id is None:
        command = [executable, "exec", *common_options, prompt]
    else:
        command = [executable, "exec", *common_options, "resume", session_id, prompt]
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        start_new_session = False
    else:
        creationflags = 0
        start_new_session = True
    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            bufsize=0,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
    except OSError as exc:
        return {
            "status": "launch-error",
            "returncode": None,
            "session_id": None,
            "event_types": [],
            "output_bytes": 0,
            "output_sha256": digest(b""),
            "output_truncated": False,
            "output_file": None,
            "error": str(exc)[:1000],
            "prompt_sha256": digest(prompt.encode("utf-8")),
        }

    captured = bytearray()
    truncated = False

    def collect() -> None:
        nonlocal truncated
        pending = b""
        failure_stopped = False
        stream = process.stdout
        if stream is None:
            return
        read = getattr(stream, "read1", stream.read)
        while True:
            chunk = read(64 * 1024)
            if not chunk:
                break
            available = max(0, _MAX_SESSION_OUTPUT_BYTES - len(captured))
            if available:
                captured.extend(chunk[:available])
            if len(chunk) > available:
                truncated = True
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            for line in lines:
                if not failure_stopped and _codex_event_summary(line)["native_startup_failure"]:
                    failure_stopped = True
                    if process.poll() is None:
                        _terminate_process_tree(process)
            if len(pending) > _MAX_SESSION_OUTPUT_BYTES:
                pending = b""

    reader = threading.Thread(target=collect, name="hve-codex-eval-output", daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=max(0.1, timeout_seconds))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    reader.join(timeout=3)
    if reader.is_alive() and process.stdout is not None:
        try:
            process.stdout.close()
        except OSError:
            pass
        reader.join(timeout=1)

    output_bytes = bytes(captured)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output_bytes)
    events = _codex_event_summary(output_bytes, fallback_session_id=session_id)
    if timed_out:
        status = "timed-out"
    elif process.returncode != 0:
        status = "failed"
    elif not events["turn_completed"]:
        status = "missing-completion-event"
    else:
        status = "completed"
    if truncated:
        status = "output-limit"
    return {
        "status": status,
        "returncode": process.returncode,
        "session_id": events["session_id"],
        "event_types": events["event_types"],
        "turn_completed_event": events["turn_completed"],
        "native_startup_failure": events["native_startup_failure"],
        "unparsed_event_lines": events["unparsed_lines"],
        "usage_observations": events["usage_observations"],
        "observed_models": events["observed_models"],
        "requested_model": model,
        "requested_reasoning_effort": reasoning_effort,
        "output_bytes": len(output_bytes),
        "output_sha256": digest(output_bytes),
        "output_truncated": truncated,
        "output_file": output_path.name,
        "prompt_sha256": digest(prompt.encode("utf-8")),
    }




def _evaluation_paths(output: Path) -> tuple[Path, Path]:
    destination = Path(output).expanduser().resolve()
    report_path = destination if destination.suffix.casefold() == ".json" else destination / "evaluations-report.json"
    evidence_dir = report_path.parent / f"{report_path.stem}.sessions"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    parent = report_path.parent.resolve()
    try:
        report_path = safe_path(parent, report_path.name)
        evidence_dir = safe_path(parent, evidence_dir.name)
    except PortError as exc:
        raise PortError(f"Evaluation report output is unsafe: {exc}") from exc
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return report_path, evidence_dir


def _evaluation_workspace_parent(
    report_path: Path,
    *,
    is_windows: bool | None = None,
) -> Path | None:
    """Keep native Windows eval workspaces under the run's owned output root."""
    native_windows = _is_native_windows() if is_windows is None else is_windows
    if not native_windows:
        return None

    output_root = report_path.parent.resolve()
    try:
        workspace_parent = safe_path(output_root, ".workspaces")
    except PortError as exc:
        raise PortError(f"Evaluation workspace output is unsafe: {exc}") from exc
    workspace_parent.mkdir(parents=True, exist_ok=True)
    return workspace_parent








def _scenario_workspace_priority_paths(
    scenario: dict[str, Any],
    fixture_baseline: dict[str, str],
) -> list[str]:
    """Return bounded exact paths whose evidence has scenario-level value."""
    prioritized = set(fixture_baseline)
    supported_kinds = {
        "workspace_file_exists",
        "workspace_file_contains",
        "workspace_file_unchanged",
        "workspace_json_value",
        "fixture_hash_at_turn",
    }
    checks = scenario.get("deterministic_checks", [])
    if not isinstance(checks, list):
        checks = []
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in supported_kinds:
            continue
        try:
            relative = "/".join(_native_relative_path_parts(check.get("path")))
        except PortError:
            continue
        prioritized.add(relative)
    return sorted(prioritized)


def _workspace_snapshot(
    workspace: Path,
    *,
    native_command: str | None = None,
    native_sandbox_mode: str | None = None,
    priority_paths: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Hash regular workspace files, recording listing errors and using a scoped native fallback."""
    snapshot: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    root = Path(workspace).resolve()

    def walk_error(exc: OSError) -> None:
        raw_path = getattr(exc, "filename", None)
        try:
            relative = Path(raw_path).relative_to(root).as_posix() if raw_path else "."
        except (OSError, ValueError):
            relative = "."
        errors.append({"path": relative, "reason": (str(exc) or type(exc).__name__)[:500]})

    for directory, dirnames, filenames in os.walk(root, topdown=True, onerror=walk_error, followlinks=False):
        current = Path(directory)
        retained_dirs = []
        for name in sorted(dirnames):
            if name == ".git":
                continue
            path = current / name
            try:
                if not _is_link_or_reparse(path):
                    retained_dirs.append(name)
            except OSError as exc:
                errors.append({"path": path.relative_to(root).as_posix(), "reason": str(exc)[:500]})
        dirnames[:] = retained_dirs
        for name in sorted(filenames):
            path = current / name
            try:
                if _is_link_or_reparse(path):
                    continue
                relative = path.relative_to(root).as_posix()
                safe_path(root, relative)
                file_hash = hashlib.sha256()
                size = 0
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(64 * 1024), b""):
                        file_hash.update(chunk)
                        size += len(chunk)
                snapshot[relative] = {"sha256": file_hash.hexdigest(), "bytes": size}
            except (OSError, PortError, ValueError) as exc:
                errors.append({"path": path.relative_to(root).as_posix(), "reason": str(exc)[:500]})

    if errors and _is_native_windows() and native_command and native_sandbox_mode in {"elevated", "unelevated"}:
        response, response_error = _native_workspace_response(
            root,
            native_command,
            native_sandbox_mode,
            "inventory",
            None,
        )
        if response is None:
            errors.append({
                "path": ".",
                "reason": f"Native sandbox inventory fallback failed: {response_error or 'unknown error'}",
            })
            return snapshot, errors

        native_snapshot: dict[str, dict[str, Any]] = {}
        native_errors: list[dict[str, str]] = []
        native_total_bytes = 0
        raw_files = response.get("files", [])
        if len(raw_files) > _MAX_NATIVE_WORKSPACE_FILES:
            native_errors.append({"path": ".", "reason": "Native workspace inventory exceeded its file limit."})
        else:
            for record in raw_files:
                if not isinstance(record, dict):
                    native_errors.append({"path": ".", "reason": "Native workspace inventory contained a malformed file record."})
                    continue
                try:
                    relative = _validate_native_attested_path(root, record.get("path"))
                except PortError as exc:
                    native_errors.append({"path": ".", "reason": str(exc)})
                    continue
                size = record.get("bytes")
                file_hash = record.get("sha256")
                if (
                    isinstance(size, bool)
                    or not isinstance(size, int)
                    or size < 0
                    or size > _MAX_NATIVE_WORKSPACE_BYTES - native_total_bytes
                    or not isinstance(file_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", file_hash) is None
                ):
                    native_errors.append({"path": relative, "reason": "Native workspace inventory contained invalid file metadata."})
                    continue
                if relative in native_snapshot:
                    native_errors.append({"path": relative, "reason": "Native workspace inventory returned a duplicate path."})
                    continue
                native_total_bytes += size
                native_snapshot[relative] = {"sha256": file_hash, "bytes": size}

        for item in response.get("errors", []):
            if not isinstance(item, dict):
                native_errors.append({"path": ".", "reason": "Native workspace inventory contained a malformed error record."})
                continue
            raw_error_path = item.get("path")
            if isinstance(raw_error_path, str) and raw_error_path in {"", ".", "<workspace>"}:
                relative_error_path = "."
            else:
                try:
                    relative_error_path = _validate_native_attested_path(root, raw_error_path)
                except PortError:
                    relative_error_path = "."
                    native_errors.append({"path": ".", "reason": "Native workspace inventory returned an invalid error path."})
                    continue
            reason = item.get("reason")
            native_errors.append({
                "path": relative_error_path,
                "reason": str(reason)[:500] if reason is not None else "Native workspace inventory reported an access error.",
            })

        if response.get("complete") is not True and not native_errors:
            native_errors.append({"path": ".", "reason": "Native workspace inventory did not complete."})
        prioritized: list[str] = []
        for candidate in priority_paths or []:
            try:
                relative = "/".join(_native_relative_path_parts(candidate))
            except PortError:
                native_errors.append({"path": ".", "reason": "Workspace priority paths contained an unsafe relative path."})
                continue
            if relative not in prioritized:
                prioritized.append(relative)
        if len(prioritized) > _MAX_CHECKPOINT_FILES:
            native_errors.append({
                "path": ".",
                "reason": "Workspace priority paths exceeded the existing checkpoint file-count limit.",
            })
            prioritized = prioritized[:_MAX_CHECKPOINT_FILES]

        # Preserve directly hashed host files when the native walk was truncated.
        # For denied paths, use the existing bounded native read helper. Missing
        # targets stay absent and the read error keeps the inventory incomplete.
        prioritized_missing = [path for path in prioritized if path not in native_snapshot]
        for relative in prioritized_missing:
            host_record = snapshot.get(relative)
            if host_record is not None:
                native_snapshot[relative] = host_record
                native_errors.append({
                    "path": relative,
                    "reason": "Native inventory omitted a prioritized host-readable file; exact host evidence was retained.",
                })
        native_read_paths = [path for path in prioritized_missing if path not in snapshot]
        if native_read_paths and (response.get("complete") is not True or native_errors):
            native_data, read_errors = _native_workspace_read_files(
                root,
                native_read_paths,
                native_command,
                native_sandbox_mode,
            )
            native_errors.extend(read_errors)
            total_priority_bytes = 0
            for relative, content in native_data.items():
                if (
                    relative not in native_read_paths
                    or not isinstance(content, bytes)
                    or len(content) > _MAX_CHECKPOINT_FILE_BYTES
                    or total_priority_bytes + len(content) > _MAX_CHECKPOINT_TOTAL_BYTES
                ):
                    native_errors.append({
                        "path": relative if isinstance(relative, str) else ".",
                        "reason": "Native priority read returned unexpected or oversized file content.",
                    })
                    continue
                total_priority_bytes += len(content)
                native_snapshot[relative] = {
                    "sha256": digest(content),
                    "bytes": len(content),
                }
            returned_paths = set(native_data)
            errored_paths = {item.get("path") for item in read_errors if isinstance(item, dict)}
            for relative in native_read_paths:
                if relative not in returned_paths and relative not in errored_paths:
                    native_errors.append({
                        "path": relative,
                        "reason": "Native priority read did not establish the requested file contents.",
                    })
        for relative, record in snapshot.items():
            native_record = native_snapshot.get(relative)
            if native_record is None:
                native_errors.append({"path": relative, "reason": "Native inventory omitted a host-readable workspace file."})
            elif native_record != record:
                native_errors.append({"path": relative, "reason": "Native and host workspace hashes differed during inventory."})
        if response.get("truncated") is True and not any("limit" in item["reason"].casefold() for item in native_errors):
            native_errors.append({"path": ".", "reason": "Native workspace inventory was truncated."})
        return native_snapshot, native_errors
    return snapshot, errors




def _capture_workspace_checkpoint(
    workspace: Path,
    baseline: dict[str, dict[str, Any]],
    checkpoint_root: Path,
    fixture_baseline: dict[str, str],
    *,
    native_command: str | None = None,
    native_sandbox_mode: str | None = None,
    priority_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Retain changed artifacts and fixture hashes at a prompt boundary."""
    current, errors = _workspace_snapshot(
        workspace,
        native_command=native_command,
        native_sandbox_mode=native_sandbox_mode,
        priority_paths=priority_paths,
    )
    inventory_complete = not errors
    changed_paths = [
        relative
        for relative, record in sorted(current.items())
        if relative not in baseline or record["sha256"] != baseline[relative]["sha256"]
    ]
    missing_paths = sorted(set(baseline) - set(current))
    deleted_paths = missing_paths if inventory_complete else []
    unknown_paths = [] if inventory_complete else missing_paths
    retained_bytes = 0
    reserved_bytes = 0
    retained: list[dict[str, Any]] = []
    deferred: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    complete = not errors
    for relative in changed_paths:
        record = current[relative]
        status = "created" if relative not in baseline else "modified"
        item: dict[str, Any] = {
            "path": relative,
            "status": status,
            "before_sha256": baseline.get(relative, {}).get("sha256"),
            "after_sha256": record["sha256"],
            "bytes": record["bytes"],
        }
        if len(retained) + len(deferred) >= _MAX_CHECKPOINT_FILES:
            item.update({"retained": False, "reason": "checkpoint file-count limit exceeded"})
            complete = False
        elif record["bytes"] > _MAX_CHECKPOINT_FILE_BYTES:
            item.update({"retained": False, "reason": "file exceeds checkpoint file-size limit"})
            complete = False
        elif reserved_bytes + record["bytes"] > _MAX_CHECKPOINT_TOTAL_BYTES:
            item.update({"retained": False, "reason": "checkpoint total-size limit exceeded"})
            complete = False
        else:
            reserved_bytes += record["bytes"]
            target: Path | None = None
            try:
                target = safe_path(checkpoint_root, relative)
                source = safe_path(workspace, relative)
                with source.open("rb") as stream:
                    data = stream.read(_MAX_CHECKPOINT_FILE_BYTES + 1)
                if len(data) > _MAX_CHECKPOINT_FILE_BYTES:
                    raise ValueError("File grew beyond the checkpoint file-size limit while being read")
            except OSError as exc:
                if target is None:
                    item.update({"retained": False, "reason": str(exc)[:500]})
                    complete = False
                    retained.append(item)
                else:
                    deferred.append((item, record, target))
                    item["read_error"] = str(exc)[:300]
                continue
            except (PortError, ValueError) as exc:
                item.update({"retained": False, "reason": str(exc)[:500]})
                complete = False
                retained.append(item)
                continue
            if digest(data) != record["sha256"]:
                item.update({"retained": False, "reason": "File changed while checkpoint evidence was being captured"})
                complete = False
            else:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    item.update({
                        "retained": True,
                        "evidence_path": target.relative_to(checkpoint_root.parents[2]).as_posix(),
                    })
                    retained_bytes += len(data)
                except (OSError, ValueError) as exc:
                    item.update({"retained": False, "reason": str(exc)[:500]})
                    complete = False
        retained.append(item)

    if deferred:
        native_data: dict[str, bytes] = {}
        native_read_errors: list[dict[str, str]] = []
        if _is_native_windows() and native_command and native_sandbox_mode in {"elevated", "unelevated"}:
            native_data, native_read_errors = _native_workspace_read_files(
                workspace,
                [item[0]["path"] for item in deferred],
                native_command,
                native_sandbox_mode,
            )
        if native_read_errors:
            errors.extend(native_read_errors)
            complete = False
        for item, record, target in deferred:
            data = native_data.get(item["path"])
            if data is None:
                item.update({
                    "retained": False,
                    "reason": "Host could not read the file and native sandbox evidence was unavailable or incomplete.",
                })
                complete = False
            elif digest(data) != record["sha256"]:
                item.update({"retained": False, "reason": "Native file content did not match the inventoried workspace hash."})
                complete = False
            else:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    item.update({
                        "retained": True,
                        "evidence_path": target.relative_to(checkpoint_root.parents[2]).as_posix(),
                    })
                    retained_bytes += len(data)
                except (OSError, ValueError) as exc:
                    item.update({"retained": False, "reason": str(exc)[:500]})
                    complete = False
            retained.append(item)

    fixture_checks = []
    for relative, expected_hash in sorted(fixture_baseline.items()):
        actual = current.get(relative, {}).get("sha256")
        if actual == expected_hash:
            status = "unchanged"
        elif actual is not None:
            status = "modified"
        elif inventory_complete:
            status = "deleted"
        else:
            status = "unknown"
        fixture_checks.append({
            "path": relative,
            "expected_sha256": expected_hash,
            "actual_sha256": actual,
            "status": status,
            **({
                "reason": "Workspace inventory was incomplete; absence cannot be established."
            } if status == "unknown" else {}),
        })
    try:
        checkpoint_relative = checkpoint_root.relative_to(checkpoint_root.parents[2]).as_posix()
    except ValueError:
        checkpoint_relative = checkpoint_root.name
    return {
        "status": "complete" if complete else "incomplete",
        "inventory_status": "complete" if inventory_complete else "incomplete",
        "directory": checkpoint_relative,
        "changed_files": retained,
        "deleted_files": deleted_paths,
        "unknown_paths": unknown_paths,
        "fixture_hashes": fixture_checks,
        "retained_bytes": retained_bytes,
        "errors": errors,
    }






def _apply_checkpoint_assertions(
    scenario: dict[str, Any],
    checks: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate exact staged-fixture hashes at the declared prompt boundary."""
    by_id = {item.get("check_id"): index for index, item in enumerate(checks)}
    for definition in scenario["deterministic_checks"]:
        if not isinstance(definition, dict) or definition.get("kind") != "fixture_hash_at_turn":
            continue
        check_id = str(definition.get("id") or definition.get("kind"))
        result_index = by_id.get(check_id)
        if result_index is None:
            continue
        result = checks[result_index]
        prompt_number = definition.get("after_prompt")
        path = str(definition.get("path", "")).replace("\\", "/")
        expected_status = definition.get("expected_status")
        if isinstance(prompt_number, bool) or not isinstance(prompt_number, int) or prompt_number < 1 or expected_status not in {"unchanged", "modified", "deleted"}:
            checks[result_index] = {**result, "status": "unsupported", "reason": "fixture_hash_at_turn requires a positive after_prompt and a supported expected_status."}
            continue
        turn = next((item for item in turns if item.get("prompt_index") == prompt_number - 1), None)
        checkpoint = turn.get("workspace_checkpoint") if isinstance(turn, dict) else None
        fixture = next(
            (item for item in checkpoint.get("fixture_hashes", []) if item.get("path") == path),
            None,
        ) if isinstance(checkpoint, dict) else None
        if fixture is None:
            checks[result_index] = {
                **result,
                "status": "pending-runtime",
                "reason": f"Prompt {prompt_number} has no retained fixture checkpoint.",
                "path": path,
                "after_prompt": prompt_number,
            }
            continue
        if not isinstance(turn, dict) or turn.get("status") != "completed":
            turn_status = turn.get("status", "missing") if isinstance(turn, dict) else "missing"
            checks[result_index] = {
                **result,
                "status": "pending-runtime",
                "reason": f"Prompt {prompt_number} did not complete; its fixture checkpoint cannot count as a behavioral assertion.",
                "path": path,
                "after_prompt": prompt_number,
                "turn_status": turn_status,
            }
            continue
        actual_status = fixture.get("status")
        if actual_status not in {"unchanged", "modified", "deleted"}:
            checks[result_index] = {
                **result,
                "status": "pending-runtime",
                "reason": fixture.get("reason") or "The fixture checkpoint did not establish an authoritative file status.",
                "path": path,
                "after_prompt": prompt_number,
                "expected_status": expected_status,
                "actual_status": actual_status,
            }
            continue
        checks[result_index] = {
            **result,
            "status": "passed" if actual_status == expected_status else "failed",
            "path": path,
            "after_prompt": prompt_number,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "expected_sha256": fixture.get("expected_sha256"),
            "actual_sha256": fixture.get("actual_sha256"),
        }
    return checks




def _validated_budgets(max_sessions: int, timeout_seconds: int) -> None:
    if isinstance(max_sessions, bool) or not isinstance(max_sessions, int) or max_sessions < 1:
        raise PortError("max_sessions must be a positive integer")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise PortError("timeout_seconds must be a positive integer")
    if max_sessions > 1000 or timeout_seconds > 24 * 60 * 60:
        raise PortError("Evaluation budgets exceed the supported safety limits")


def _validated_model_settings(model: str, reasoning_effort: str) -> tuple[str, str]:
    if not isinstance(model, str) or not model.strip() or len(model.strip()) > 128 or any(ord(char) < 32 for char in model):
        raise PortError("model must be a non-empty model identifier of at most 128 characters")
    if not isinstance(reasoning_effort, str) or reasoning_effort not in _SUPPORTED_REASONING_EFFORTS:
        raise PortError("reasoning_effort must be one of: none, low, medium, high, xhigh, max")
    return model.strip(), reasoning_effort


def _codex_host_scenario_data(path: Path) -> dict[str, Any]:
    """Load a host-test definition and reject legacy workflow evaluations."""
    data = _scenario_data(path)
    if data.get("scope") != "codex-host":
        raise PortError("host runtime accepts only scope=codex-host definitions; use Vally for workflow behavior")
    return data


def prepare_evaluations(
    distribution: Path,
    scenario_file: Path,
    output: Path,
) -> dict[str, Any]:
    """Install and stage curated scenarios in temporary projects without Codex execution."""
    scenarios_path = Path(scenario_file).expanduser().resolve()
    data = _codex_host_scenario_data(scenarios_path)
    distribution_root = Path(distribution).expanduser().resolve()
    report_path, _evidence_dir = _evaluation_paths(output)
    scenario_reports: list[dict[str, Any]] = []
    all_checks: list[dict[str, Any]] = []
    fixture_errors: list[dict[str, Any]] = []
    unresolved_entrypoints: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hve-codex-prepare-") as temporary:
        for scenario in data["scenarios"]:
            scenario_root = Path(temporary) / _slug_for_path(scenario["id"])
            scenario_root.mkdir(parents=True, exist_ok=True)
            install_result: dict[str, Any] | None = None
            setup_errors: list[dict[str, Any]] = []
            try:
                install_result = _install_scenario(distribution_root, scenario_root)
            except Exception as exc:
                setup_errors.append({"code": "distribution-install-error", "message": str(exc)[:1000]})
            fixtures, errors = _stage_fixtures(scenario, scenario_root) if not setup_errors else ([], setup_errors)
            fixture_errors.extend({"scenario_id": scenario["id"], **error} for error in errors)
            resolution = _resolve_entrypoints(scenario, scenario_root)
            if resolution["status"] == "unresolved":
                unresolved_entrypoints.append(scenario["id"])
            baseline = _fixture_baseline(scenario, scenario_root)
            checks = _scenario_checks(scenario, scenario_root, distribution_root, baseline, execute_postchecks=False)
            all_checks.extend(checks)
            scenario_reports.append(_scenario_report(
                scenario,
                fixtures=fixtures,
                fixture_errors=errors,
                checks=checks,
                turns=[],
                runtime_status="not-run-prepare-only",
                install_result=install_result,
                entrypoint_resolution=resolution,
                distribution=distribution_root,
            ))
    passed = sum(item.get("status") == "passed" for item in all_checks)
    failed = sum(item.get("status") == "failed" for item in all_checks)
    pending = sum(item.get("status") == "pending-runtime" for item in all_checks)
    unsupported = sum(item.get("status") == "unsupported" for item in all_checks)
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_mode": "prepare-only-no-model-sessions",
        "distribution_exists": distribution_root.is_dir(),
        "scenario_source": scenarios_path.name,
        "scenario_definition_sha256": digest(scenarios_path.read_bytes()),
        "execution": {
            "model_sessions_started": 0,
            "upstream_scripts_executed": False,
            "scenario_fixtures_staged": True,
            "fixture_staging_source": "curated scenario JSON only",
        },
        "summary": {
            "scenarios_total": len(scenario_reports),
            "scenarios_prepared": sum(not item["fixture_errors"] for item in scenario_reports),
            "scenarios_with_fixture_errors": sum(bool(item["fixture_errors"]) for item in scenario_reports),
            "deterministic_checks_passed": passed,
            "deterministic_checks_failed": failed,
            "runtime_checks_pending": pending,
            "unsupported_checks": unsupported,
        },
        "scenarios": scenario_reports,
        "host_evidence": {
            "cli": {"status": "not-run", "reason": "Preparation does not invoke the Codex CLI."},
            "ide": {"status": "missing", "reason": "No live IDE session evidence was collected."},
        },
        "stable_blockers": [
            {"code": "behavioral-runtime-not-executed", "message": "Preparation only; no Codex model sessions were run."},
            {"code": "manual-rubric-review-pending", "message": "Human review of behavioral evidence is required."},
            {"code": "ide-runtime-evidence-missing", "message": "Codex IDE behavior has not been validated in a live IDE session."},
        ],
        "stable_ready": False,
        "success": False,
    }
    if unresolved_entrypoints:
        report["stable_blockers"].append({
            "code": "scenario-entrypoint-resolution-failed",
            "message": "Installed aliases did not resolve actual scenario entrypoints: " + ", ".join(unresolved_entrypoints),
        })
    report["report_path"] = report_path.name
    dump_json(report_path, report)
    return report


class _EvaluationTemporaryDirectory:
    """Capture cleanup failures after a completed run so its report can be saved."""

    def __init__(self, **options: Any) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(**options)
        self.cleanup_error: OSError | None = None

    def __enter__(self) -> str:
        return self._temporary_directory.__enter__()

    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> bool:
        try:
            return bool(self._temporary_directory.__exit__(exception_type, exception, traceback))
        except OSError as cleanup_error:
            if exception_type is None:
                self.cleanup_error = cleanup_error
            # Preserve any evaluation exception; cleanup is best-effort and is
            # recorded only when the evaluation body itself completed.
            return False


def run_evaluations(
    distribution: Path,
    scenario_file: Path,
    output: Path,
    codex_command: str = "codex",
    max_sessions: int = 20,
    timeout_seconds: int = 1800,
    *,
    model: str = DEFAULT_EVAL_MODEL,
    reasoning_effort: str = DEFAULT_EVAL_REASONING_EFFORT,
    isolate_computer_use: bool = False,
    persistent_stage: Path | None = None,
) -> dict[str, Any]:
    """Explicitly run bounded Codex sessions in disposable installer-staged projects.

    This function is the execution boundary. Importing or preparing evaluations
    never invokes Codex; calling this function probes the named CLI and, when its
    JSON, sandbox, explicit model/config, and resume capabilities are present,
    spends the caller's configured session and wall-clock budget. Native-hook
    cases are staged but blocked until reviewed trust and host evidence can be
    completed in a persistent project.
    """
    _validated_budgets(max_sessions, timeout_seconds)
    model, reasoning_effort = _validated_model_settings(model, reasoning_effort)
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    scenarios_path = Path(scenario_file).expanduser().resolve()
    data = _codex_host_scenario_data(scenarios_path)
    distribution_root = Path(distribution).expanduser().resolve()
    stage_receipt = None
    if persistent_stage is not None:
        persistent_stage = Path(persistent_stage).expanduser().resolve()
        stage_receipt = json.loads(safe_path(persistent_stage, "acceptance-stage.json").read_text(encoding="utf-8"))
        if stage_receipt.get("scenario_definition_sha256") != digest(scenarios_path.read_bytes()):
            raise PortError("Persistent stage scenario definitions changed; preserve it and stage a new fixture")
        selected = [scenario for scenario in data["scenarios"] if scenario["id"] == stage_receipt.get("scenario_id")]
        if len(selected) != 1 or stage_receipt.get("fixture_errors"):
            raise PortError("Persistent stage does not identify one valid scenario")
        project = safe_path(persistent_stage, "project")
        if str(project.resolve()) != stage_receipt.get("project"):
            raise PortError("Persistent stage project path changed; review the new path before execution")
        if _fixture_baseline(selected[0], project) != stage_receipt.get("baseline_hashes"):
            raise PortError("Persistent fixture inputs changed; no files were overwritten")
        provenance = json.loads(safe_path(distribution_root, "generated/provenance.json").read_text(encoding="utf-8"))
        if stage_receipt.get("provenance") != provenance:
            raise PortError("Persistent stage provenance differs from the selected distribution")
        data = {**data, "scenarios": selected}
    report_path, evidence_dir = _evaluation_paths(output)
    cli_probe = _probe_codex(codex_command, timeout_seconds, deadline=deadline)
    agent_default_capability = _agent_default_config_capability(cli_probe.get("version"))
    cli_probe["agent_default_config_compatibility"] = agent_default_capability
    supports_agent_default_config = agent_default_capability["status"] == "supported"
    executable, executable_error = _codex_executable(codex_command)
    windows_sandbox_mode, windows_sandbox_config_error = _existing_windows_sandbox_mode()
    native_windows = _is_native_windows()
    features = cli_probe.get("features", {})
    cli_ready = bool(
        executable
        and cli_probe.get("exec_help_available")
        and features.get("json_events")
        and features.get("workspace_write_sandbox")
        and features.get("model_override")
        and features.get("config_override")
    )
    can_resume = bool(
        features.get("resume")
        and features.get("resume_model_override")
        and features.get("resume_config_override")
    )
    if not native_windows:
        windows_sandbox_preflight = {
            "method": "app-server/windowsSandbox/readiness",
            "status": "not-applicable",
            "readiness_status": None,
            "model_turns_started": 0,
            "reason": None,
        }
    elif windows_sandbox_config_error:
        windows_sandbox_preflight = {
            "method": "app-server/windowsSandbox/readiness",
            "status": "blocked-config-mode-unavailable",
            "readiness_status": None,
            "model_turns_started": 0,
            "reason": windows_sandbox_config_error,
        }
    else:
        windows_sandbox_preflight = _probe_windows_sandbox_readiness(
            executable,
            windows_sandbox_mode,
            min(_WINDOWS_SANDBOX_PREFLIGHT_TIMEOUT_SECONDS, max(0.0, deadline - time.monotonic())),
        )
    scenario_reports: list[dict[str, Any]] = []
    all_checks: list[dict[str, Any]] = []
    fixture_errors: list[dict[str, Any]] = []
    unresolved_entrypoints: list[str] = []
    native_trust_scenarios: list[str] = []
    requested_model_mismatch_detected = False
    sessions_started = 0
    sessions_attempted = 0
    turn_processes_attempted = 0
    resume_missing = False
    checkpoint_evidence_incomplete = False
    native_startup_failure_detected = False

    temporary_options: dict[str, Any] = {"prefix": "hve-codex-eval-"}
    workspace_parent = _evaluation_workspace_parent(report_path)
    if workspace_parent is not None:
        temporary_options["dir"] = workspace_parent
    if native_windows:
        temporary_options["ignore_cleanup_errors"] = True
    stage_base: Path | None = None
    evaluation_workspace = None if persistent_stage else _EvaluationTemporaryDirectory(**temporary_options)
    with (nullcontext(str(persistent_stage)) if persistent_stage else evaluation_workspace) as temporary:
        stage_base = Path(temporary)
        for scenario in data["scenarios"]:
            scenario_id = scenario["id"]
            scenario_root = stage_base / ("project" if persistent_stage else _slug_for_path(scenario_id))
            scenario_root.mkdir(parents=True, exist_ok=True)
            install_result: dict[str, Any] | None = None
            setup_errors: list[dict[str, Any]] = []
            try:
                install_result = stage_receipt.get("full_installer") if stage_receipt else _install_scenario(distribution_root, scenario_root)
            except Exception as exc:
                setup_errors.append({"code": "distribution-install-error", "message": str(exc)[:1000]})
            fixtures, errors = (stage_receipt.get("fixtures_staged", []), []) if stage_receipt else (
                _stage_fixtures(scenario, scenario_root) if not setup_errors else ([], setup_errors))
            fixture_errors.extend({"scenario_id": scenario_id, **error} for error in errors)
            resolution = _resolve_entrypoints(scenario, scenario_root)
            if resolution["status"] == "unresolved":
                unresolved_entrypoints.append(scenario_id)
            native_check_kinds = sorted({
                str(item.get("kind"))
                for item in scenario.get("deterministic_checks", [])
                if isinstance(item, dict) and item.get("kind") in _NATIVE_TRUST_CHECKS
            })
            native_host_preflight = None
            if native_check_kinds:
                native_trust_scenarios.append(scenario_id)
                native_host_preflight = {
                    "status": "blocked-manual-persistent-stage-required",
                    "required": True,
                    "required_native_checks": native_check_kinds,
                    "trust_verified": False,
                    "reason": (
                        "The temporary eval runner cannot present a persistent native-hook trust review and "
                        "retain host evidence before execution. Stage this case with eval stage, review and trust "
                        "the hooks in the native client, then collect evidence there."
                    ),
                }
            if persistent_stage:
                from .host import probe_existing_project
                readiness = probe_existing_project(scenario_root, executable, require_hooks=bool(native_check_kinds)) if executable else {"ready": False}
                if readiness.get("ready"):
                    native_host_preflight = {"status": "ready", **readiness}
                    if scenario_id in native_trust_scenarios:
                        native_trust_scenarios.remove(scenario_id)
                else:
                    native_host_preflight = {"status": "blocked-persistent-readiness", **readiness}
                    if scenario_id not in native_trust_scenarios:
                        native_trust_scenarios.append(scenario_id)
            model_settings = None
            model_settings_error = None
            if stage_receipt:
                model_settings = stage_receipt.get("staged_model_settings")
                if not isinstance(model_settings, dict) or model_settings.get("requested_model") != model or model_settings.get("requested_reasoning_effort") != reasoning_effort:
                    model_settings_error = "Persistent stage model settings differ; no settings were changed"
                else:
                    for profile in (scenario_root / ".codex/agents").glob("*.toml"):
                        settings = tomllib.loads(safe_path(scenario_root, profile.relative_to(scenario_root).as_posix()).read_text(encoding="utf-8"))
                        if settings.get("model") != model or settings.get("model_reasoning_effort") != reasoning_effort:
                            model_settings_error = "Persistent agent profile model settings differ; no settings were changed"
                            break
            elif not setup_errors:
                try:
                    model_settings = _pin_staged_model_settings(
                        scenario_root,
                        model,
                        reasoning_effort,
                        supports_agent_default_config=supports_agent_default_config,
                        agent_default_config_status=agent_default_capability["status"],
                    )
                except (PortError, OSError) as exc:
                    model_settings_error = str(exc)
                    model_settings = {"status": "pin-failed", "error": model_settings_error}
            baseline = _fixture_baseline(scenario, scenario_root)
            stage_preconditions = _scenario_stage_preconditions(
                scenario,
                scenario_root,
                distribution_root,
                baseline,
            )
            priority_paths = _scenario_workspace_priority_paths(scenario, baseline)
            turns: list[dict[str, Any]] = []
            runtime_status = "not-started-cli-unavailable"
            repo_status: dict[str, Any] | None = None

            if native_startup_failure_detected:
                runtime_status = "not-started-native-cli-startup-failure"
            elif errors:
                runtime_status = "not-started-setup-error"
            elif resolution["status"] == "unresolved":
                runtime_status = "not-started-entrypoint-unresolved"
            elif native_host_preflight and not native_host_preflight.get("ready"):
                runtime_status = "not-started-native-hook-trust-pending"
            elif windows_sandbox_config_error:
                runtime_status = "not-started-windows-sandbox-config-unavailable"
            elif native_windows and windows_sandbox_preflight.get("status") != "ready":
                runtime_status = "not-started-windows-sandbox-readiness-unverified"
            elif model_settings_error:
                runtime_status = "not-started-model-settings-invalid"
            elif requested_model_mismatch_detected:
                runtime_status = "not-started-requested-model-mismatch"
            elif not cli_ready:
                runtime_status = "not-started-cli-incompatible"
            elif agent_default_capability["status"] == "unverified":
                runtime_status = "not-started-agent-default-config-unverified"
            elif sessions_attempted >= max_sessions:
                runtime_status = "not-started-session-budget-exhausted"
            elif time.monotonic() >= deadline:
                runtime_status = "not-started-time-budget-exhausted"
            else:
                repo_status = _init_isolated_repo(scenario_root, deadline - time.monotonic())
                if repo_status.get("status") != "initialized":
                    runtime_status = "not-started-project-unavailable"
                else:
                    runtime_status = "session-partial"
                    workspace_baseline, baseline_scan_errors = _workspace_snapshot(
                        scenario_root,
                        native_command=executable,
                        native_sandbox_mode=windows_sandbox_mode,
                        priority_paths=priority_paths,
                    )
                    session_id: str | None = None
                    complete_turns = 0
                    sessions_attempted += 1
                    for prompt_index, prompt in enumerate(resolution["prompts"]):
                        if prompt_index > 0 and not can_resume:
                            resume_missing = True
                            turns.append({
                                "prompt_index": prompt_index,
                                "status": "not-started-resume-unavailable",
                                "prompt_sha256": digest(prompt.encode("utf-8")),
                            })
                            continue
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            turns.append({
                                "prompt_index": prompt_index,
                                "status": "not-started-time-budget-exhausted",
                                "prompt_sha256": digest(prompt.encode("utf-8")),
                            })
                            break
                        if prompt_index > 0 and session_id is None:
                            turns.append({
                                "prompt_index": prompt_index,
                                "status": "not-started-session-id-missing",
                                "prompt_sha256": digest(prompt.encode("utf-8")),
                            })
                            break
                        turn_path = safe_path(
                            evidence_dir,
                            f"{_slug_for_path(scenario_id)}/turn-{prompt_index + 1:02}.jsonl",
                        )
                        turn_result = _execute_codex_turn(
                            executable,
                            scenario_root,
                            prompt,
                            session_id=session_id,
                            timeout_seconds=remaining,
                            output_path=turn_path,
                            model=model,
                            reasoning_effort=reasoning_effort,
                            windows_sandbox_mode=windows_sandbox_mode,
                            use_project_config=True,
                            supports_agent_default_config=supports_agent_default_config,
                            isolate_computer_use=isolate_computer_use,
                        )
                        turn_processes_attempted += 1
                        checkpoint_dir = safe_path(
                            evidence_dir,
                            f"{_slug_for_path(scenario_id)}/workspace/turn-{prompt_index + 1:02}",
                        )
                        checkpoint = _capture_workspace_checkpoint(
                            scenario_root,
                            workspace_baseline,
                            checkpoint_dir,
                            baseline,
                            native_command=executable,
                            native_sandbox_mode=windows_sandbox_mode,
                            priority_paths=priority_paths,
                        )
                        if baseline_scan_errors:
                            checkpoint["status"] = "incomplete"
                            checkpoint["baseline_errors"] = baseline_scan_errors
                        if checkpoint["status"] != "complete":
                            checkpoint_evidence_incomplete = True
                        turn_result["workspace_checkpoint"] = checkpoint
                        returned_session_id = turn_result.get("session_id")
                        if prompt_index == 0:
                            event_types = turn_result.get("event_types", [])
                            if turn_result.get("turn_completed_event") is True or (
                                isinstance(event_types, list) and "thread.started" in event_types
                            ):
                                sessions_started += 1
                            if isinstance(returned_session_id, str) and returned_session_id:
                                session_id = returned_session_id
                        elif isinstance(returned_session_id, str) and returned_session_id:
                            session_id = returned_session_id
                        turn_result["prompt_index"] = prompt_index
                        turn_result["output_file"] = turn_path.relative_to(evidence_dir).as_posix()
                        turn_result["session_id"] = session_id
                        turns.append(turn_result)
                        if turn_result.get("native_startup_failure") is True:
                            native_startup_failure_detected = True
                            turn_result["behavioral_status"] = "failed-native-cli-startup"
                            runtime_status = "session-stopped-native-cli-startup-failure"
                            break
                        observed_models = turn_result.get("observed_models", [])
                        if isinstance(observed_models, list) and any(
                            isinstance(observed, str) and observed != model for observed in observed_models
                        ):
                            requested_model_mismatch_detected = True
                            runtime_status = "session-stopped-observed-model-mismatch"
                            break
                        if turn_result.get("status") == "completed":
                            complete_turns += 1
                        else:
                            break
                        runtime_status = "session-complete" if complete_turns == len(resolution["prompts"]) else "session-partial"

            workspace_evidence = None
            if runtime_status == "session-complete":
                workspace_files, workspace_scan_errors = _workspace_snapshot(
                    scenario_root,
                    native_command=executable,
                    native_sandbox_mode=windows_sandbox_mode,
                    priority_paths=priority_paths,
                )
                workspace_evidence = {"files": workspace_files, "errors": workspace_scan_errors}
                if workspace_scan_errors:
                    checkpoint_evidence_incomplete = True
            checks = _scenario_checks(
                scenario,
                scenario_root,
                distribution_root,
                baseline,
                execute_postchecks=runtime_status == "session-complete",
                pending_reason=(
                    "The post-session check is withheld because all declared Codex prompt turns did not complete."
                ),
                stage_preconditions=stage_preconditions,
                workspace_evidence=workspace_evidence,
                native_command=executable,
                native_sandbox_mode=windows_sandbox_mode,
            )
            checks = _apply_checkpoint_assertions(scenario, checks, turns)
            all_checks.extend(checks)
            scenario_report = _scenario_report(
                scenario,
                fixtures=fixtures,
                fixture_errors=errors,
                checks=checks,
                turns=turns,
                runtime_status=runtime_status,
                install_result=install_result,
                entrypoint_resolution=resolution,
                distribution=distribution_root,
                model_settings=model_settings,
                native_host_preflight=native_host_preflight,
            )
            scenario_report["isolated_project"] = repo_status
            scenario_reports.append(scenario_report)

    cleanup_error = evaluation_workspace.cleanup_error if evaluation_workspace else None
    workspace_cleanup_status = "preserved-persistent" if persistent_stage else ("incomplete" if cleanup_error is not None else "verified-clean")
    workspace_cleanup_path = None
    if stage_base is not None and not persistent_stage:
        try:
            if os.path.lexists(stage_base):
                workspace_cleanup_status = "incomplete"
                workspace_cleanup_path = (
                    stage_base.relative_to(workspace_parent).as_posix()
                    if workspace_parent is not None and stage_base.is_relative_to(workspace_parent)
                    else stage_base.name
                )
        except OSError:
            workspace_cleanup_status = "incomplete" if cleanup_error is not None else "unverified"
            workspace_cleanup_path = stage_base.name

    passed_checks = sum(check.get("status") == "passed" for check in all_checks)
    failed_checks = sum(check.get("status") == "failed" for check in all_checks)
    pending_checks = sum(check.get("status") == "pending-runtime" for check in all_checks)
    unsupported_checks = sum(check.get("status") == "unsupported" for check in all_checks)
    runtime_complete = sum(item["behavioral_status"] == "session-complete" for item in scenario_reports)
    stable_blockers = [
        {"code": "manual-rubric-review-pending", "message": "Behavioral outputs are evidence only until a human reviews each scenario rubric."},
        {"code": "ide-runtime-evidence-missing", "message": "Codex IDE behavior has not been validated in a live IDE session."},
    ]
    if not distribution_root.is_dir():
        stable_blockers.append({"code": "distribution-missing", "message": "The selected distribution directory does not exist."})
    if not cli_ready:
        stable_blockers.append({"code": "codex-cli-feature-probe-failed", "message": executable_error or "CLI help did not confirm JSON events, workspace-write sandbox, and explicit model/config support."})
    if agent_default_capability["status"] == "unverified":
        stable_blockers.append({
            "code": "codex-cli-agent-default-config-unverified",
            "message": (
                "No model-free compatibility evidence is recorded for the CLI's global agent-default config fields; "
                "evaluation model turns were withheld. Add exact-version evidence before enabling them."
            ),
        })
    if resume_missing:
        stable_blockers.append({"code": "codex-cli-resume-unavailable", "message": "One or more multi-prompt scenarios could not continue the captured session."})
    if unresolved_entrypoints:
        stable_blockers.append({
            "code": "scenario-entrypoint-resolution-failed",
            "message": "Installed aliases did not resolve actual scenario entrypoints: " + ", ".join(unresolved_entrypoints),
        })
    if native_trust_scenarios:
        stable_blockers.append({
            "code": "native-hook-trust-preflight-required",
            "message": "Native-hook scenarios require persistent client trust review and host evidence before execution: " + ", ".join(native_trust_scenarios),
        })
    if windows_sandbox_config_error:
        stable_blockers.append({
            "code": "windows-native-sandbox-config-required",
            "message": windows_sandbox_config_error,
        })
    elif native_windows and windows_sandbox_preflight.get("status") != "ready":
        stable_blockers.append({
            "code": "windows-native-sandbox-readiness-required",
            "message": str(
                windows_sandbox_preflight.get("reason")
                or "Codex did not explicitly report the native Windows sandbox as ready."
            ),
        })
    if requested_model_mismatch_detected:
        stable_blockers.append({
            "code": "requested-model-mismatch",
            "message": "A JSONL event reported a model different from the requested model; subsequent turns and scenarios were not started.",
        })
    if native_startup_failure_detected:
        stable_blockers.append({
            "code": "native-cli-startup-failure",
            "message": (
                "A structured native command_execution item reported the host startup failure; "
                "subsequent prompt turns and scenarios were not started."
            ),
        })
    if any(
        isinstance(item.get("model_settings"), dict) and item["model_settings"].get("status") == "pin-failed"
        for item in scenario_reports
    ):
        stable_blockers.append({"code": "staged-model-settings-invalid", "message": "At least one scenario's disposable project model configuration could not be pinned safely."})
    if sessions_started < sessions_attempted or runtime_complete < len(scenario_reports):
        stable_blockers.append({"code": "codex-session-incomplete", "message": "At least one scenario did not complete all declared Codex prompt turns successfully."})
    if failed_checks:
        stable_blockers.append({"code": "deterministic-check-failures", "message": f"{failed_checks} deterministic check(s) failed."})
    if fixture_errors:
        stable_blockers.append({"code": "fixture-staging-errors", "message": f"{len(fixture_errors)} fixture(s) could not be staged safely."})
    if unsupported_checks:
        stable_blockers.append({"code": "unsupported-scenario-checks", "message": f"{unsupported_checks} check kind(s) have no deterministic evaluator."})
    if checkpoint_evidence_incomplete:
        stable_blockers.append({"code": "workspace-artifact-evidence-incomplete", "message": "At least one prompt checkpoint could not retain every changed artifact within the evidence limits."})
    if workspace_cleanup_status not in {"verified-clean", "preserved-persistent"}:
        stable_blockers.append({
            "code": "temporary-workspace-cleanup-incomplete",
            "message": (
                "The disposable evaluation workspace could not be confirmed clean after Windows cleanup. "
                f"Status: {workspace_cleanup_status}."
            ),
        })
    if native_windows:
        stable_blockers.append({
            "code": "windows-native-permission-context-unverified",
            "message": "The runner forwards the existing Windows sandbox implementation mode, but eval JSONL does not expose the effective native turn_context permission policy. Verify a native rollout context before treating Windows permission isolation as evidenced.",
        })
    usage_observations: list[dict[str, Any]] = []
    completed_turns = 0
    for scenario_report in scenario_reports:
        for turn in scenario_report.get("turns", []):
            if turn.get("turn_completed_event") is True:
                completed_turns += 1
            for observation in turn.get("usage_observations", []):
                if not isinstance(observation, dict):
                    continue
                normalized_observation = dict(observation)
                normalized_observation.setdefault("session_id", turn.get("session_id"))
                normalized_observation["scenario_id"] = scenario_report.get("id")
                usage_observations.append(normalized_observation)
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_mode": "bounded-codex-cli-sessions",
        "distribution_exists": distribution_root.is_dir(),
        "scenario_source": scenarios_path.name,
        "scenario_definition_sha256": digest(scenarios_path.read_bytes()),
        "budgets": {
            "max_sessions": max_sessions,
            "timeout_seconds": timeout_seconds,
            "declared_scenario_sessions": len(data["scenarios"]),
            "declared_prompt_turns": sum(len(item["prompts"]) for item in data["scenarios"]),
            "session_budget_covers_all_scenarios": max_sessions >= len(data["scenarios"]),
        },
        "execution": {
            "model_sessions_started": sessions_started,
            "sessions_attempted": sessions_attempted,
            "turn_processes_attempted": turn_processes_attempted,
            "upstream_scripts_executed": False,
            "scenario_fixtures_staged": True,
            "fixture_staging_source": "curated scenario JSON only",
            "full_installer_staged_per_scenario": not bool(persistent_stage),
            "persistent_fixture_reused": bool(persistent_stage),
            "installer_discovery_mode": "standalone-skills-no-plugin",
            "sandbox_requested": "workspace-write",
            "sandbox_configuration_source": "reviewed-disposable-project-default",
            "live_parent_sandbox_override": False,
            "project_trust_scope": "existing-exact-path-trusted-project-required",
            "project_trust_modified": False,
            "exec_user_config_isolated": False,
            "computer_use_isolation": "process-only-disabled" if isolate_computer_use else "not-requested",
            "windows_native_sandbox_mode_requested": windows_sandbox_mode,
            "approval_bypass_flags_passed": [],
            "model_selected_by_runner": model,
            "requested_model": model,
            "requested_reasoning_effort": reasoning_effort,
            "agent_default_config_compatibility": agent_default_capability,
            "generic_subagent_model_status": (
                "requested-model-default-pinned" if supports_agent_default_config else "unverified"
            ),
            "time_budget_enforced_monotonic": True,
            "temporary_workspace_cleanup": {
                "status": workspace_cleanup_status,
                "leftover_path": workspace_cleanup_path,
                "error": (
                    {
                        "type": type(cleanup_error).__name__,
                        "message": str(cleanup_error)[:1000],
                    }
                    if cleanup_error is not None else None
                ),
            },
        },
        "usage_accounting": summarize_usage(
            usage_observations,
            requested_model=model,
            requested_reasoning_effort=reasoning_effort,
            turns_completed=completed_turns,
        ),
        "codex_cli": cli_probe,
        "summary": {
            "scenarios_total": len(scenario_reports),
            "scenarios_prepared": sum(not item["fixture_errors"] for item in scenario_reports),
            "scenarios_with_fixture_errors": sum(bool(item["fixture_errors"]) for item in scenario_reports),
            "behavioral_sessions_completed": runtime_complete,
            "deterministic_checks_passed": passed_checks,
            "deterministic_checks_failed": failed_checks,
            "runtime_checks_pending": pending_checks,
            "unsupported_checks": unsupported_checks,
        },
        "scenarios": scenario_reports,
        "host_evidence": {
            "cli": {
                "status": "observed" if sessions_started else ("available-not-used" if cli_ready else "unavailable"),
                "sessions_started": sessions_started,
            },
            "ide": {"status": "missing", "reason": "Codex IDE behavior requires a separate live IDE validation."},
            "windows_native_sandbox": {
                "status": (
                    "blocked-config-mode-unavailable" if windows_sandbox_config_error else
                    windows_sandbox_preflight["status"] if native_windows and windows_sandbox_preflight.get("status") != "ready" else
                    "readiness-ready-effective-mode-unverified" if native_windows else "not-applicable"
                ),
                "requested_mode": windows_sandbox_mode,
                "effective_mode": "unverified" if native_windows else None,
                "readiness_status": windows_sandbox_preflight.get("readiness_status"),
                "readiness_probe": windows_sandbox_preflight.get("method"),
                "model_turns_started_by_preflight": windows_sandbox_preflight.get("model_turns_started", 0),
                "preflight_config_scope": (
                    "existing CODEX_HOME (or ~/.codex); app-server has no --ignore-user-config option in this CLI build"
                    if native_windows else None
                ),
                "app_server_user_config_isolated": False if native_windows else None,
                "exec_user_config_isolated": False if native_windows else None,
                "preflight_rpc_methods": (
                    ["initialize", "initialized", "windowsSandbox/readiness"] if native_windows else []
                ),
                "setup_or_turn_started_by_preflight": False,
                "reason": (
                    windows_sandbox_config_error if windows_sandbox_config_error else
                    windows_sandbox_preflight.get("reason")
                    if native_windows and windows_sandbox_preflight.get("status") != "ready" else
                    "The exec JSONL stream does not expose native turn_context permission metadata."
                    if native_windows else "The current platform does not use the native Windows sandbox."
                ),
            },
        },
        "stable_blockers": stable_blockers,
        "stable_ready": False,
        "success": False,
    }
    report["report_path"] = report_path.name
    dump_json(report_path, report)
    return report


def _slug_for_path(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return slug[:80] or "scenario"


__all__ = ["prepare_evaluations", "run_evaluations"]
