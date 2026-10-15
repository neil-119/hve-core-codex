from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hve_codex_porter.runtime_evidence import verify_candidate_vally


@pytest.fixture
def capture(tmp_path: Path):
    candidate = tmp_path / "candidate"
    bundle = candidate / "generated/runtime-evidence/bundle"
    run = candidate / "generated/runtime-evidence/run"
    adapter = tmp_path / "protected/vally-executor"
    run.mkdir(parents=True)
    adapter.mkdir(parents=True)
    (adapter / "verify.mjs").write_text("// protected verifier\n")
    for root in (candidate, bundle):
        (root / "generated").mkdir(parents=True, exist_ok=True)
        (root / "generated/source-to-output.json").write_text('{"source":"mapped"}\n')
        (root / "generated/source-inventory.json").write_text('{"upstream_sha":"' + "a" * 40 + '"}\n')
    return {
        "candidate_root": candidate, "approved_adapter_root": adapter,
        "expected_source_sha": "a" * 40,
        "evidence": {"result": "passed", "vally": {
            "bundle_path": "generated/runtime-evidence/bundle",
            "run_path": "generated/runtime-evidence/run",
            "approved_adapter_root": "attacker-supplied-ignored",
            "verified": True,
        }},
    }


def _verifier(receipt: object, returncode: int = 0):
    def execute(command, *, stdout, stderr, timeout, check):
        stdout.write(json.dumps(receipt).encode())
        return subprocess.CompletedProcess(command, returncode)
    return execute


def test_actual_verifier_uses_protected_code_and_full_suite(capture):
    receipt = {"mode": "vally-evidence-verification", "verified": True, "fullSuiteExecution": True}
    with patch("hve_codex_porter.runtime_evidence.subprocess.run", side_effect=_verifier(receipt)) as run:
        result = verify_candidate_vally(**capture)
    assert result["verified"] is True
    command = run.call_args.args[0]
    assert command[1] == str(capture["approved_adapter_root"] / "verify.mjs")
    assert command[-1] == str(capture["approved_adapter_root"])
    assert "attacker-supplied-ignored" not in command


@pytest.mark.parametrize("receipt,code", [
    ({"mode": "vally-evidence-verification", "verified": True, "fullSuiteExecution": False}, 0),
    ({"mode": "vally-evidence-verification", "verified": True, "fullSuiteExecution": True}, 1),
    ({"mode": "vally-evidence-verification", "verified": False, "fullSuiteExecution": True}, 0),
    ({"verified": True, "fullSuiteExecution": True}, 0),
])
def test_envelope_claim_cannot_override_verifier_failure(capture, receipt, code):
    with patch("hve_codex_porter.runtime_evidence.subprocess.run", side_effect=_verifier(receipt, code)):
        assert verify_candidate_vally(**capture)["verified"] is False


def test_changed_candidate_source_rejected_before_verifier(capture):
    (capture["candidate_root"] / "generated/source-to-output.json").write_text("{}\n")
    with patch("hve_codex_porter.runtime_evidence.subprocess.run") as run:
        result = verify_candidate_vally(**capture)
    assert not result["verified"]
    assert "does not match" in result["blockers"][0]
    run.assert_not_called()


@pytest.mark.parametrize("path", ["../external", "C:/external", "generated/runtime-evidence/../../external"])
def test_external_or_traversing_capture_is_rejected(capture, path):
    capture["evidence"]["vally"]["bundle_path"] = path
    with patch("hve_codex_porter.runtime_evidence.subprocess.run") as run:
        assert verify_candidate_vally(**capture)["verified"] is False
    run.assert_not_called()


def test_timeout_is_failed_evidence(capture):
    with patch("hve_codex_porter.runtime_evidence.subprocess.run",
               side_effect=subprocess.TimeoutExpired("node", 120)):
        assert verify_candidate_vally(**capture)["verified"] is False


def test_required_custom_cases_cannot_be_omitted(capture, tmp_path):
    approved = tmp_path / "custom"
    (approved / "evals/codex-port-edge").mkdir(parents=True)
    (approved / "evals/codex-port-edge/eval.yaml").write_text("name: required-custom\n")
    with patch("hve_codex_porter.runtime_evidence.subprocess.run") as run:
        result = verify_candidate_vally(**capture, expected_custom_root=approved)
    assert not result["verified"]
    run.assert_not_called()


def test_matching_custom_archives_require_complete_declared_set(capture, tmp_path):
    approved = tmp_path / "custom"
    (approved / "evals/codex-port-edge").mkdir(parents=True)
    raw = b"name: required-custom\n"
    (approved / "evals/codex-port-edge/eval.yaml").write_bytes(raw)
    bundle = capture["candidate_root"] / capture["evidence"]["vally"]["bundle_path"]
    archive = bundle / "custom/evals/codex-port-edge/eval.yaml"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(raw)
    (bundle / "generated/vally-bundle-report.json").write_text('{"source":{"custom":{"files":[]}}}')
    with patch("hve_codex_porter.runtime_evidence.subprocess.run") as run:
        result = verify_candidate_vally(**capture, expected_custom_root=approved)
    assert not result["verified"]
    assert "exact approved custom" in result["blockers"][0]
    run.assert_not_called()
