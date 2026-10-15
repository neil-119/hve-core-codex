"""A clean wheel distribution must not need an embedded writable cache."""
import json
import io
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
import tomlkit
from hve_codex_porter.host import probe_plugin
from hve_codex_porter.host import probe_discovery


def test_probe_uses_private_temp_and_exact_enabled_identity(tmp_path, monkeypatch):
    distribution = tmp_path / "distribution"
    plugin = distribution / "plugins/hve-core-codex"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"hve-core-codex"}')
    marketplace = distribution / ".agents/plugins"
    marketplace.mkdir(parents=True)
    (marketplace / "marketplace.json").write_text('{}')
    def execute(command, **kwargs):
        args = command[1:]
        if args[0] == "--version":
            output = "codex fixture"
        elif args[:2] == ["plugin", "add"]:
            installed = Path(kwargs["env"]["CODEX_HOME"]) / "installed"
            shutil.copytree(plugin, installed)
            output = json.dumps({"installedPath": str(installed)})
        elif args[:2] == ["plugin", "list"]:
            output = json.dumps({"installed": [{"pluginId": "hve-core-codex-other@hve-core-codex-local", "installed": True, "enabled": True}]})
        else:
            output = '{}'
        return SimpleNamespace(returncode=0, stdout=output, stderr="")
    monkeypatch.setattr("hve_codex_porter.host.subprocess.run", execute)
    result = probe_plugin(distribution, "fixture")
    assert result["installed_files_match"]
    assert not result["passed"]
    assert not (distribution / ".cache").exists()


def _make_discovery_distribution(root: Path, skill_names=("core-one", "core-two")) -> Path:
    overlay = root / "codex-overlay"
    agents = overlay / "agents"
    rules = overlay / "rules"
    hooks = overlay / "hooks"
    plugin = root / "plugins" / "hve-core-codex"
    agents.mkdir(parents=True)
    rules.mkdir()
    hooks.mkdir()
    plugin.mkdir(parents=True)
    (overlay / "AGENTS.hve-core.md").write_text("HVE Core project guidance.\n", encoding="utf-8")
    (agents / "fixture-agent.toml").write_text('name = "fixture-agent"\n', encoding="utf-8")
    (rules / "index.json").write_text("{}\n", encoding="utf-8")
    (hooks / "hve_rules.py").write_text("# probe fixture\n", encoding="utf-8")
    (hooks / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "python HVE_HOOK_SCRIPT"}]}]}}),
        encoding="utf-8",
    )
    (plugin / "plugin.json").write_text('{"name":"hve-core-codex"}\n', encoding="utf-8")
    for name in skill_names:
        skill = plugin / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: fixture skill\n---\n", encoding="utf-8")
    return root


class _RecordingStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, value):
        self.data.extend(value)
        return len(value)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeAppServerProcess:
    def __init__(self, messages):
        self.stdin = _RecordingStdin()
        self.stdout = io.BytesIO(b"".join(json.dumps(message).encode("utf-8") + b"\n" for message in messages))
        self.stderr = io.BytesIO()
        self.returncode = None

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def kill(self):
        self.returncode = -9


def _discovery_messages(project: Path, *, hooks_error=False, include_agent_config=True):
    skills = []
    for name in ("core-one", "core-two"):
        skills.append({
            "name": name,
            "path": str(project / ".agents" / "skills" / name / "SKILL.md"),
            "scope": "repo",
            "enabled": True,
            "description": "fixture",
        })
    # A name-only prefix match and a same-name path from elsewhere must not satisfy HVE discovery.
    skills.extend([
        {"name": "prefix-core-one", "path": str(project / ".agents" / "skills" / "core-one" / "SKILL.md"), "enabled": True},
        {"name": "core-one", "path": str(project / "unrelated" / "core-one" / "SKILL.md"), "enabled": True},
    ])
    hve_hook = str(project / ".codex" / "hooks.json")
    hooks = [
        {"sourcePath": hve_hook, "eventName": "sessionStart", "trustStatus": "untrusted"},
        {"sourcePath": hve_hook, "eventName": "userPromptSubmit", "trustStatus": "managed"},
        {"sourcePath": str(project / "other-hooks.json"), "eventName": "stop", "trustStatus": "trusted"},
    ]
    config = {"agents": {"fixture-agent": {"description": "fixture"}}} if include_agent_config else {"agents": None}
    messages = [
        {"id": 1, "result": {
            "userAgent": "codex/0.144.6 (Windows 11; x64)",
            "platformFamily": "windows",
            "platformOs": "windows",
        }},
        {"id": 2, "result": {"data": [{"cwd": str(project), "skills": skills, "errors": []}]}},
        (
            {"id": 3, "error": {"code": -32601, "message": "Method not found"}}
            if hooks_error
            else {
                "id": 3,
                "result": {
                    "data": [{
                        "cwd": str(project),
                        "hooks": hooks,
                        "errors": [],
                        "warnings": ["Project trust is not established; hook configuration may be inactive."],
                    }],
                },
            }
        ),
        {"id": 4, "result": {"config": config, "origins": {}, "layers": []}},
    ]
    return messages


def test_discovery_probe_installs_standalone_skills_and_reports_native_state(tmp_path, monkeypatch):
    distribution = _make_discovery_distribution(tmp_path / "distribution")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-codex")
    processes = []

    def execute(command, **kwargs):
        project = Path(kwargs["cwd"])
        env = kwargs["env"]
        assert command[-2:] == ["app-server", "--stdio"]
        assert env["CODEX_HOME"] != str(Path.home())
        assert env["HOME"] == env["CODEX_HOME"]
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert not (Path(env["CODEX_HOME"]) / "auth.json").exists()
        assert (project / ".agents" / "skills" / "core-one" / "SKILL.md").is_file()
        assert (project / ".agents" / "skills" / "core-two" / "SKILL.md").is_file()
        assert (project / ".codex" / "agents" / "fixture-agent.toml").is_file()
        assert (project / ".codex" / "hooks.json").is_file()
        private_config = Path(env["CODEX_HOME"]) / "config.toml"
        config = tomlkit.parse(private_config.read_text(encoding="utf-8"))
        project_key = os.path.normcase(str(project.resolve()))
        assert set(config["projects"]) == {project_key}
        assert config["projects"][project_key]["trust_level"] == "trusted"
        assert "hooks" not in config
        process = _FakeAppServerProcess(_discovery_messages(project))
        processes.append(process)
        return process

    monkeypatch.setattr("hve_codex_porter.host.subprocess.Popen", execute)
    result = probe_discovery(distribution, "codex-fixture")

    assert result["passed"], json.dumps(result, indent=2)
    assert result["model_sessions_started"] == 0
    assert result["project_config_trusted_for_discovery"] is True
    assert result["hook_definition_trust"] == "not-granted"
    assert result["installation"]["standalone_skills"] == 2
    assert result["host"]["version"] == "0.144.6"
    assert result["host"]["platform_family"] == "windows"
    assert result["standalone_skills"]["enabled_exact_count"] == 2
    assert result["standalone_skills"]["missing"] == []
    assert {item["name"] for item in result["standalone_skills"]["matched"]} == {"core-one", "core-two"}
    assert result["hooks"]["count"] == 3
    assert result["hooks"]["hve_count"] == 2
    assert result["hooks"]["hve_status"] == "observed"
    assert result["hooks"]["hve_trust_status_counts"] == {"managed": 1, "untrusted": 1}
    assert not result["hooks"]["execution_tested"]
    assert result["hooks"]["warnings"] == ["Project trust is not established; hook configuration may be inactive."]
    assert result["custom_agents"]["files_on_disk"] == ["fixture-agent.toml"]
    assert result["custom_agents"]["reported_by_config_read"] == {
        "status": "config-reported-only",
        "names": ["fixture-agent"],
    }

    requests = [json.loads(line) for line in processes[0].stdin.data.splitlines()]
    assert [request["method"] for request in requests] == [
        "initialize", "initialized", "skills/list", "hooks/list", "config/read"
    ]
    assert requests[-1]["params"] == {"cwd": requests[-1]["params"]["cwd"]}
    assert not any(request["method"] in {"thread/start", "turn/start"} for request in requests)


def test_discovery_probe_reports_unsupported_hooks_and_unobservable_agents(tmp_path, monkeypatch):
    distribution = _make_discovery_distribution(tmp_path / "distribution", skill_names=("core-one",))

    def execute(command, **kwargs):
        project = Path(kwargs["cwd"])
        messages = _discovery_messages(project, hooks_error=True, include_agent_config=False)
        # This fixture installs one skill; make the app-server report that exact set.
        messages[1]["result"]["data"][0]["skills"] = [
            item for item in messages[1]["result"]["data"][0]["skills"] if item.get("name") == "core-one"
        ]
        return _FakeAppServerProcess(messages)

    monkeypatch.setattr("hve_codex_porter.host.subprocess.Popen", execute)
    result = probe_discovery(distribution, "codex-fixture")

    assert not result["passed"]
    assert result["methods"]["hooks/list"]["status"] == "unsupported"
    assert result["hooks"]["status"] == "unsupported"
    assert result["hooks"]["hve_status"] == "unsupported"
    assert result["custom_agents"]["file_count"] == 1
    assert result["custom_agents"]["reported_by_config_read"] == {
        "status": "not-exposed-by-config-read",
        "names": None,
    }


def test_discovery_probe_stops_on_bounded_app_server_output(tmp_path, monkeypatch):
    distribution = _make_discovery_distribution(tmp_path / "distribution", skill_names=("core-one",))
    monkeypatch.setattr("hve_codex_porter.host._APP_SERVER_MAX_LINE_BYTES", 16)
    processes = []

    def execute(command, **kwargs):
        process = _FakeAppServerProcess([])
        process.stdout = io.BytesIO(b"x" * 32 + b"\n")
        processes.append(process)
        return process

    monkeypatch.setattr("hve_codex_porter.host.subprocess.Popen", execute)
    result = probe_discovery(distribution, "codex-fixture")

    assert not result["passed"]
    assert result["methods"]["initialize"]["status"] == "output-limit"
    requests = [json.loads(line) for line in processes[0].stdin.data.splitlines()]
    assert [request["method"] for request in requests] == ["initialize"]


def test_discovery_probe_does_not_pass_empty_or_erroring_skill_enumeration(tmp_path, monkeypatch):
    empty_distribution = _make_discovery_distribution(
        tmp_path / "empty-distribution", skill_names=("core-one",)
    )

    def execute_empty(command, **kwargs):
        project = Path(kwargs["cwd"])
        messages = _discovery_messages(project)
        messages[1]["result"]["data"][0]["skills"] = []
        return _FakeAppServerProcess(messages)

    with monkeypatch.context() as patcher:
        patcher.setattr("hve_codex_porter.host.subprocess.Popen", execute_empty)
        patcher.setattr("hve_codex_porter.host._expected_skill_paths", lambda project: {})
        empty_result = probe_discovery(empty_distribution, "codex-fixture")
        assert empty_result["standalone_skills"]["expected_count"] == 0
        assert not empty_result["passed"]

    error_distribution = _make_discovery_distribution(
        tmp_path / "error-distribution", skill_names=("core-one",)
    )

    def execute_with_error(command, **kwargs):
        project = Path(kwargs["cwd"])
        messages = _discovery_messages(project)
        messages[1]["result"]["data"][0]["errors"] = [{"message": "Enumeration warning from host"}]
        return _FakeAppServerProcess(messages)

    monkeypatch.setattr("hve_codex_porter.host.subprocess.Popen", execute_with_error)
    error_result = probe_discovery(error_distribution, "codex-fixture")
    assert error_result["standalone_skills"]["enabled_exact_count"] == 1
    assert error_result["standalone_skills"]["error_count"] == 1
    assert not error_result["passed"]
