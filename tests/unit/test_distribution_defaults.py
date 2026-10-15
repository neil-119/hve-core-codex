from __future__ import annotations

import tomllib
from pathlib import Path
import pytest

from hve_codex_porter import cli
from hve_codex_porter.common import PortError


ROOT = Path(__file__).resolve().parents[2]


def test_source_cache_defaults_outside_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli.parser().parse_args(["inventory"]).cache == tmp_path / ".cache/hve-core-codex/sources"


def test_cli_rejects_repository_output_before_fetch():
    args = cli.parser().parse_args(["convert", "--output", str(ROOT)])
    with pytest.raises(PortError, match="outside the migrator"):
        cli.handle(args)


def test_default_distribution_uses_environment_override(monkeypatch, tmp_path):
    configured = tmp_path / "external" / "distribution"
    monkeypatch.setenv("HVE_CODEX_DISTRIBUTION", str(configured))

    assert cli.default_distribution() == configured
    parsed = cli.parser().parse_args(["install", "--target", str(tmp_path / "project")])
    assert parsed.distribution == configured


def test_explicit_distribution_overrides_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HVE_CODEX_DISTRIBUTION", str(tmp_path / "environment"))
    explicit = tmp_path / "explicit"

    parsed = cli.parser().parse_args([
        "install", "--target", str(tmp_path / "project"), "--distribution", str(explicit)
    ])

    assert parsed.distribution == explicit


def test_default_distribution_without_override_is_external_to_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("HVE_CODEX_DISTRIBUTION", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert cli.default_distribution() == tmp_path / ".codex" / "hve-core-codex-distribution"


def test_build_config_keeps_source_inputs_without_vendored_distribution_payload():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        config = tomllib.load(stream)

    targets = config["tool"]["hatch"]["build"]["targets"]
    sdist_include = targets["sdist"]["include"]
    wheel_include = targets["wheel"]["force-include"]

    assert "/src" in sdist_include
    assert "/mappings" in sdist_include
    assert "/schemas" in sdist_include
    assert "/scripts" in sdist_include
    assert "/tests" in sdist_include
    assert not any(path in sdist_include for path in ("/plugins", "/codex-overlay", "/generated", "/.agents/plugins"))
    assert {"mappings", "schemas"}.issubset(wheel_include)
    assert "tests/codex-host/stable.json" in wheel_include
    assert "tests/vally-custom" in wheel_include
    assert any(path.startswith("vally-executor/") for path in wheel_include)
    assert not any(
        path == "plugins/hve-core-codex"
        or path == "codex-overlay"
        or path == "generated"
        or path.startswith(".agents/plugins/")
        for path in wheel_include
    )
