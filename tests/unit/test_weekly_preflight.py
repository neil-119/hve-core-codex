from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_preflight():
    path = Path(__file__).resolve().parents[2] / ".github/scripts/weekly_preflight.py"
    spec = importlib.util.spec_from_file_location("weekly_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("published", [False, True])
def test_preflight_pins_protected_edge_and_skips_already_published(monkeypatch, tmp_path, published):
    module = load_preflight()
    branch_sha, source_sha = "a" * 40, "b" * 40
    builder_sha, adapter_sha = "c" * 40, "d" * 40
    lock = {
        "schema_version": 1,
        "channel": "edge",
        "upstream_ref": "main",
        "upstream_sha": source_sha,
        "adapter_revision": adapter_sha,
        "builder_commit": builder_sha,
    }
    manifest = {
        "schema_version": 1,
        "channel": "edge",
        "source": {"ref": "main", "sha": source_sha},
        "adapter_sha": adapter_sha,
        "converter": {"sha": builder_sha},
    }
    env = {
        "CHANNEL": "edge", "EVENT_NAME": "schedule", "EVENT_REF": "refs/heads/main",
        "EVENT_SHA": "c" * 40, "GITHUB_REPOSITORY": "owner/port",
        "GITHUB_OUTPUT": str(tmp_path / "output"), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []

    def command(*args):
        calls.append(args)
        if args[:2] == ("gh", "api"):
            if args[2].endswith("/branches/edge"):
                return '{"protected":true}'
            return json.dumps([[{"tag_name": f"hve-core-edge-{source_sha}-codex.1", "draft": False, "prerelease": True}]] if published else [[]])
        if args[1] == "rev-parse":
            return branch_sha
        return ""

    def run(args, **kwargs):
        calls.append(tuple(args))
        metadata_path = args[-1].split(":", 1)[1]
        if metadata_path.endswith("/source-lock.json"):
            return SimpleNamespace(returncode=0, stdout=json.dumps(lock))
        if metadata_path.endswith("/release-manifest.json"):
            return SimpleNamespace(returncode=0, stdout=json.dumps(manifest))
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "discover_sources", lambda *a, **kw: [{"channel": "edge", "source_ref": "main", "source_sha": source_sha}])
    module.main()
    output = (tmp_path / "output").read_text()
    assert f"publish={'false' if published else 'true'}" in output
    assert f"source_sha={branch_sha}" in output
    assert "target_branch=edge" in output
    assert all(args[0] in {"git", "gh"} for args in calls)
    assert not any("push" in args or "create" in args for args in calls)


def test_channel_manifest_rejects_a_source_lock_that_does_not_match_manifest(monkeypatch):
    module = load_preflight()
    lock = {
        "schema_version": 1,
        "channel": "stable",
        "upstream_ref": "v3.2.2",
        "upstream_sha": "a" * 40,
        "adapter_revision": "c" * 40,
        "builder_commit": "d" * 40,
    }
    manifest = {
        "schema_version": 1,
        "channel": "stable",
        "source": {"ref": "v3.2.2", "sha": "b" * 40},
        "adapter_sha": "c" * 40,
        "converter": {"sha": "d" * 40},
    }

    def run(args, **kwargs):
        content = lock if args[-1].endswith("source-lock.json") else manifest
        return SimpleNamespace(returncode=0, stdout=json.dumps(content))

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(SystemExit, match="does not match"):
        module.read_channel_manifest("e" * 40, "stable")


def test_preflight_rejects_wrong_caller_and_unprotected_branch(monkeypatch):
    module = load_preflight()
    monkeypatch.setenv("CHANNEL", "edge")
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setenv("EVENT_REF", "refs/heads/edge")
    with pytest.raises(SystemExit, match="reviewed main"):
        module.main()
    monkeypatch.setenv("EVENT_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/port")
    monkeypatch.setattr(module, "command", lambda *a: '{"protected":false}')
    with pytest.raises(SystemExit, match="protected channel"):
        module.main()
