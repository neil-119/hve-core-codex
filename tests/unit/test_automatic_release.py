from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hve_codex_porter.release import ReleaseError, publication_eligibility


ROOT = Path(__file__).resolve().parents[2]


def load_helper():
    spec = importlib.util.spec_from_file_location("automatic_release", ROOT / ".github/scripts/automatic_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_policy_keeps_static_and_protected_branch_gates():
    report = {
        "upstream_sha": "a" * 40, "adapter_revision": "b" * 40, "mapping_hash": "d" * 64,
        "coverage": {"inventory": "complete", "structural": "passed"},
        "release_blockers": [{"code": "runtime-evidence-missing"}],
    }
    args = dict(channel="stable", compatibility_report=report, source_sha="a" * 40,
                adapter_sha="b" * 40, converter_sha="c" * 40,
                output_hashes={"plugins/hve-core-codex/.codex-plugin/plugin.json": "e" * 64},
                mapping_sha256="d" * 64, scenario_definition_sha256="f" * 64,
                merged_commit="c" * 40, protected_branch=True)
    assert not publication_eligibility(**args)["eligible"]
    assert publication_eligibility(**args, migration_only=True)["eligible"]
    assert not publication_eligibility(**(args | {"protected_branch": False}), migration_only=True)["eligible"]
    for change in ({"unknown_fields": ["new-field"]}, {"unresolved_references": ["missing"]},
                   {"coverage": {"inventory": "incomplete", "structural": "passed"}},
                   {"release_blockers": [{"code": "license-missing"}]}):
        assert not publication_eligibility(**(args | {"compatibility_report": report | change}), migration_only=True)["eligible"]
    assert not publication_eligibility(**args, migration_only=True, runtime_evidence={"result": "passed"})["eligible"]


def test_assets_digest_detects_mutation_addition_and_symlinks(tmp_path):
    helper = load_helper()
    (tmp_path / "dist.zip").write_bytes(b"archive")
    digest = helper.asset_digest(tmp_path)
    (tmp_path / "dist.zip").write_bytes(b"changed")
    assert helper.asset_digest(tmp_path) != digest
    (tmp_path / "dist.zip").write_bytes(b"archive")
    (tmp_path / "extra").write_text("unexpected")
    assert helper.asset_digest(tmp_path) != digest
    with pytest.raises(ReleaseError, match="do not match"):
        helper.publish("stable", tmp_path, digest, "tag")
    (tmp_path / ".gitignore").write_bytes(b"*")
    with pytest.raises(ReleaseError, match="safely named"):
        helper.asset_digest(tmp_path)


@pytest.mark.parametrize("channel", ["stable", "edge"])
@pytest.mark.parametrize("published", [False, True])
def test_selection_skips_published_sources_and_rejects_stale_main(monkeypatch, tmp_path, channel, published):
    helper = load_helper()
    for name, value in {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_REF": "refs/heads/main",
                        "GITHUB_SHA": "c" * 40, "RUNNER_TEMP": str(tmp_path),
                        "GITHUB_OUTPUT": str(tmp_path / "outputs")}.items():
        monkeypatch.setenv(name, value)
    source = {"channel": channel, "source_sha": "a" * 40,
              "source_ref": "main" if channel == "edge" else "hve-core-v3.2.2"}
    tag = f"hve-core-edge-{'a' * 40}-codex.1" if channel == "edge" else "hve-core-v3.2.2-codex.1"
    branch = {"protected": True, "commit": {"sha": "c" * 40}}
    monkeypatch.setattr(helper, "discover_sources", lambda *a, **kw: [source])
    assets = [{"name": name} for name in ("dist.zip", "dist.zip.sha256", "dist.tar.gz", "dist.tar.gz.sha256",
              "hve_core_codex-0.1.0-py3-none-any.whl", "hve_core_codex-0.1.0-py3-none-any.whl.sha256")]
    assets.append({"name": "release-manifest.json", "id": 123})
    monkeypatch.setattr(helper, "gh", lambda *a: {"source": {"sha": "a" * 40}} if "/assets/123" in a[1] else branch if a[1].endswith("branches/main") else
                        [[{"tag_name": tag, "prerelease": channel == "edge", "draft": False, "assets": assets}]] if published else [[]])
    helper.select(channel)
    assert f"changed={'false' if published else 'true'}" in (tmp_path / "outputs").read_text()
    branch["commit"]["sha"] = "d" * 40
    with pytest.raises(ReleaseError, match="advanced"):
        helper.select(channel)


def test_seal_records_migration_scope_without_claiming_runtime(monkeypatch, tmp_path):
    helper = load_helper()
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path.parent / "outputs"))
    (tmp_path / "release-manifest.json").write_text(json.dumps({
        "source": {"sha": "a" * 40}, "upstream_manifest_version": "3.2.2",
    }))
    helper.seal("stable", tmp_path)
    assert "migration-only" in (tmp_path / "release-notes.md").read_text()
    assert "No fresh live CLI/Desktop" in (tmp_path / "release-notes.md").read_text()


def test_automatic_workflow_separates_build_and_publisher_and_reports_failures():
    workflow = YAML(typ="safe").load((ROOT / ".github/workflows/automatic-release.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["build"]["permissions"] == {"actions": "read", "contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "write"}
    assert jobs["publish"]["needs"] == "build"
    assert "environment" not in jobs["publish"]
    assert "failure" in jobs["report-failure"]["if"]
    steps = jobs["publish"]["steps"]
    download = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["artifact-ids"] == "${{ needs.build.outputs.artifact_id }}"
    assert steps[-1]["env"]["ASSET_DIGEST"] == "${{ needs.build.outputs.digest }}"
    text = (ROOT / ".github/workflows/automatic-release.yml").read_text()
    assert "--validation-policy migration" in text
    assert "eval vally-run" not in text
    assert "--runtime-evidence" not in text
    assert 'uv build --wheel --out-dir "$RUNNER_TEMP/installer"' in text
    assert 'cp "$RUNNER_TEMP/installer/"*.whl "$RUNNER_TEMP/assets/"' in text


@pytest.mark.parametrize("channel", ["stable", "edge"])
@pytest.mark.parametrize("failure", [None, "upload", "checksum", "tag-conflict", "stale-source"])
def test_publication_is_draft_until_verified_and_never_rewrites_tag(monkeypatch, tmp_path, channel, failure):
    helper = load_helper()
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    ref = "v3.2.2" if channel == "stable" else "main"
    candidate = {"channel": channel, "source": {"sha": "a" * 40, "ref": ref},
                 "upstream_manifest_version": "3.2.2", "converter": {"sha": "c" * 40}}
    (tmp_path / "release-manifest.json").write_text(json.dumps(candidate))
    (tmp_path / "release-notes.md").write_text("migration only")
    tag = helper.tag_for(channel, {"source_sha": "a" * 40, "upstream_manifest_version": "3.2.2"})
    calls = []
    def api(*args):
        calls.append(args)
        endpoint = args[1]
        if endpoint.endswith("branches/main"):
            return {"protected": True, "commit": {"sha": "c" * 40}}
        if endpoint.endswith("/releases"):
            return [[]]
        if "/matching-refs/" in endpoint:
            return [{"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": "d" * 40}}] if failure == "tag-conflict" else []
        if "/releases/tags/" in endpoint:
            return {"draft": True, "prerelease": channel == "edge", "assets": [
                {"name": p.name, "digest": "bad" if failure == "checksum" else "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in tmp_path.iterdir()]}
        return {}
    def run(args, **kwargs):
        calls.append(tuple(args))
        if args[:3] == ["gh", "release", "upload"] and failure == "upload":
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(helper, "gh", api)
    monkeypatch.setattr(helper.subprocess, "run", run)
    monkeypatch.setattr(helper, "discover_sources", lambda *a, **kw: [{
        "channel": channel, "source_ref": ref, "source_sha": ("b" if failure == "stale-source" else "a") * 40}])
    if failure:
        with pytest.raises((ReleaseError, subprocess.CalledProcessError)):
            helper.publish(channel, tmp_path, helper.asset_digest(tmp_path), tag)
        assert not any(call[:3] == ("gh", "release", "edit") for call in calls)
    else:
        helper.publish(channel, tmp_path, helper.asset_digest(tmp_path), tag)
        create = next(call for call in calls if call[:3] == ("gh", "release", "create"))
        edit = next(call for call in calls if call[:3] == ("gh", "release", "edit"))
        assert "--draft" in create
        assert calls.index(create) < calls.index(edit)
        assert "--draft=false" in edit


def test_manifest_timestamp_is_pinned_to_builder_commit(monkeypatch, tmp_path):
    helper = load_helper()
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    source = {"source_sha": "a" * 40, "source_ref": "v3.2.2"}
    (tmp_path / "selected-source.json").write_text(json.dumps(source))
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated/provenance.json").write_text(json.dumps({
        "upstream_sha": "a" * 40, "upstream_ref": "v3.2.2", "upstream_manifest_version": "3.2.2",
        "converter_version": "0.1.0", "mapping_hash": "d" * 64,
    }))
    calls = []
    monkeypatch.setattr(helper, "build_release_manifest", lambda **kw: calls.append(kw))
    monkeypatch.setattr(helper, "_trusted_scenario_hash", lambda *a: "f" * 64)
    monkeypatch.setattr(helper.subprocess, "check_output", lambda *a, **kw: "2026-10-26T12:00:00Z\n")
    helper.manifest("stable", tmp_path)
    helper.manifest("stable", tmp_path)
    assert calls[0] == calls[1]
    assert calls[0]["generated_at"] == "2026-10-26T12:00:00Z"
