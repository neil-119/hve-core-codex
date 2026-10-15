"""Shared installation lifecycle and real standalone hook routing, without models."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hve_codex_porter.cli import main, parser
from test_installer import _distribution, _write


@pytest.mark.parametrize("command", ["install", "update", "doctor", "uninstall"])
def test_global_scope_alias_and_conflicting_flags(command: str) -> None:
    assert parser().parse_args([command, "--global"]).scope == "user"
    assert parser().parse_args([command, "--scope", "user"]).scope == "user"
    assert parser().parse_args([command]).scope == "project"
    with pytest.raises(SystemExit):
        parser().parse_args([command, "--global", "--scope", "project"])


def test_global_cli_lifecycle_and_hook_routing_across_repositories(tmp_path: Path, monkeypatch, capsys) -> None:
    distribution = _distribution(tmp_path)
    runtime = Path(__file__).resolve().parents[2] / "src/hve_codex_porter/hook_runtime.py"
    _write(distribution / "codex-overlay/hooks/hve_rules.py", runtime.read_bytes())
    index_path = distribution / "codex-overlay/rules/index.json"
    registry = json.loads(index_path.read_text())
    registry["rules"][0]["apply_to"] = ["src/**/*.py"]
    _write(index_path, json.dumps(registry))
    home = tmp_path / "personal"
    codex_home = home / ".codex"
    _write(codex_home / "config.toml", 'model = "personal-model"\n')
    _write(codex_home / "AGENTS.md", "Personal guidance.\n")
    _write(home / ".agents/skills/personal/SKILL.md", "Personal skill.\n")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("HVE_CODEX_HOOK_STATE_DIR", str(tmp_path / "hook-state"))
    shared = ["--global", "--user-home", str(home), "--codex-home", str(codex_home)]
    candidate = ["--distribution", str(distribution), "--no-plugin"]

    assert main(["install", *shared, *candidate, "--dry-run"]) == 0
    assert not (codex_home / "hve-core-codex/ownership.json").exists()
    assert not (home / ".agents/skills/sample").exists()
    assert main(["install", *shared, *candidate]) == 0
    assert main(["doctor", *shared]) == 0
    assert (home / ".agents/skills/sample/SKILL.md").is_file()
    assert (codex_home / "agents/planner.toml").is_file()
    hook = codex_home / "hooks/hve_rules.py"

    # A containing checkout's registry must not leak into independent child repos.
    _write(tmp_path / "codex-overlay/rules/index.json", (codex_home / "rules/index.json").read_bytes())
    _write(tmp_path / "codex-overlay/rules/demo.md", "WRONG_ANCESTOR guidance.\n")

    repos = [tmp_path / "repo-a", tmp_path / "repo-b"]
    for index, repo in enumerate(repos):
        (repo / ".git").mkdir(parents=True)
        nested = repo / "src/nested"
        nested.mkdir(parents=True)
        target = repo / "src/app.py"
        _write(target, "print('hello')\n")
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": f"repo-{index}",
                   "turn_id": "1", "cwd": str(nested), "prompt": f"Read `{target}`"}
        result = subprocess.run([sys.executable, "-B", str(hook)], input=json.dumps(payload),
                                text=True, capture_output=True, cwd=nested, check=True)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "src/app.py" in context
        assert "First guidance line." in context
        assert "WRONG_ANCESTOR" not in context
        assert not (repo / ".codex").exists()
        assert not (repo / ".agents").exists()
        payload.update(turn_id="2", prompt="Read `README.md`")
        result = subprocess.run([sys.executable, "-B", str(hook)], input=json.dumps(payload),
                                text=True, capture_output=True, cwd=nested, check=True)
        assert not result.stdout.strip()

    # A local registry takes precedence even when the shared hook handles the event.
    local = repos[0] / ".codex/rules"
    _write(local / "index.json", (codex_home / "rules/index.json").read_bytes())
    _write(local / "demo.md", "PROJECT_ONLY guidance.\n")
    payload.update(cwd=str(repos[0]), session_id="local-registry", prompt="Read `src/app.py`")
    result = subprocess.run([sys.executable, "-B", str(hook)], input=json.dumps(payload),
                            text=True, capture_output=True, cwd=repos[0], check=True)
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "PROJECT_ONLY" in context
    assert "First guidance line." not in context

    _write(distribution / "codex-overlay/rules/demo.md", "Updated shared guidance.\n")
    assert main(["update", *shared, *candidate]) == 0
    assert "Updated shared guidance." in (codex_home / "rules/demo.md").read_text()
    assert main(["doctor", *shared]) == 0
    assert main(["uninstall", *shared]) == 0
    assert not (home / ".agents/skills/sample").exists()
    assert (home / ".agents/skills/personal/SKILL.md").read_text() == "Personal skill.\n"
    assert (codex_home / "AGENTS.md").read_text().strip() == "Personal guidance."
    assert 'model = "personal-model"' in (codex_home / "config.toml").read_text()
    assert (local / "demo.md").read_text() == "PROJECT_ONLY guidance.\n"
    capsys.readouterr()
