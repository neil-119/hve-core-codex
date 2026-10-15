from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit

import hve_codex_porter.installer as installer_module
from hve_codex_porter.installer import doctor, install, uninstall, update


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


def _distribution(base: Path) -> Path:
    distribution = base / "distribution"
    plugin = distribution / "plugins" / "hve-core-codex"
    overlay = distribution / "codex-overlay"
    _write(plugin / "plugin.json", json.dumps({"name": "hve-core-codex", "version": "1.2.3"}))
    _write(plugin / "skills" / "sample" / "SKILL.md", "---\nname: sample\ndescription: Sample skill\n---\nSee [the other skill](../other/SKILL.md) and [resource](../../resources/guide.md).\n")
    _write(plugin / "skills" / "other" / "SKILL.md", "---\nname: other\ndescription: Other skill\n---\nOther skill.\n")
    _write(plugin / "resources" / "guide.md", "Shared plugin resource.\n")
    _write(plugin / "assets" / "image.bin", b"\x00\x01\x02")
    _write(overlay / "agents" / "planner.toml", 'name = "planner"\ndescription = "Uses @@HVE_RULES_ROOT@@"\n')
    _write(
        overlay / "rules" / "index.json",
        json.dumps({"version": 1, "rules": [{"id": "demo", "source": "source-rule.md", "path": "demo.md", "apply_to": ["**/*"], "precedence": 1, "imports": []}]}),
    )
    _write(overlay / "rules" / "demo.md", "First guidance line.\nSee @@HVE_PLUGIN_ROOT@@/resources/guide.md.\nRules index: @@HVE_RULES_ROOT@@/index.json.\n")
    _write(overlay / "hooks" / "hve_rules.py", "# standalone test hook\n")
    hook_entry = {"hooks": [{"type": "command", "command": "python HVE_HOOK_SCRIPT", "statusMessage": "Loading HVE rules"}]}
    _write(overlay / "hooks" / "hooks.json", json.dumps({"hooks": {"SessionStart": [hook_entry], "SubagentStart": [hook_entry], "UserPromptSubmit": [hook_entry], "PreToolUse": [hook_entry]}}))
    _write(overlay / "AGENTS.hve-core.md", "Read @@HVE_PLUGIN_ROOT@@/resources/guide.md; query @@HVE_RULES_ROOT@@.\n")
    return distribution


def test_full_install_materializes_managed_overlay_and_registers_plugin(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()

    result = install(distribution, target)

    assert result["status"] == "installed"
    assert (target / ".codex" / "agents" / "planner.toml").is_file()
    assert (target / ".codex" / "rules" / "index.json").is_file()
    assert (target / ".codex" / "hooks" / "hve_rules.py").read_text(encoding="utf-8").startswith("# standalone")
    assert (target / ".agents" / "plugins" / "hve-core-codex" / "plugin.json").is_file()

    plugin_root = (target / ".agents" / "plugins" / "hve-core-codex").absolute().as_posix()
    rules_root = (target / ".codex" / "rules").absolute().as_posix()
    assert plugin_root in (target / ".codex" / "rules" / "demo.md").read_text(encoding="utf-8")
    assert rules_root in (target / ".codex" / "agents" / "planner.toml").read_text(encoding="utf-8")
    agents_text = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert "BEGIN HVE CORE CODEX MANAGED SECTION" in agents_text
    assert "Read " + plugin_root in agents_text

    hooks = json.loads((target / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    hook_command = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert str((target / ".codex" / "hooks" / "hve_rules.py").absolute()) in hook_command
    assert "HVE_HOOK_SCRIPT" not in hook_command
    config = tomlkit.parse((target / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert config["plugins"]["hve-core-codex@hve-core-codex-local"]["enabled"] is True
    marketplace = json.loads((target / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["source"]["path"] == "./.agents/plugins/hve-core-codex"
    assert doctor(target)["status"] == "healthy"


def test_dry_run_and_repeat_install_are_side_effect_safe(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()

    preview = install(distribution, target, dry_run=True)
    assert preview["status"] == "dry-run"
    assert preview["operations"]
    assert not (target / ".codex").exists()
    assert not (target / ".agents").exists()

    first = install(distribution, target)
    backup_dirs = list((target / ".codex" / "hve-core-codex" / "backups").glob("*"))
    second = install(distribution, target)
    assert first["status"] == "installed"
    assert second["status"] == "installed"
    assert second["operations"] == []
    assert list((target / ".codex" / "hve-core-codex" / "backups").glob("*")) == backup_dirs


def test_update_replaces_unmodified_owned_file_and_keeps_backup(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    install(distribution, target)
    installed_skill = target / ".agents" / "plugins" / "hve-core-codex" / "skills" / "sample" / "SKILL.md"
    old_content = installed_skill.read_bytes()
    _write(distribution / "plugins" / "hve-core-codex" / "skills" / "sample" / "SKILL.md", old_content + b"Updated upstream.\n")

    result = update(distribution, target)

    assert result["status"] == "updated"
    assert installed_skill.read_bytes().endswith(b"Updated upstream.\n")
    backups = list((target / ".codex" / "hve-core-codex" / "backups").rglob("SKILL.md"))
    assert backups and backups[0].read_bytes() == old_content


def test_update_merges_disjoint_user_and_upstream_edits(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    install(distribution, target)
    rule = target / ".codex" / "rules" / "demo.md"
    local = rule.read_text(encoding="utf-8").replace("Rules index:", "Locally adjusted rules index:")
    _write(rule, local)
    upstream = distribution / "codex-overlay" / "rules" / "demo.md"
    _write(upstream, upstream.read_text(encoding="utf-8").replace("First guidance line.", "Updated upstream guidance line."))

    result = update(distribution, target)

    merged = rule.read_text(encoding="utf-8")
    assert result["status"] == "updated"
    assert "Updated upstream guidance line." in merged
    assert "Locally adjusted rules index:" in merged
    assert doctor(target)["status"] == "healthy"
    removed = uninstall(target)
    assert removed["status"] == "conflict"
    assert rule.is_file()
    assert "Locally adjusted rules index:" in rule.read_text(encoding="utf-8")


def test_update_reports_overlapping_edit_without_overwriting_it(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    install(distribution, target)
    installed_agent = target / ".codex" / "agents" / "planner.toml"
    _write(installed_agent, 'name = "planner"\ndescription = "local value"\n')
    before = installed_agent.read_bytes()
    _write(distribution / "codex-overlay" / "agents" / "planner.toml", 'name = "planner"\ndescription = "new upstream value"\n')

    result = update(distribution, target)

    assert result["status"] == "conflict"
    assert any(item["path"].endswith("planner.toml") for item in result["conflicts"])
    assert installed_agent.read_bytes() == before


def test_uninstall_removes_owned_content_but_preserves_local_edits_and_user_config(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    _write(target / "AGENTS.md", "User project instructions.\n")
    _write(target / ".codex" / "hooks.json", json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "user command"}]}]}, "user": {"keep": True}}))
    install(distribution, target)
    installed_agent = target / ".codex" / "agents" / "planner.toml"
    _write(installed_agent, installed_agent.read_bytes() + b"# user change\n")

    result = uninstall(target)

    assert result["status"] == "conflict"
    assert installed_agent.exists()
    assert "User project instructions." in (target / "AGENTS.md").read_text(encoding="utf-8")
    assert "BEGIN HVE CORE CODEX MANAGED SECTION" not in (target / "AGENTS.md").read_text(encoding="utf-8")
    remaining_hooks = json.loads((target / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert remaining_hooks == {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "user command"}]}]}, "user": {"keep": True}}
    assert not (target / ".agents" / "plugins" / "hve-core-codex").exists()
    assert (target / ".codex" / "hve-core-codex" / "ownership.json").is_file()


def test_no_plugin_installs_standalone_skills_and_relative_resources(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()

    result = install(distribution, target, no_plugin=True)

    skills = target / ".agents" / "skills"
    assert result["status"] == "installed"
    assert not (target / ".agents" / "plugins" / "hve-core-codex").exists()
    assert (skills / "sample" / "SKILL.md").is_file()
    assert (skills / "other" / "SKILL.md").is_file()
    assert (target / ".agents" / "resources" / "guide.md").is_file()
    assert (skills / "sample" / "SKILL.md").read_text(encoding="utf-8").count("../../resources/guide.md") == 1
    support_root = target / ".codex" / "hve-core-codex" / "plugin-resources"
    rules = (target / ".codex" / "rules" / "demo.md").read_text(encoding="utf-8")
    assert support_root.absolute().as_posix() in rules
    assert (support_root / "resources" / "guide.md").is_file()
    assert not (target / ".codex" / "config.toml").exists()
    assert doctor(target)["status"] == "healthy"


def test_no_hooks_skips_hook_writes_and_reports_degradation(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()

    result = install(distribution, target, no_hooks=True)

    assert result["status"] == "installed"
    assert not (target / ".codex" / "hooks.json").exists()
    assert not (target / ".codex" / "hooks" / "hve_rules.py").exists()
    assert any("Hooks were skipped" in warning for warning in result["warnings"])


def test_update_no_hooks_removes_only_owned_hook_entries(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    user_entry = {"hooks": [{"type": "command", "command": "user command"}]}
    _write(target / ".codex" / "hooks.json", json.dumps({"hooks": {"SessionStart": [user_entry]}}))
    install(distribution, target)

    result = update(distribution, target, no_hooks=True)

    hooks = json.loads((target / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert result["status"] == "updated"
    assert hooks == {"hooks": {"SessionStart": [user_entry]}}
    assert not (target / ".codex" / "hooks" / "hve_rules.py").exists()
    assert doctor(target)["status"] == "healthy"


def test_user_scope_uses_codex_home_and_quotes_local_hook_command(tmp_path: Path, monkeypatch) -> None:
    distribution = _distribution(tmp_path)
    user_home = tmp_path / "user home"
    codex_home = user_home / "codex home"
    python = tmp_path / "python runtime" / "python.exe"
    monkeypatch.setattr(installer_module.sys, "executable", str(python))

    result = install(distribution, tmp_path / "ignored-target", scope="user", codex_home=codex_home, user_home=user_home)

    assert result["status"] == "installed"
    assert (codex_home / "agents" / "planner.toml").is_file()
    assert (codex_home / "rules" / "index.json").is_file()
    marketplace = json.loads((user_home / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["source"]["path"] == "./codex home/plugins/hve-core-codex"
    command = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert str((codex_home / "hooks" / "hve_rules.py").absolute()) in command
    assert command.startswith(f'"{python.absolute()}" "{(codex_home / "hooks" / "hve_rules.py").absolute()}"')
    assert (codex_home / "plugins" / "hve-core-codex" / "plugin.json").is_file()
    assert doctor(tmp_path / "ignored-target", scope="user", codex_home=codex_home, user_home=user_home)["status"] == "healthy"
    removed = uninstall(tmp_path / "ignored-target", scope="user", codex_home=codex_home, user_home=user_home)
    assert removed["status"] == "uninstalled"
    assert not (user_home / ".agents" / "plugins" / "marketplace.json").exists()


def test_custom_codex_home_requires_explicit_user_home_before_writing(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    codex_home = tmp_path / "codex home"

    with pytest.raises(installer_module.InstallerError, match="requires an explicit --user-home"):
        install(distribution, tmp_path / "ignored-target", scope="user", codex_home=codex_home)

    assert not codex_home.exists()


def test_user_no_plugin_uses_user_home_standalone_skills(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    user_home = tmp_path / "user home"
    codex_home = user_home / ".codex"

    result = install(
        distribution, tmp_path / "ignored-target", scope="user", codex_home=codex_home,
        user_home=user_home, no_plugin=True,
    )

    assert result["status"] == "installed"
    assert (user_home / ".agents" / "skills" / "sample" / "SKILL.md").is_file()
    assert (user_home / ".agents" / "resources" / "guide.md").is_file()
    assert (codex_home / "hve-core-codex" / "plugin-resources" / "resources" / "guide.md").is_file()
    assert not (user_home / ".agents" / "plugins" / "marketplace.json").exists()
    assert doctor(tmp_path / "ignored-target", scope="user", codex_home=codex_home, user_home=user_home)["status"] == "healthy"


def test_existing_disabled_plugin_setting_is_a_conflict(tmp_path: Path) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    _write(target / ".codex" / "config.toml", '[plugins."hve-core-codex@hve-core-codex-local"]\nenabled = false\n')

    result = install(distribution, target)

    assert result["status"] == "conflict"
    assert any("disabled" in item["reason"] for item in result["conflicts"])
    assert not (target / ".codex" / "agents" / "planner.toml").exists()


@pytest.mark.parametrize(
    ("rel", "contents", "message"),
    [
        (".codex/hooks.json", "{ invalid", "invalid JSON"),
        (".codex/config.toml", "[plugins\nenabled =", "cannot be parsed safely"),
        (".agents/plugins/marketplace.json", "{ invalid", "invalid JSON"),
    ],
)
def test_malformed_user_configs_are_reported_without_partial_install(
    tmp_path: Path, rel: str, contents: str, message: str,
) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    _write(target / rel, contents)

    result = install(distribution, target)

    assert result["status"] == "conflict"
    assert any(message in item["reason"] for item in result["conflicts"])
    assert not (target / ".codex" / "agents" / "planner.toml").exists()
    assert not (target / ".codex" / "hve-core-codex" / "ownership.json").exists()


def test_write_failure_rolls_back_prior_file_changes(tmp_path: Path, monkeypatch) -> None:
    distribution = _distribution(tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    atomic_write = installer_module._atomic_write
    count = 0

    def fail_on_second_write(path: Path, data: bytes, mode: int | None) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("induced write failure")
        atomic_write(path, data, mode)

    monkeypatch.setattr(installer_module, "_atomic_write", fail_on_second_write)

    with pytest.raises(OSError, match="induced write failure"):
        install(distribution, target)

    remaining = [
        path for path in target.rglob("*")
        if path.is_file() and "backups" not in path.parts
    ]
    assert remaining == []


@pytest.mark.skipif(installer_module.os.name != "nt", reason="Windows sharing violations are platform-specific")
def test_atomic_write_retries_windows_sharing_violations(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "managed.txt"
    original_replace = installer_module.os.replace
    calls = 0

    def sharing_once(source: str, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            error = PermissionError("file is temporarily locked")
            error.winerror = 32
            raise error
        original_replace(source, target)

    monkeypatch.setattr(installer_module.os, "replace", sharing_once)

    installer_module._atomic_write(destination, b"complete", None)

    assert calls == 3
    assert destination.read_bytes() == b"complete"


def test_generated_repository_distribution_installs_and_uninstalls(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    if not (repository / "plugins" / "hve-core-codex").is_dir() or not (repository / "codex-overlay").is_dir():
        pytest.skip("generated distribution is not present in this checkout")
    target = tmp_path / "repo"
    target.mkdir()

    result = install(repository, target)

    assert result["status"] == "installed"
    assert doctor(target)["status"] == "healthy"
    removed = uninstall(target)
    assert removed["status"] == "uninstalled"
