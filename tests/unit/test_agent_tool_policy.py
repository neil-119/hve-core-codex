"""Source operation boundaries remain explicit without invented host settings."""
import tomlkit

from hve_codex_porter.agent_converter import render_agent


def test_declared_operations_preserve_narrow_scope():
    role = tomlkit.parse(render_agent("researcher", "Read evidence", "Read only.", True, [
        {"tool": "github/search_issues", "instruction": "Search issues using the configured GitHub connector; do not mutate issues."},
    ]))
    instructions = role["developer_instructions"]
    assert "`github/search_issues`" in instructions
    assert "do not mutate issues" in instructions
    assert "does not authorize an additional operation" in instructions
    assert "does not establish native per-tool enforcement" in instructions
    assert set(role) == {"name", "description", "sandbox_mode", "developer_instructions"}


def test_absent_and_empty_allowlists_have_distinct_semantics():
    inherited = tomlkit.parse(render_agent("inherited", "Inherited tools", "Procedure", False))
    empty = tomlkit.parse(render_agent("empty", "No tools", "Procedure", True, []))
    assert "Source tool scope" not in inherited["developer_instructions"]
    assert "Do not invoke tools" in empty["developer_instructions"]
