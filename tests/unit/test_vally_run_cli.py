"""The public Vally command uses standard Codex and rejects retired fork options."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from hve_codex_porter import cli


def test_standard_codex_dry_run_is_forwarded(tmp_path, monkeypatch, capsys):
    repository = tmp_path / "checkout"
    (repository / "vally-executor").mkdir(parents=True)
    monkeypatch.setattr(cli, "REPO_ROOT", repository)
    output = tmp_path / "run"
    payload = {"mode": "vally-run", "status": "dry-run", "exitCode": 0}

    def run(command, **kwargs):
        output.mkdir()
        (output / "run-report.json").write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    node = Mock(side_effect=run)
    monkeypatch.setattr(cli.subprocess, "run", node)
    assert cli.main(["eval", "vally-run", "--bundle", str(tmp_path / "bundle"),
                     "--output", str(output), "--codex-binary", "codex", "--reasoning-effort", "medium", "--dry-run"]) == 0
    capsys.readouterr()
    command = node.call_args.args[0]
    assert command[command.index("--codex-binary") + 1] == "codex"
    assert command[command.index("--reasoning-effort") + 1] == "medium"
    assert "--dry-run" in command


def test_judge_backend_defaults_to_none_and_codex_is_forwarded(tmp_path, monkeypatch, capsys):
    parsed = cli.parser().parse_args(["eval", "vally-run", "--bundle", str(tmp_path / "bundle"),
                                      "--output", str(tmp_path / "run")])
    assert parsed.judge_backend == "none"

    repository = tmp_path / "checkout"
    (repository / "vally-executor").mkdir(parents=True)
    monkeypatch.setattr(cli, "REPO_ROOT", repository)
    output = tmp_path / "run-codex-judge"
    payload = {"mode": "vally-run", "status": "dry-run", "exitCode": 0}

    def run(command, **kwargs):
        output.mkdir()
        (output / "run-report.json").write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    node = Mock(side_effect=run)
    monkeypatch.setattr(cli.subprocess, "run", node)
    assert cli.main(["eval", "vally-run", "--bundle", str(tmp_path / "bundle"),
                     "--output", str(output), "--judge-backend", "codex", "--dry-run"]) == 0
    capsys.readouterr()
    command = node.call_args.args[0]
    assert command[command.index("--judge-backend") + 1] == "codex"


@pytest.mark.parametrize("retired", ["--luna-judge-binary", "--luna-judge-sha256", "--enable-eval-mcp"])
def test_retired_fork_options_cannot_start_node(tmp_path, monkeypatch, capsys, retired):
    node = Mock()
    monkeypatch.setattr(cli.subprocess, "run", node)
    with pytest.raises(SystemExit) as error:
        cli.main(["eval", "vally-run", "--bundle", str(tmp_path / "bundle"),
                  "--output", str(tmp_path / "run"), retired, "unused"])
    assert error.value.code == 2
    capsys.readouterr()
    node.assert_not_called()
