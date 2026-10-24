"""Build boundaries and reproducible release archives, with no network/models."""
from __future__ import annotations

import importlib.util
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from hve_codex_porter.common import PortError
from hve_codex_porter.release import write_checksums, write_release_manifest
from test_conversion import upstream  # noqa: F401 -- shared synthetic-source fixture

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hve_build_distribution", ROOT / "scripts/build_distribution.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_archive_bytes_ignore_directory_name_clock_and_owner(tmp_path: Path) -> None:
    for name in ("one", "two"):
        root = tmp_path / name
        (root / ".agents/plugins").mkdir(parents=True)
        (root / ".agents/plugins/marketplace.json").write_text('{}\n')
        (root / "plugins/hve-core-codex").mkdir(parents=True)
        (root / "plugins/hve-core-codex/hello.md").write_text("Same bytes.\n")
        (root / "unrelated-private.txt").write_text("Do not ship unrelated files.\n")
        builder.archive_distribution(root)
        builder.zip_distribution(root)
    assert (tmp_path / "one.tar.gz").read_bytes() == (tmp_path / "two.tar.gz").read_bytes()
    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "one.zip") as archive:
        assert archive.namelist() == [".agents/plugins/marketplace.json", "plugins/hve-core-codex/hello.md"]
        assert archive.read("plugins/hve-core-codex/hello.md") == (tmp_path / "one/plugins/hve-core-codex/hello.md").read_bytes()
    with tarfile.open(tmp_path / "one.tar.gz") as archive:
        assert archive.getnames() == [".agents/plugins/marketplace.json", "plugins/hve-core-codex/hello.md"]
        assert all(member.mtime == 0 and member.uid == 0 for member in archive.getmembers())


def test_build_rejects_output_inside_migrator_before_fetch() -> None:
    with pytest.raises(PortError, match="outside the migrator"):
        builder.main(["--output", str(ROOT / "generated")])


def test_build_generates_external_distribution_and_can_finalize_archive(upstream, tmp_path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(builder, "git", lambda *_args: "a" * 40)
    output = tmp_path / "distribution"
    assert builder.main(["--source-dir", str(upstream), "--output", str(output), "--sha", "a" * 40]) == 0
    assert (output / "plugins/hve-core-codex/plugin.json").is_file()
    assert (output / "generated/provenance.json").is_file()
    assert (tmp_path / "distribution.tar.gz.sha256").is_file()
    assert builder.main(["--output", str(output), "--archive-only"]) == 0
    write_release_manifest(output, {"schema_version": 1, "channel": "stable"})
    write_checksums(output)
    assert builder.main(["--output", str(output), "--archive-only"]) == 0
    with zipfile.ZipFile(tmp_path / "distribution.zip") as archive:
        assert "generated/release-manifest.json" in archive.namelist()
    plugin = output / "plugins/hve-core-codex/plugin.json"
    original = plugin.read_bytes()
    plugin.write_bytes(original + b"\n")
    with pytest.raises(PortError, match="structural validation"):
        builder.main(["--output", str(output), "--archive-only"])
    plugin.write_bytes(original)
    provenance = json.loads((output / "generated/provenance.json").read_text())
    assert provenance["upstream_sha"] == "a" * 40
    with pytest.raises(PortError, match="disagrees"):
        builder.main(["--source-dir", str(upstream), "--output", str(tmp_path / "bad"), "--sha", "b" * 40])
