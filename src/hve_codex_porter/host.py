"""Explicit non-model host installation probe in a disposable Codex home."""
from __future__ import annotations
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import tempfile
import time
from pathlib import Path
from typing import Any
from .common import PortError, dump_json
from .installer import InstallerError, install


_APP_SERVER_TIMEOUT_SECONDS = 15.0
_APP_SERVER_MAX_LINE_BYTES = 128 * 1024
_APP_SERVER_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_APP_SERVER_MAX_STDERR_BYTES = 16 * 1024
_APP_SERVER_MAX_QUEUED_LINES = 32
_APP_SERVER_ALLOWED_METHODS = ("skills/list", "hooks/list", "config/read")
_RPC_EOF = object()


def probe_existing_project(project: Path, executable: str, *, require_hooks: bool = True) -> dict:
    """Inspect effective project loading and persisted hook trust without granting either."""
    project = project.resolve()
    configured_home = os.environ.get("CODEX_HOME")
    home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    with _AppServerClient(executable, project, home) as client:
        initialized = client.initialize()
        if not initialized.get("ok"):
            return {"ready": False, "reason": "initialization-failed", "model_sessions_started": 0}
        config = client.request("config/read", {"cwd": str(project), "includeLayers": True})
        hooks = client.request("hooks/list", {"cwds": [str(project)]})
    layers = config.get("result", {}).get("layers", []) if config.get("ok") else []
    project_layers = [layer for layer in layers if isinstance(layer, dict)
                      and isinstance(layer.get("name"), dict)
                      and _path_key(layer["name"].get("dotCodexFolder")) == _path_key(project / ".codex")]
    loaded = bool(project_layers) and all(not layer.get("disabledReason") for layer in project_layers)
    summary = _summarize_hooks(hooks.get("result", {}), project) if hooks.get("ok") else {}
    trusted = (summary.get("hve_count", 0) > 0
               and summary.get("hve_trust_status_counts") == {"trusted": summary.get("hve_count")}
               and summary.get("hve_enabled_counts") == {"true": summary.get("hve_count")})
    return {
        "ready": loaded and (trusted or not require_hooks),
        "project_config_loaded": loaded,
        "project_config_disabled_reasons": [layer["disabledReason"] for layer in project_layers if layer.get("disabledReason")],
        "hooks": summary,
        "hook_trust_verified": trusted,
        "model_sessions_started": 0,
        "trust_modified": False,
    }

def probe_plugin(distribution: Path, executable: str, output: Path | None = None) -> dict:
    distribution = distribution.resolve()
    if not (distribution / "plugins/hve-core-codex/plugin.json").is_file():
        raise PortError("Build the distribution before probing plugin support")
    steps = []
    with tempfile.TemporaryDirectory(prefix="hve-host-") as temporary:
        root = Path(temporary)
        home, project = root / "codex-home", root / "project"
        home.mkdir()
        project.mkdir()
        shutil.copytree(distribution / "plugins/hve-core-codex", project / "plugins/hve-core-codex")
        marketplace = project / ".agents/plugins"
        marketplace.mkdir(parents=True)
        shutil.copyfile(distribution / ".agents/plugins/marketplace.json", marketplace / "marketplace.json")
        env = dict(os.environ)
        env["CODEX_HOME"] = str(home)
        commands = [["--version"], ["plugin", "marketplace", "add", str(project), "--json"], ["plugin", "add", "hve-core-codex@hve-core-codex-local", "--json"], ["plugin", "list", "--json"]]
        for args in commands:
            completed = subprocess.run([executable, *args], cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            steps.append({"command": args[:3], "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
            if completed.returncode:
                break
        installed_files_match = False
        listed = False
        if len(steps) == 4 and all(s["exit_code"] == 0 for s in steps):
            installed = json.loads(steps[2]["stdout"])
            installed_path = Path(installed["installedPath"])
            expected = {p.relative_to(project / "plugins/hve-core-codex").as_posix(): p.read_bytes() for p in (project / "plugins/hve-core-codex").rglob("*") if p.is_file()}
            actual = {p.relative_to(installed_path).as_posix(): p.read_bytes() for p in installed_path.rglob("*") if p.is_file()}
            installed_files_match = all(actual.get(k) == v for k, v in expected.items())
            listing = json.loads(steps[3]["stdout"])
            listed = any(item.get("pluginId") == "hve-core-codex@hve-core-codex-local" and item.get("installed") and item.get("enabled") for item in listing.get("installed", []))
        result = {"schema_version": 1, "probe": "plugin-registration-and-installation", "passed": installed_files_match and listed, "installed_files_match": installed_files_match, "listed_after_install": listed, "model_sessions_started": 0, "steps": steps, "behavioral_validation": "not-run", "custom_agents": "not-probed", "hooks": "not-probed"}
    if output:
        dump_json(output, result)
    return result


def probe_discovery(distribution: Path, executable: str, output: Path | None = None) -> dict:
    """Install standalone artifacts in a disposable project and ask Codex what it discovers.

    The project config in the disposable home trusts only this temporary project so
    its local read-only configuration can be inspected. Hook definitions themselves
    are never trusted or executed. The only app-server requests sent are initialization, ``skills/list``,
    ``hooks/list``, and ``config/read``. The probe never creates a thread or starts a
    turn, and it never returns full host configuration, hook commands, or skill content.
    """
    distribution = Path(distribution).resolve()
    if not (distribution / "codex-overlay").is_dir() or not (
        distribution / "plugins/hve-core-codex/plugin.json"
    ).is_file():
        raise PortError("Build the distribution before probing Codex discovery")

    result: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="hve-discovery-") as temporary:
        root = Path(temporary)
        home = root / "codex-home"
        project = root / "project"
        home.mkdir()
        project.mkdir()

        installation: dict[str, Any]
        try:
            installation = install(distribution, project, scope="project", no_plugin=True)
        except (InstallerError, OSError, ValueError) as exc:
            result = _discovery_failure(f"standalone installation failed: {_bounded_text(str(exc))}")
        else:
            installation_ok = installation.get("status") == "installed" and not installation.get("conflicts")
            installed_skills = _expected_skill_paths(project)
            agent_files = _agent_files(project)
            install_summary = {
                "status": installation.get("status", "unknown"),
                "standalone_skills": len(installed_skills),
                "custom_agent_files": len(agent_files),
                "hooks_config_present": (project / ".codex" / "hooks.json").is_file(),
                "conflict_count": len(installation.get("conflicts", [])),
            }

            if not installation_ok:
                result = _discovery_failure("standalone installation reported conflicts", install_summary)
            else:
                methods: dict[str, dict[str, Any]] = {
                    "initialize": {"status": "not-run"},
                    "skills/list": {"status": "not-run"},
                    "hooks/list": {"status": "not-run"},
                    "config/read": {"status": "not-run"},
                }
                skill_summary: dict[str, Any] = {
                    "status": "not-run",
                    "expected_count": len(installed_skills),
                    "discovered_exact_count": 0,
                    "enabled_exact_count": 0,
                    "matched": [],
                    "disabled": [],
                    "missing": sorted(installed_skills),
                    "wrong_path": [],
                    "error_count": 0,
                }
                hook_summary: dict[str, Any] = {
                    "status": "not-run",
                    "count": 0,
                    "hve_count": 0,
                    "trust_status_counts": {},
                    "hve_trust_status_counts": {},
                    "enabled_counts": {},
                    "execution_tested": False,
                    "error_count": 0,
                    "warning_count": 0,
                    "errors": [],
                    "warnings": [],
                }
                agent_summary: dict[str, Any] = {
                    "files_on_disk": [path.name for path in agent_files],
                    "file_count": len(agent_files),
                    "reported_by_config_read": {"status": "not-run", "names": None},
                }
                host_summary: dict[str, Any] = {
                    "user_agent": None,
                    "version": None,
                    "platform_family": None,
                    "platform_os": None,
                }
                protocol_error: str | None = None
                process_returncode: int | None = None
                stderr_summary = ""
                project_config_trusted = False

                try:
                    _trust_disposable_project_config(home, project)
                    project_config_trusted = True
                    with _AppServerClient(executable, project, home) as client:
                        initialize = client.initialize()
                        if initialize.get("ok"):
                            methods["initialize"] = {"status": "ok"}
                            host_summary = _summarize_host(initialize.get("result"))
                            for method in _APP_SERVER_ALLOWED_METHODS:
                                params = _discovery_params(method, project)
                                response = client.request(method, params)
                                methods[method] = _method_summary(response)
                                if response.get("ok"):
                                    payload = response.get("result")
                                    if method == "skills/list":
                                        skill_summary = _summarize_skills(payload, project, installed_skills)
                                    elif method == "hooks/list":
                                        hook_summary = _summarize_hooks(payload, project)
                                    else:
                                        agent_summary["reported_by_config_read"] = _summarize_config_agents(payload)
                                elif method == "skills/list":
                                    skill_summary["status"] = response.get("status", "error")
                                elif method == "hooks/list":
                                    hook_summary["status"] = response.get("status", "error")
                                    hook_summary["hve_status"] = response.get("status", "error")
                                else:
                                    agent_summary["reported_by_config_read"] = {
                                        "status": response.get("status", "error"),
                                        "names": None,
                                    }
                                if response.get("fatal"):
                                    protocol_error = _bounded_text(str(response["fatal"]))
                                    break
                        else:
                            methods["initialize"] = _method_summary(initialize)
                            protocol_error = _bounded_text(str(initialize.get("message", "app-server initialization failed")))
                except (OSError, subprocess.SubprocessError, PortError, RuntimeError) as exc:
                    protocol_error = _bounded_text(str(exc)) or type(exc).__name__
                finally:
                    if "client" in locals():
                        process_returncode = client.returncode
                        stderr_summary = client.stderr_summary

                skills_ok = (
                    skill_summary.get("expected_count", 0) > 0
                    and skill_summary.get("error_count", 0) == 0
                    and skill_summary.get("status") == "listed"
                    and skill_summary.get("enabled_exact_count") == skill_summary.get("expected_count")
                )
                methods_ok = all(methods[name].get("status") == "ok" for name in _APP_SERVER_ALLOWED_METHODS)
                result = {
                    "schema_version": 1,
                    "probe": "codex-app-server-discovery",
                    "passed": installation_ok and methods_ok and skills_ok,
                    "model_sessions_started": 0,
                    "project_config_trusted_for_discovery": project_config_trusted,
                    "hook_definition_trust": "not-granted",
                    "installation": install_summary,
                    "methods": methods,
                    "host": host_summary,
                    "standalone_skills": skill_summary,
                    "hooks": hook_summary,
                    "custom_agents": agent_summary,
                    "process_returncode": process_returncode,
                    "protocol_error": protocol_error,
                    "stderr_summary": stderr_summary,
                }

    if output:
        dump_json(output, result)
    return result


def _discovery_failure(message: str, installation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "probe": "codex-app-server-discovery",
        "passed": False,
        "model_sessions_started": 0,
        "project_config_trusted_for_discovery": False,
        "hook_definition_trust": "not-granted",
        "installation": installation or {"status": "failed"},
        "methods": {
            "initialize": {"status": "not-run"},
            "skills/list": {"status": "not-run"},
            "hooks/list": {"status": "not-run"},
            "config/read": {"status": "not-run"},
        },
        "host": {"user_agent": None, "version": None, "platform_family": None, "platform_os": None},
        "standalone_skills": {"status": "not-run"},
        "hooks": {"status": "not-run"},
        "custom_agents": {"status": "not-run"},
        "protocol_error": message,
    }


def _expected_skill_paths(project: Path) -> dict[str, Path]:
    skills_root = project / ".agents" / "skills"
    if not skills_root.is_dir() or skills_root.is_symlink():
        return {}
    return {
        skill.name: skill / "SKILL.md"
        for skill in sorted(skills_root.iterdir(), key=lambda item: item.name.casefold())
        if skill.is_dir() and not skill.is_symlink() and (skill / "SKILL.md").is_file()
    }


def _agent_files(project: Path) -> list[Path]:
    agents_root = project / ".codex" / "agents"
    if not agents_root.is_dir() or agents_root.is_symlink():
        return []
    return sorted(
        (path for path in agents_root.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".toml"),
        key=lambda path: path.name.casefold(),
    )


def _trust_disposable_project_config(home: Path, project: Path) -> None:
    """Trust only the fresh probe project in its private Codex home."""
    import tomlkit

    project_key = os.path.normcase(str(project.resolve()))
    project_config = tomlkit.table()
    project_config["trust_level"] = "trusted"
    projects = tomlkit.table()
    projects[project_key] = project_config
    document = tomlkit.document()
    document["projects"] = projects
    (home / "config.toml").write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")


def _discovery_params(method: str, project: Path) -> dict[str, Any]:
    cwd = str(project.resolve())
    if method == "skills/list":
        return {"cwds": [cwd], "forceReload": True}
    if method == "hooks/list":
        return {"cwds": [cwd]}
    if method == "config/read":
        return {"cwd": cwd}
    raise PortError(f"Unexpected app-server discovery method: {method}")


def _summarize_skills(payload: Any, project: Path, expected: dict[str, Path]) -> dict[str, Any]:
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    project_key = _path_key(project)
    project_entries = [entry for entry in entries if isinstance(entry, dict) and _path_key(entry.get("cwd")) == project_key]
    skills = [skill for entry in project_entries for skill in entry.get("skills", []) if isinstance(skill, dict)]
    errors = [error for entry in project_entries for error in entry.get("errors", [])]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for skill in skills:
        name = skill.get("name")
        if isinstance(name, str) and name in expected:
            by_name.setdefault(name, []).append(skill)

    matched: list[dict[str, Any]] = []
    disabled: list[str] = []
    wrong_path: list[dict[str, Any]] = []
    missing: list[str] = []
    enabled_names: set[str] = set()
    for name, expected_path in expected.items():
        candidates = by_name.get(name, [])
        exact = [item for item in candidates if _path_key(item.get("path")) == _path_key(expected_path)]
        enabled_exact = [item for item in exact if item.get("enabled") is True]
        if enabled_exact:
            enabled_names.add(name)
            matched.append({"name": name, "path": str(expected_path), "enabled": True})
        elif exact:
            disabled.append(name)
            matched.append({"name": name, "path": str(expected_path), "enabled": False})
        else:
            missing.append(name)
            if candidates:
                wrong_path.append({
                    "name": name,
                    "observed_paths": sorted({str(item.get("path", "")) for item in candidates}),
                })

    return {
        "status": "listed",
        "expected_count": len(expected),
        "discovered_exact_count": len(matched),
        "enabled_exact_count": len(enabled_names),
        "matched": matched,
        "disabled": disabled,
        "missing": missing,
        "wrong_path": wrong_path,
        "error_count": len(errors),
    }


def _summarize_hooks(payload: Any, project: Path) -> dict[str, Any]:
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    project_key = _path_key(project)
    project_entries = [entry for entry in entries if isinstance(entry, dict) and _path_key(entry.get("cwd")) == project_key]
    hooks = [hook for entry in project_entries for hook in entry.get("hooks", []) if isinstance(hook, dict)]
    expected_path = _path_key(project / ".codex" / "hooks.json")
    hve_hooks = [hook for hook in hooks if _path_key(hook.get("sourcePath")) == expected_path]
    return {
        "status": "listed",
        "hve_status": "observed" if hve_hooks else "not-observed",
        "count": len(hooks),
        "hve_count": len(hve_hooks),
        "trust_status_counts": _value_counts(hook.get("trustStatus") for hook in hooks),
        "hve_trust_status_counts": _value_counts(hook.get("trustStatus") for hook in hve_hooks),
        "hve_enabled_counts": _value_counts(str(hook.get("enabled")).lower() for hook in hve_hooks),
        "enabled_counts": _value_counts(
            str(hook.get("enabled")).lower() if isinstance(hook.get("enabled"), bool) else "unknown"
            for hook in hooks
        ),
        "execution_tested": False,
        "error_count": sum(len(entry.get("errors", [])) for entry in project_entries),
        "warning_count": sum(len(entry.get("warnings", [])) for entry in project_entries),
        "errors": _entry_messages(project_entries, "errors"),
        "warnings": _entry_messages(project_entries, "warnings"),
    }


def _summarize_host(payload: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "user_agent": None,
        "version": None,
        "platform_family": None,
        "platform_os": None,
    }
    if not isinstance(payload, dict):
        return result
    user_agent = payload.get("userAgent")
    if isinstance(user_agent, str) and user_agent.strip():
        user_agent = _bounded_text(user_agent, 200)
        result["user_agent"] = user_agent
        match = re.search(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)", user_agent)
        if match:
            result["version"] = match.group(1)
    for source, target in (("platformFamily", "platform_family"), ("platformOs", "platform_os")):
        value = payload.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = _bounded_text(value, 80)
    return result


def _entry_messages(entries: list[dict[str, Any]], key: str) -> list[str]:
    messages: list[str] = []
    for entry in entries:
        for value in entry.get(key, []):
            message = value.get("message") if isinstance(value, dict) else value
            if isinstance(message, str) and message.strip():
                clean = _bounded_text(message, 300)
                if clean not in messages:
                    messages.append(clean)
            if len(messages) == 20:
                return messages
    return messages


def _summarize_config_agents(payload: Any) -> dict[str, Any]:
    config = payload.get("config") if isinstance(payload, dict) else None
    if not isinstance(config, dict):
        return {"status": "unavailable", "names": None}

    for key in ("agents", "custom_agents", "customAgents"):
        if key not in config:
            continue
        value = config[key]
        if value is None:
            return {"status": "not-exposed-by-config-read", "names": None}
        if isinstance(value, dict):
            names = sorted(str(name) for name in value)
        elif isinstance(value, list):
            names = sorted({
                str(item["name"])
                for item in value
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            })
        else:
            return {"status": "exposed-unparseable", "names": None}
        return {"status": "config-reported-only", "names": names}
    return {"status": "not-exposed-by-config-read", "names": None}


def _value_counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        label = value if isinstance(value, str) and value else "unknown"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _path_key(value: Any) -> str:
    if not isinstance(value, (str, Path)) or not value:
        return ""
    try:
        return os.path.normcase(os.path.normpath(str(Path(value).resolve(strict=False))))
    except (OSError, RuntimeError, ValueError):
        return ""


def _method_summary(response: dict[str, Any]) -> dict[str, Any]:
    status = "ok" if response.get("ok") else response.get("status", "error")
    summary: dict[str, Any] = {"status": status}
    if response.get("message"):
        summary["message"] = _bounded_text(str(response["message"]))
    return summary


def _bounded_text(value: str, maximum: int = 500) -> str:
    return " ".join(value.split())[:maximum]


class _AppServerClient:
    """A small bounded JSON-lines client for the read-only app-server probe."""

    def __init__(self, executable: str, project: Path, home: Path):
        self.executable = executable
        self.project = project
        self.home = home
        self.process: subprocess.Popen[bytes] | None = None
        self._stdout_queue: queue.Queue[bytes | object] = queue.Queue(maxsize=_APP_SERVER_MAX_QUEUED_LINES)
        self._overflow = threading.Event()
        self._stderr_lock = threading.Lock()
        self._stderr = bytearray()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._request_id = 0
        self.returncode: int | None = None
        self.stderr_summary = ""

    def __enter__(self) -> _AppServerClient:
        command = _app_server_command(self.executable)
        self.process = subprocess.Popen(
            command,
            cwd=self.project,
            env=_isolated_environment(self.home),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            raise PortError("Codex app-server stdio pipes could not be opened")
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        process = self.process
        if process is None:
            return
        if process.stdin and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            self.returncode = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                self.returncode = process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.returncode = None
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None:
                thread.join(timeout=0.25)
        with self._stderr_lock:
            self.stderr_summary = _bounded_text(self._stderr.decode("utf-8", errors="replace"), 1000)

    def initialize(self) -> dict[str, Any]:
        response = self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "hve_core_codex_discovery_probe",
                    "title": "HVE Core Codex discovery probe",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        if response.get("ok"):
            self._send({"method": "initialized", "params": {}})
        return response

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method not in _APP_SERVER_ALLOWED_METHODS:
            raise PortError(f"App-server method is outside the discovery allowlist: {method}")
        return self._request(method, params)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + _APP_SERVER_TIMEOUT_SECONDS
        while True:
            if self._overflow.is_set():
                return {"status": "output-limit", "message": "app-server output exceeded probe limits", "fatal": "output-limit"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "timeout", "message": f"Timed out waiting for {method}", "fatal": "timeout"}
            try:
                message_bytes = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                return {"status": "timeout", "message": f"Timed out waiting for {method}", "fatal": "timeout"}
            if self._overflow.is_set():
                return {"status": "output-limit", "message": "app-server output exceeded probe limits", "fatal": "output-limit"}
            if message_bytes is _RPC_EOF:
                return {"status": "process-exited", "message": f"app-server closed stdout before replying to {method}", "fatal": "process-exited"}
            try:
                message = json.loads(message_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"status": "protocol-error", "message": "app-server emitted a non-JSON stdout line", "fatal": "protocol-error"}
            if not isinstance(message, dict):
                return {"status": "protocol-error", "message": "app-server emitted a non-object JSON message", "fatal": "protocol-error"}
            if message.get("id") != request_id:
                if "id" not in message and "method" in message:
                    continue
                continue
            if isinstance(message.get("error"), dict):
                error = message["error"]
                error_code = error.get("code")
                error_message = _bounded_text(str(error.get("message", "app-server request failed")))
                unsupported = error_code == -32601 or any(
                    text in error_message.casefold()
                    for text in ("method not found", "unknown method", "unsupported method", "requires experimentalapi")
                )
                return {
                    "status": "unsupported" if unsupported else "rpc-error",
                    "message": error_message,
                    "error_code": error_code,
                }
            if "result" not in message:
                return {"status": "protocol-error", "message": "app-server response omitted result", "fatal": "protocol-error"}
            return {"ok": True, "result": message["result"]}

    def _send(self, value: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.stdin.closed:
            raise PortError("Codex app-server stdin is unavailable")
        payload = (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        total_bytes = 0
        try:
            while True:
                line = self.process.stdout.readline(_APP_SERVER_MAX_LINE_BYTES + 1)
                if not line:
                    break
                total_bytes += len(line)
                if len(line) > _APP_SERVER_MAX_LINE_BYTES or total_bytes > _APP_SERVER_MAX_OUTPUT_BYTES:
                    self._overflow.set()
                    if not line.endswith(b"\n"):
                        while line and not line.endswith(b"\n"):
                            line = self.process.stdout.readline(4096)
                    continue
                try:
                    self._stdout_queue.put_nowait(line)
                except queue.Full:
                    self._overflow.set()
                    continue
        except OSError:
            self._overflow.set()
        finally:
            try:
                self._stdout_queue.put_nowait(_RPC_EOF)
            except queue.Full:
                self._overflow.set()

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    break
                with self._stderr_lock:
                    remaining = _APP_SERVER_MAX_STDERR_BYTES - len(self._stderr)
                    if remaining > 0:
                        self._stderr.extend(chunk[:remaining])
        except OSError:
            return


def _app_server_command(executable: str) -> list[str]:
    resolved = shutil.which(executable) or executable
    suffix = Path(resolved).suffix.casefold()
    args = ["app-server", "--stdio"]
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        command_text = subprocess.list2cmdline([resolved, *args])
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_text]
    if os.name == "nt" and suffix == ".ps1":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell.exe"
        return [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", resolved, *args]
    return [resolved, *args]


def _isolated_environment(home: Path) -> dict[str, str]:
    allowed_names = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "OS", "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL", "TERM",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed_names}
    home_value = str(home.resolve())
    env.update({
        "CODEX_HOME": home_value,
        "HOME": home_value,
        "USERPROFILE": home_value,
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "TMP": str(home),
        "TEMP": str(home),
        "TMPDIR": str(home),
    })
    drive, tail = os.path.splitdrive(home_value)
    if drive:
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = tail
    return env
