"""Effective configuration and persisted trust are separate acceptance prerequisites."""
from unittest.mock import MagicMock, patch

import pytest

from hve_codex_porter.host import probe_existing_project


@pytest.mark.parametrize("disabled,trust,ready", [(True, "trusted", False), (False, "untrusted", False), (False, "trusted", True)])
def test_existing_project_requires_loaded_config_and_real_hook_trust(tmp_path, disabled, trust, ready):
    layer = {"name": {"type": "project", "dotCodexFolder": str(tmp_path / ".codex")},
             "disabledReason": "exact project trust required" if disabled else None}
    hooks = {"data": [{"cwd": str(tmp_path), "hooks": [
        {"sourcePath": str(tmp_path / ".codex/hooks.json"), "trustStatus": trust, "enabled": True}
    ]}]}
    client = MagicMock()
    client.initialize.return_value = {"ok": True}
    client.request.side_effect = [{"ok": True, "result": {"layers": [layer]}}, {"ok": True, "result": hooks}]
    with patch("hve_codex_porter.host._AppServerClient") as factory:
        factory.return_value.__enter__.return_value = client
        result = probe_existing_project(tmp_path, "codex")
    assert result["ready"] is ready
    assert result["trust_modified"] is False
    assert result["model_sessions_started"] == 0
    assert [call.args[0] for call in client.request.call_args_list] == ["config/read", "hooks/list"]
