"""Inventory the installed distribution size without Codex or model execution."""
import json
import subprocess
import sys

from hve_codex_porter.host_acceptance import (
    _MAX_NATIVE_WORKSPACE_FILES,
    _MAX_NATIVE_WORKSPACE_INVENTORY_OUTPUT_BYTES,
    _NATIVE_WORKSPACE_SCRIPT,
)


def test_native_inventory_retains_tracking_after_more_than_8192_package_files(tmp_path):
    package = tmp_path / ".agents" / "originals"
    package.mkdir(parents=True)
    for index in range(8200):
        (package / f"{index:05}.txt").write_bytes(b"source")
    tracking = tmp_path / ".copilot-tracking" / "research"
    tracking.mkdir(parents=True)
    (tracking / "result.md").write_bytes(b"research evidence")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _NATIVE_WORKSPACE_SCRIPT, str(tmp_path), "inventory"],
        input=b"null", capture_output=True, timeout=60, check=True,
    )
    response = json.loads(result.stdout)
    assert response["complete"], response["errors"]
    assert len(response["files"]) == 8201
    assert any(item["path"] == ".copilot-tracking/research/result.md" for item in response["files"])
    assert len(response["files"]) <= _MAX_NATIVE_WORKSPACE_FILES
    assert len(result.stdout) <= _MAX_NATIVE_WORKSPACE_INVENTORY_OUTPUT_BYTES
