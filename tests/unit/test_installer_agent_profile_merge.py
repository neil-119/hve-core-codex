from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from hve_codex_porter.installer import (
    _is_codex_agent_profile,
    _three_way_merge_agent_profile,
    install,
    update,
)


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


def _profile_distribution(root: Path, profile: str) -> Path:
    distribution = root / "distribution"
    _write(
        distribution / "plugins" / "hve-core-codex" / "plugin.json",
        json.dumps({"name": "hve-core-codex", "version": "1.0.0"}),
    )
    _write(distribution / "codex-overlay" / "agents" / "researcher.toml", profile)
    return distribution


def test_profile_merge_preserves_local_model_pins_and_upstream_instructions() -> None:
    base = (
        b'name = "researcher"\n'
        b'model = "gpt-6.1-sol"\n'
        b'model_reasoning_effort = "high"\n'
        b'developer_instructions = "Old instructions"\n'
    )
    local = (
        b'name = "researcher"\n'
        b'model = "gpt-6-luna"\n'
        b'model_reasoning_effort = "max"\n'
        b'developer_instructions = "Old instructions" # locally formatted\n'
    )
    upstream = (
        b'name = "researcher"\n'
        b'model = "gpt-6.1-sol"\n'
        b'model_reasoning_effort = "high"\n'
        b'developer_instructions = "Updated instructions"\n'
    )

    merged = _three_way_merge_agent_profile(base, local, upstream)

    assert merged is not None
    parsed = tomllib.loads(merged.decode("utf-8"))
    assert parsed["model"] == "gpt-6-luna"
    assert parsed["model_reasoning_effort"] == "max"
    assert parsed["developer_instructions"] == "Updated instructions"
    assert b"# locally formatted" in merged


def test_profile_merge_combines_nested_and_top_level_additions_and_deletions() -> None:
    base = b'name = "researcher"\nremove_local = "old"\nremove_upstream = "old"\n[policy]\nkeep = true\n'
    local = b'name = "researcher"\nremove_upstream = "old"\nlocal_added = 1\n[policy]\nlocal = true\nkeep = true\n[extension]\nlocal = true\n'
    upstream = b'name = "researcher"\nremove_local = "old"\nupstream_added = 2\n[policy]\nkeep = true\nupstream = true\n[extension]\nupstream = true\n[upstream_only]\nenabled = true\n'

    merged = _three_way_merge_agent_profile(base, local, upstream)

    assert merged is not None
    parsed = tomllib.loads(merged.decode("utf-8"))
    assert "remove_local" not in parsed
    assert "remove_upstream" not in parsed
    assert parsed["local_added"] == 1
    assert parsed["upstream_added"] == 2
    assert parsed["policy"] == {"keep": True, "local": True, "upstream": True}
    assert parsed["extension"] == {"local": True, "upstream": True}
    assert parsed["upstream_only"] == {"enabled": True}


@pytest.mark.parametrize(
    ("base", "local_value", "upstream_value"),
    [
        (b'name = "researcher"\n', b'name = "local"\n', b'name = "upstream"\n'),
        (
            b'name = "researcher"\n',
            b'name = "researcher"\nadded = "local"\n',
            b'name = "researcher"\nadded = "upstream"\n',
        ),
        (
            b'name = "researcher"\nretired = "old"\n',
            b'name = "researcher"\n',
            b'name = "researcher"\nretired = "changed"\n',
        ),
    ],
)
def test_profile_merge_conflicts_on_different_changes_to_the_same_key(
    base: bytes,
    local_value: bytes,
    upstream_value: bytes,
) -> None:
    assert _three_way_merge_agent_profile(base, local_value, upstream_value) is None


@pytest.mark.parametrize(
    ("base", "local", "upstream"),
    [
        (b"enabled = false\n", b"enabled = true\n", b"enabled = 1\n"),
        (b"count = 0\n", b"count = 1\n", b"count = 1.0\n"),
        (
            b"[policy]\nenabled = false\n",
            b"[policy]\nenabled = true\n",
            b"[policy]\nenabled = 1\n",
        ),
        (b"values = [false]\n", b"values = [true]\n", b"values = [1]\n"),
        (b"values = [0]\n", b"values = [1]\n", b"values = [1.0]\n"),
        (
            b"[[rules]]\nenabled = false\n",
            b"[[rules]]\nenabled = true\n",
            b"[[rules]]\nenabled = 1\n",
        ),
    ],
)
def test_profile_merge_distinguishes_toml_types_recursively(
    base: bytes,
    local: bytes,
    upstream: bytes,
) -> None:
    assert _three_way_merge_agent_profile(base, local, upstream) is None


@pytest.mark.parametrize("invalid_input", ["base", "local", "upstream"])
def test_profile_merge_fails_closed_when_any_input_is_invalid_toml(invalid_input: str) -> None:
    valid = b'name = "researcher"\n'
    inputs = {"base": valid, "local": valid, "upstream": valid}
    inputs[invalid_input] = b'name = [\n'

    assert _three_way_merge_agent_profile(inputs["base"], inputs["local"], inputs["upstream"]) is None


@pytest.mark.parametrize("invalid_input", ["base", "local", "upstream"])
def test_profile_merge_fails_closed_when_any_input_is_not_utf8(invalid_input: str) -> None:
    valid = b'name = "researcher"\n'
    inputs = {"base": valid, "local": valid, "upstream": valid}
    inputs[invalid_input] = b"\xff"

    assert _three_way_merge_agent_profile(inputs["base"], inputs["local"], inputs["upstream"]) is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".codex/agents/researcher.toml", True),
        (".codex/agents/researcher.TOML", True),
        (".codex/agents/nested/researcher.toml", False),
        (".codex/rules/researcher.toml", False),
        (".agents/agents/researcher.toml", False),
    ],
)
def test_semantic_merge_is_scoped_to_direct_project_agent_profiles(path: str, expected: bool) -> None:
    assert _is_codex_agent_profile(path) is expected


def test_update_applies_semantic_profile_merge_without_losing_user_pins(tmp_path: Path) -> None:
    base_profile = (
        'name = "researcher"\n'
        'model = "gpt-6.1-sol"\n'
        'model_reasoning_effort = "high"\n'
        'developer_instructions = "Old instructions"\n'
    )
    distribution = _profile_distribution(tmp_path, base_profile)
    target = tmp_path / "project"
    target.mkdir()

    assert install(distribution, target, no_hooks=True)["status"] == "installed"
    profile_path = target / ".codex" / "agents" / "researcher.toml"
    _write(
        profile_path,
        base_profile.replace('gpt-6.1-sol', 'gpt-6-luna').replace('"high"', '"max"'),
    )
    _write(
        distribution / "codex-overlay" / "agents" / "researcher.toml",
        base_profile.replace("Old instructions", "Updated instructions"),
    )

    result = update(distribution, target, no_hooks=True)

    assert result["status"] == "updated"
    parsed = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-6-luna"
    assert parsed["model_reasoning_effort"] == "max"
    assert parsed["developer_instructions"] == "Updated instructions"
