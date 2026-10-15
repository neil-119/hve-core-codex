"""CLI boundary tests for offline Vally receipt verification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from hve_codex_porter import cli


SOURCE_SHA = "a" * 40


def argv_for(tmp_path: Path, source_sha: str = SOURCE_SHA, output: Path | None = None) -> list[str]:
    args = [
        "eval",
        "vally-verify",
        "--bundle",
        str(tmp_path / "bundle"),
        "--run",
        str(tmp_path / "run"),
        "--source-sha",
        source_sha,
        "--node-command",
        "node-test-double",
    ]
    if output is not None:
        args.extend(["--output", str(output)])
    return args


def completed(receipt: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["node-test-double"], returncode=returncode, stdout=receipt, stderr=""
    )


def test_rejects_non_exact_source_sha_before_starting_verifier(tmp_path: Path, monkeypatch, capsys):
    node = Mock()
    monkeypatch.setattr(cli.subprocess, "run", node)

    for invalid_sha in ("a" * 39, "a" * 41, "g" * 40):
        assert cli.main(argv_for(tmp_path, source_sha=invalid_sha)) == 2
        assert "exact 40-character upstream commit" in capsys.readouterr().err

    node.assert_not_called()


def test_uses_repository_adapter_and_saves_verified_receipt(tmp_path: Path, monkeypatch, capsys):
    repository = tmp_path / "checkout"
    adapter = repository / "vally-executor"
    adapter.mkdir(parents=True)
    packaged_adapter = tmp_path / "package-data" / "vally-executor"
    packaged_adapter.mkdir(parents=True)
    monkeypatch.setattr(cli, "REPO_ROOT", repository)
    monkeypatch.setattr(cli, "DATA_ROOT", tmp_path / "package-data")

    receipt = {
        "mode": "vally-evidence-verification",
        "verified": True,
        "sourceSha": SOURCE_SHA,
        "blockers": [],
    }
    node = Mock(return_value=completed(json.dumps(receipt)))
    monkeypatch.setattr(cli.subprocess, "run", node)
    saved = tmp_path / "evidence" / "verified.json"

    assert cli.main(argv_for(tmp_path, output=saved)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == receipt
    assert json.loads(saved.read_text(encoding="utf-8")) == receipt

    command = node.call_args.args[0]
    assert command == [
        "node-test-double",
        str((adapter / "verify.mjs").resolve()),
        "--bundle",
        str((tmp_path / "bundle").resolve()),
        "--run",
        str((tmp_path / "run").resolve()),
        "--expected-upstream-sha",
        SOURCE_SHA,
        "--approved-adapter-root",
        str(adapter.resolve()),
    ]
    assert node.call_args.kwargs == {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": 120,
        "check": False,
    }


def test_uses_packaged_adapter_when_repository_copy_is_absent(tmp_path: Path, monkeypatch, capsys):
    repository = tmp_path / "checkout-without-adapter"
    repository.mkdir()
    data_root = tmp_path / "package-data"
    adapter = data_root / "vally-executor"
    adapter.mkdir(parents=True)
    monkeypatch.setattr(cli, "REPO_ROOT", repository)
    monkeypatch.setattr(cli, "DATA_ROOT", data_root)
    node = Mock(return_value=completed(json.dumps({"mode": "vally-evidence-verification", "verified": True})))
    monkeypatch.setattr(cli.subprocess, "run", node)

    uppercase_sha = SOURCE_SHA.upper()
    assert cli.main(argv_for(tmp_path, source_sha=uppercase_sha)) == 0
    capsys.readouterr()
    command = node.call_args.args[0]
    assert command[1] == str((adapter / "verify.mjs").resolve())
    assert command[-1] == str(adapter.resolve())
    assert command[command.index("--expected-upstream-sha") + 1] == SOURCE_SHA


def test_parser_does_not_allow_a_caller_selected_adapter_root(tmp_path: Path, capsys):
    with pytest.raises(SystemExit) as error:
        cli.parser().parse_args(argv_for(tmp_path) + ["--approved-adapter-root", str(tmp_path / "untrusted")])

    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("returncode", "verified"),
    [(0, False), (7, True), (7, False)],
)
def test_nonverified_receipt_or_nonzero_verifier_exit_fails(
    tmp_path: Path, monkeypatch, capsys, returncode: int, verified: bool
):
    receipt = {"mode": "vally-evidence-verification", "verified": verified, "blockers": ["source mismatch"]}
    monkeypatch.setattr(cli.subprocess, "run", Mock(return_value=completed(json.dumps(receipt), returncode)))

    assert cli.main(argv_for(tmp_path)) == 1
    assert json.loads(capsys.readouterr().out) == receipt


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("{not-json", "could not produce a receipt"),
        ("[]", "invalid receipt"),
        (json.dumps({"mode": "vally-run", "verified": True}), "invalid receipt"),
    ],
)
def test_rejects_malformed_or_wrong_mode_receipt(
    tmp_path: Path, monkeypatch, capsys, stdout: str, message: str
):
    monkeypatch.setattr(cli.subprocess, "run", Mock(return_value=completed(stdout)))

    assert cli.main(argv_for(tmp_path)) == 2
    assert message in json.loads(capsys.readouterr().err)["error"]


@pytest.mark.parametrize("failure", [OSError("node missing"), subprocess.TimeoutExpired("node", 120)])
def test_verifier_start_or_timeout_failure_is_reported(tmp_path: Path, monkeypatch, capsys, failure):
    node = Mock(side_effect=failure)
    monkeypatch.setattr(cli.subprocess, "run", node)

    assert cli.main(argv_for(tmp_path)) == 2
    assert "could not produce a receipt" in json.loads(capsys.readouterr().err)["error"]
    node.assert_called_once()
