from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hve_codex_porter import release, runtime_evidence


SOURCE_SHA = "a" * 40
ADAPTER_SHA = "b" * 40
CONVERTER_SHA = "c" * 40
HASH = "d" * 64
TRUSTED_VERIFIER = Path(release.__file__).resolve().parents[2] / "vally-executor" / "verify.mjs"


def _write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    return data


def _copy_approved_verifier(adapter_root: Path) -> str:
    source = TRUSTED_VERIFIER
    assert source.is_file(), "the checked-out approved Vally verifier must exist"
    adapter_root.mkdir(parents=True, exist_ok=True)
    target = adapter_root / "verify.mjs"
    shutil.copyfile(source, target)
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _make_capture(candidate_root: Path, custom_root: Path) -> Path:
    source_to_output = _write_json(candidate_root / "generated/source-to-output.json", {"version": 1})
    source_inventory = _write_json(candidate_root / "generated/source-inventory.json", {"version": 1})

    bundle = candidate_root / "generated/runtime-evidence/bundle"
    run = candidate_root / "generated/runtime-evidence/run"
    (bundle / "generated").mkdir(parents=True, exist_ok=True)
    run.mkdir(parents=True, exist_ok=True)
    (bundle / "generated/source-to-output.json").write_bytes(source_to_output)
    (bundle / "generated/source-inventory.json").write_bytes(source_inventory)

    expected_custom_files = []
    for original in sorted(custom_root.rglob("*")):
        if original.is_file():
            relative = original.relative_to(custom_root).as_posix()
            data = original.read_bytes()
            staged = bundle / "custom" / Path(*relative.split("/"))
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(data)
            expected_custom_files.append(
                {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}
            )
    assert expected_custom_files
    _write_json(
        bundle / "generated/vally-bundle-report.json",
        {"source": {"custom": {"files": expected_custom_files}}},
    )
    evidence = {
        "vally": {
            "bundle_path": "generated/runtime-evidence/bundle",
            "run_path": "generated/runtime-evidence/run",
        },
        # The verifier must derive its own receipt; this envelope claim is deliberately false.
        "vally_verification": {"verified": False},
    }
    evidence_path = candidate_root / "generated/runtime-evidence.json"
    _write_json(evidence_path, evidence)
    return evidence_path


def _spy_real_verifier(monkeypatch, *, returncode: int):
    calls = []
    original = runtime_evidence.verify_candidate_vally

    def verify(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    receipt = {
        "mode": "vally-evidence-verification",
        "verified": True,
        "fullSuiteExecution": True,
    }
    commands = []

    def fake_run(command, *, stdout, stderr, timeout, check):
        commands.append((command, timeout, check))
        stdout.write((json.dumps(receipt) + "\n").encode())
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(runtime_evidence, "verify_candidate_vally", verify)
    monkeypatch.setattr(runtime_evidence.subprocess, "run", fake_run)
    return calls, commands


def test_eligibility_uses_real_file_verifier_and_passes_receipt_to_policy(tmp_path, monkeypatch):
    installed_root = tmp_path / "installed"
    release_file = installed_root / "src/hve_codex_porter/release.py"
    release_file.parent.mkdir(parents=True)
    monkeypatch.setattr(release, "__file__", str(release_file))

    approved_adapter = installed_root / "vally-executor"
    adapter_hash = _copy_approved_verifier(approved_adapter)
    custom_root = installed_root / "tests/vally-custom/stable"
    custom_root.mkdir(parents=True)
    (custom_root / "selection.json").write_text('{"case":"stable"}\n', encoding="utf-8")

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    evidence_path = _make_capture(candidate, custom_root)
    evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    output_hashes = {"plugins/hve-core-codex/plugin.json": HASH}
    manifest = {
        "channel": "stable",
        "source": {"repository": "microsoft/hve-core", "sha": SOURCE_SHA},
        "adapter_sha": ADAPTER_SHA,
        "converter": {"sha": CONVERTER_SHA},
        "mapping_sha256": HASH,
        "scenario_definition_sha256": HASH,
        "runtime_evidence_sha256": evidence_hash,
        "output_hashes": output_hashes,
    }
    manifest_path = candidate / "generated/release-manifest.json"
    report_path = candidate / "generated/compatibility-report.json"
    _write_json(manifest_path, manifest)
    _write_json(report_path, {})

    monkeypatch.setattr(release, "hash_generated_outputs", lambda _root: output_hashes)
    monkeypatch.setattr(release, "_checksums_file_blockers", lambda _root, _hashes: [])
    policy_calls = []

    def policy(**kwargs):
        policy_calls.append(kwargs)
        return {"eligible": True, "blockers": [], "status": "eligible"}

    monkeypatch.setattr(release, "publication_eligibility", policy)
    verifier_calls, commands = _spy_real_verifier(monkeypatch, returncode=0)
    args = SimpleNamespace(
        compatibility_report=report_path,
        release_manifest=manifest_path,
        runtime_evidence=evidence_path,
        root=candidate,
        channel="stable",
        upstream_prerelease=False,
        opt_in=False,
        merged_commit=None,
        protected_branch=False,
    )

    decision = release._cmd_eligibility(args)

    assert decision["eligible"] is True
    assert verifier_calls[0]["candidate_root"] == candidate
    assert verifier_calls[0]["approved_adapter_root"] == approved_adapter
    assert verifier_calls[0]["expected_source_sha"] == SOURCE_SHA
    assert verifier_calls[0]["expected_custom_root"] == custom_root
    assert hashlib.sha256((approved_adapter / "verify.mjs").read_bytes()).hexdigest() == adapter_hash
    assert commands[0][1:] == (120, False)
    command = commands[0][0]
    assert command[command.index("--approved-adapter-root") + 1] == str(approved_adapter)
    assert command[command.index("--expected-upstream-sha") + 1] == SOURCE_SHA
    receipt = policy_calls[0]["vally_verification"]
    assert receipt["verified"] is True
    assert receipt["receipt"]["fullSuiteExecution"] is True


def test_eligibility_propagates_real_verifier_rejection_and_fails_closed(tmp_path, monkeypatch):
    installed_root = tmp_path / "installed"
    release_file = installed_root / "src/hve_codex_porter/release.py"
    release_file.parent.mkdir(parents=True)
    monkeypatch.setattr(release, "__file__", str(release_file))
    approved_adapter = installed_root / "vally-executor"
    _copy_approved_verifier(approved_adapter)
    custom_root = installed_root / "tests/vally-custom/stable"
    custom_root.mkdir(parents=True)
    (custom_root / "selection.json").write_text('{"case":"stable"}\n', encoding="utf-8")

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    evidence_path = _make_capture(candidate, custom_root)
    evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    output_hashes = {"plugins/hve-core-codex/plugin.json": HASH}
    manifest_path = candidate / "generated/release-manifest.json"
    report_path = candidate / "generated/compatibility-report.json"
    _write_json(
        manifest_path,
        {
            "channel": "stable",
            "source": {"repository": "microsoft/hve-core", "sha": SOURCE_SHA},
            "adapter_sha": ADAPTER_SHA,
            "converter": {"sha": CONVERTER_SHA},
            "mapping_sha256": HASH,
            "scenario_definition_sha256": HASH,
            "runtime_evidence_sha256": evidence_hash,
            "output_hashes": output_hashes,
        },
    )
    _write_json(report_path, {})
    monkeypatch.setattr(release, "hash_generated_outputs", lambda _root: output_hashes)
    monkeypatch.setattr(release, "_checksums_file_blockers", lambda _root, _hashes: [])
    policy_calls = []

    def policy(**kwargs):
        policy_calls.append(kwargs)
        return {"eligible": True, "blockers": []}

    monkeypatch.setattr(release, "publication_eligibility", policy)
    verifier_calls, _commands = _spy_real_verifier(monkeypatch, returncode=7)
    args = SimpleNamespace(
        compatibility_report=report_path,
        release_manifest=manifest_path,
        runtime_evidence=evidence_path,
        root=candidate,
        channel="stable",
        upstream_prerelease=False,
        opt_in=False,
        merged_commit=None,
        protected_branch=False,
    )

    decision = release._cmd_eligibility(args)

    assert verifier_calls[0]["candidate_root"] == candidate
    assert decision["eligible"] is False
    assert decision["exit_failure"] is True
    assert any("Independent Vally full-suite verification did not pass" in item for item in decision["blockers"])
    assert policy_calls[0]["vally_verification"]["verified"] is False


@pytest.mark.parametrize("returncode", [0, 7])
def test_sync_eligibility_uses_baseline_adapter_and_channel_custom_sources(
    tmp_path, monkeypatch, returncode
):
    candidate = tmp_path / "candidate"
    baseline = tmp_path / "baseline"
    candidate.mkdir()
    approved_adapter = baseline / "vally-executor"
    adapter_hash = _copy_approved_verifier(approved_adapter)
    custom_root = baseline / "tests/vally-custom/stable"
    custom_root.mkdir(parents=True)
    (custom_root / "selection.json").write_text('{"case":"stable"}\n', encoding="utf-8")
    output_hashes = {"plugins/hve-core-codex/plugin.json": HASH}

    _write_json(baseline / "mappings/sample.yaml", {"fixture": True})
    for relative, body in (
        ("schemas/runtime-acceptance.json", {"version": 2}),
        ("tests/codex-host/stable.json", {"scope": "stable"}),
        ("tests/codex-host/edge.json", {"scope": "edge"}),
    ):
        _write_json(baseline / relative, body)
    _write_json(baseline / "generated/source-inventory.json", {"artifacts": []})
    _write_json(candidate / "generated/compatibility-report.json", {})
    _write_json(candidate / "generated/source-inventory.json", {"artifacts": []})
    evidence_path = _make_capture(candidate, custom_root)
    evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    _write_json(
        candidate / "generated/release-manifest.json",
        {
            "channel": "stable",
            "source": {
                "repository": "microsoft/hve-core",
                "ref": "v3.2.2",
                "sha": SOURCE_SHA,
            },
            "adapter_sha": ADAPTER_SHA,
            "converter": {"sha": ADAPTER_SHA},
            "mapping_sha256": HASH,
            "scenario_definition_sha256": HASH,
            "runtime_evidence_sha256": evidence_hash,
            "output_hashes": output_hashes,
        },
    )
    checksums_path = candidate / "generated/checksums.txt"
    checksums_path.parent.mkdir(parents=True, exist_ok=True)
    checksums_path.write_text("fixture checksum\n", encoding="utf-8")

    monkeypatch.setattr(release, "hash_generated_outputs", lambda _root: output_hashes)
    monkeypatch.setattr(
        release,
        "load_ledger",
        lambda _path: {
            "processed": [
                {
                    "channel": "stable",
                    "source_sha": SOURCE_SHA,
                    "source_ref": "v3.2.2",
                    "outcome": "pr-opened",
                }
            ]
        },
    )
    gate_calls = []

    def sync_gate(**kwargs):
        gate_calls.append(kwargs)
        return {"eligible": True, "blockers": []}

    monkeypatch.setattr(release, "sync_auto_merge_eligibility", sync_gate)
    verifier_calls, commands = _spy_real_verifier(monkeypatch, returncode=returncode)
    args = SimpleNamespace(
        root=candidate,
        baseline_root=baseline,
        channel="stable",
        expected_source_sha=SOURCE_SHA,
        expected_adapter_revision=ADAPTER_SHA,
    )

    decision = release._cmd_sync_eligibility(args)

    assert verifier_calls[0]["candidate_root"] == candidate.resolve()
    assert verifier_calls[0]["approved_adapter_root"] == approved_adapter.resolve()
    assert verifier_calls[0]["expected_source_sha"] == SOURCE_SHA
    assert verifier_calls[0]["expected_custom_root"] == custom_root.resolve()
    assert hashlib.sha256((approved_adapter / "verify.mjs").read_bytes()).hexdigest() == adapter_hash
    assert commands, gate_calls[0]["vally_verification"]
    assert commands[0][0][commands[0][0].index("--approved-adapter-root") + 1] == str(approved_adapter.resolve())
    assert gate_calls[0]["vally_verification"]["verified"] is (returncode == 0)
    if returncode == 0:
        assert decision["eligible"] is True
        assert "exit_failure" not in decision
    else:
        assert decision["eligible"] is False
        assert decision["exit_failure"] is True
        assert decision["status"] == "manual-review-required"
        assert decision["action"] is None
        assert any("Independent Vally full-suite verification did not pass" in item for item in decision["blockers"])
