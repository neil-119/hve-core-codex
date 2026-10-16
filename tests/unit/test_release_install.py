"""Release selection, checksum failures and ZIP safety without network/model calls."""
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_installer", ROOT / "scripts/install_release.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_edge_uses_latest_published_edge_not_stable_or_drafts():
    prefix = "hve-core-edge-" + "a" * 40 + "-codex."
    releases = [
        {"tag_name": prefix + "1", "prerelease": True, "published_at": "2026-10-01"},
        {"tag_name": prefix + "2", "prerelease": True, "published_at": "2026-10-10"},
        {"tag_name": prefix + "3", "prerelease": True, "draft": True, "published_at": "2026-10-11"},
        {"tag_name": "unrelated-prerelease", "prerelease": True, "published_at": "2026-10-12"},
    ]
    assert installer.select_release("edge", lambda _: releases)["tag_name"] == prefix + "2"


def test_exact_release_tag_is_encoded_and_must_match():
    calls = []
    def get(url):
        calls.append(url)
        return {"tag_name": "tag/1"}
    assert installer.select_release("tag/1", get)["tag_name"] == "tag/1"
    assert calls == [installer.API + "/releases/tags/tag%2F1"]
    with pytest.raises(ValueError, match="published"):
        installer.select_release("wanted", lambda _: {"tag_name": "wrong"})


@pytest.mark.parametrize("name", ["../escape", "/escape", "C:/escape", "..\\escape"])
def test_zip_rejects_unsafe_paths_before_writing(tmp_path, name):
    path = tmp_path / "dist.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("generated/provenance.json", "{}")
        archive.writestr(name, "unsafe")
    target = tmp_path / "extracted"
    with pytest.raises(ValueError, match="Unsafe"):
        installer.extract_distribution(path, target)
    assert not target.exists()


def test_sha_mismatch_stops_installation(tmp_path):
    assets = {"dist.zip": {"name": "dist.zip"}, "dist.zip.sha256": {"name": "dist.zip.sha256"}}
    def fetch(asset, path):
        path.write_bytes(b"payload" if asset["name"] == "dist.zip" else b"0" * 64 + b"  dist.zip\n")
    with pytest.raises(ValueError, match="mismatch"):
        installer.verified_asset(assets, "dist.zip", tmp_path, fetch)


def test_release_install_uses_persistent_tool_and_selected_project(tmp_path):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("generated/provenance.json", json.dumps({"channel": "edge"}))
    wheel = "hve_core_codex-0.1.0-py3-none-any.whl"
    data = {"dist.zip": payload.getvalue(), wheel: b"wheel fixture"}
    for name in list(data):
        data[name + ".sha256"] = (hashlib.sha256(data[name]).hexdigest() + "  " + name + "\n").encode()
    release = {"tag_name": "pinned", "assets": [{"name": name} for name in data]}
    # Replace the injected downloader at the verified-asset boundary.
    real_verify = installer.verified_asset
    def verify(assets, name, directory):
        return real_verify(assets, name, directory, lambda asset, path: path.write_bytes(data[asset["name"]]))
    with patch.object(installer, "select_release", return_value=release) as select, \
         patch.object(installer, "verified_asset", side_effect=verify), \
         patch.object(installer.subprocess, "run") as run, \
         patch.object(installer.subprocess, "check_output", return_value=str(tmp_path / "tools")):
        assert installer.main(["--release", "pinned", "--target", str(tmp_path), "--no-plugin"]) == 0
    select.assert_called_once_with("pinned")
    assert run.call_args_list[0].args[0][:4] == ["uv", "tool", "install", "--force"]
    command = run.call_args_list[1].args[0]
    assert command[1] == "install"
    assert "--no-plugin" in command
    assert command[command.index("--target") + 1] == str(tmp_path)
    assert "--global" not in command
    assert "PYTHONPATH" not in run.call_args_list[1].kwargs["env"]
    assert "PYTHONHOME" not in run.call_args_list[1].kwargs["env"]


@pytest.mark.parametrize("project", [False, True])
def test_uninstall_uses_existing_tool_without_downloading_or_reinstalling(tmp_path, project):
    executable = tmp_path / "hve-core-codex" / ("Scripts/hve-codex.exe" if installer.os.name == "nt" else "bin/hve-codex")
    executable.parent.mkdir(parents=True)
    executable.touch()
    args = ["--uninstall", "--dry-run"]
    if project:
        args += ["--target", str(tmp_path)]
    with patch.object(installer, "select_release") as select, \
         patch.object(installer.subprocess, "check_output", return_value=str(tmp_path)), \
         patch.object(installer.subprocess, "run") as run:
        assert installer.main(args) == 0
    select.assert_not_called()
    run.assert_called_once()
    assert run.call_args.args[0] == [str(executable), "uninstall", *(["--target", str(tmp_path)] if project else ["--global"]), "--dry-run"]
    assert "PYTHONPATH" not in run.call_args.kwargs["env"]


def test_uninstall_missing_tool_does_not_download(tmp_path):
    with patch.object(installer, "select_release") as select, \
         patch.object(installer.subprocess, "check_output", return_value=str(tmp_path)), \
         patch.object(installer.subprocess, "run") as run:
        with pytest.raises(ValueError, match="not installed"):
            installer.main(["--uninstall"])
    select.assert_not_called()
    run.assert_not_called()
