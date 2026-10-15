"""Cross-subsystem test using a fixture distribution built in the test's temp directory."""
from pathlib import Path
from hve_codex_porter.conversion import convert
from hve_codex_porter.installer import install, uninstall, doctor
from hve_codex_porter.rules import match_rules

ROOT = Path(__file__).resolve().parents[2]

def test_converter_installer_rules_and_uninstall_preserve_user_files(tmp_path):
    source, dist, target = tmp_path / "source", tmp_path / "dist", tmp_path / "project"
    source.mkdir()
    target.mkdir()
    folder = source / ".github/instructions"
    folder.mkdir(parents=True)
    (folder / "python.instructions.md").write_text("---\napplyTo: '**/*.py'\n---\nKeep user authorization.\n", encoding="utf-8")
    skill = source / ".github/skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demonstration workflow\n---\nKeep existing files.\n", encoding="utf-8")
    (target / "AGENTS.md").write_text("User policy: no remote mutation.\n", encoding="utf-8")
    (target / "keep.txt").write_bytes(b"unchanged")
    convert(source, dist, {"upstream_ref": "fixture", "upstream_sha": "a" * 40, "channel": "edge"}, ROOT / "mappings")
    result = install(dist, target)
    assert not result["conflicts"]
    assert match_rules(target / ".codex/rules", ["src/main.py"])
    assert not doctor(target)["issues"]
    result = uninstall(target)
    assert not result["conflicts"]
    assert (target / "keep.txt").read_bytes() == b"unchanged"
    assert "User policy" in (target / "AGENTS.md").read_text()
