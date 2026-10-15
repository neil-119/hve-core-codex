from __future__ import annotations

import hashlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/publish_edge_snapshot.py"
SPEC = importlib.util.spec_from_file_location("publish_edge_snapshot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
edge_snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(edge_snapshot)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_asset(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    checksum = hashlib.sha256(content).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{checksum}  {path.name}\n", encoding="ascii")


def _validation_payload(port_sha: str) -> dict:
    return {
        "workflow_runs": [
            {
                "id": 12345,
                "path": ".github/workflows/validate.yml",
                "head_branch": "main",
                "event": "push",
                "head_sha": port_sha,
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/example/project/actions/runs/12345",
            }
        ]
    }


def test_validation_requires_success_for_exact_commit_and_workflow():
    source = "a" * 40
    run = edge_snapshot.successful_validation_run(_validation_payload(source), source)
    assert run["id"] == 12345

    wrong_sha = _validation_payload("b" * 40)
    with pytest.raises(edge_snapshot.SnapshotError, match="exact commit"):
        edge_snapshot.successful_validation_run(wrong_sha, source)

    failed = _validation_payload(source)
    failed["workflow_runs"][0]["conclusion"] = "failure"
    with pytest.raises(edge_snapshot.SnapshotError, match="exact commit"):
        edge_snapshot.successful_validation_run(failed, source)


def test_tag_requires_a_full_source_sha_and_positive_revision():
    source = "f" * 40
    assert edge_snapshot.derive_tag(source, "3") == f"hve-core-edge-{source}-codex.3"
    for revision in ("0", "-1", "01", "latest"):
        with pytest.raises(edge_snapshot.SnapshotError, match="positive integer"):
            edge_snapshot.derive_tag(source, revision)


def test_prepare_assets_checks_hashes_and_records_unsigned_edge_limits(tmp_path: Path):
    source_sha = "b" * 40
    port_sha = "a" * 40
    distribution = tmp_path / "dist"
    generated = distribution / "generated"
    generated.mkdir(parents=True)
    _write_json(
        generated / "compatibility-report.json",
        {"upstream_ref": "main", "upstream_sha": source_sha, "channel": "edge"},
    )
    (generated / "checksums.txt").write_text("generated/compatibility-report.json  abc\n", encoding="utf-8")

    _write_asset(tmp_path / "dist.zip", b"portable distribution")
    _write_asset(tmp_path / "dist.tar.gz", b"unix distribution")
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    _write_asset(wheel_dir / "hve_core_codex-0.1.0-py3-none-any.whl", b"installer wheel")

    validation_json = tmp_path / "validate-runs.json"
    _write_json(validation_json, _validation_payload(port_sha))
    asset_dir = tmp_path / "assets"
    output = tmp_path / "github-output.txt"
    args = Namespace(
        distribution=distribution,
        wheel_dir=wheel_dir,
        asset_dir=asset_dir,
        validation_runs_json=validation_json,
        port_commit=port_sha,
        port_revision="1",
        github_output=output,
    )

    tag = edge_snapshot._prepare_assets(args)
    assert tag == f"hve-core-edge-{source_sha}-codex.1"
    assert (asset_dir / "dist.zip").is_file()
    assert (asset_dir / "dist.tar.gz").is_file()
    assert (asset_dir / "hve_core_codex-0.1.0-py3-none-any.whl").is_file()
    notes = (asset_dir / "release-notes.md").read_text(encoding="utf-8")
    assert "58.6%" in notes
    assert "not a score for this snapshot" in notes
    assert "Desktop validation remains manual" in notes
    assert "blocked or unverified cases remain listed" in notes
    assert "No paid model calls" in notes
    assert "No GitHub artifact attestation, GPG signature, or release-app signature" in notes

    (asset_dir / "dist.zip.sha256").write_text("0" * 64 + "  dist.zip\n", encoding="ascii")
    with pytest.raises(edge_snapshot.SnapshotError, match="SHA-256 mismatch"):
        edge_snapshot.verify_asset_directory(asset_dir, port_commit=port_sha, port_revision="1")
