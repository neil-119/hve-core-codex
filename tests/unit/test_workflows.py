from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from ruamel.yaml import YAML


FINGERPRINT_PATH = Path(__file__).resolve().parents[2] / ".github/scripts/build_input_fingerprint.py"
_fingerprint_spec = importlib.util.spec_from_file_location("build_input_fingerprint", FINGERPRINT_PATH)
assert _fingerprint_spec and _fingerprint_spec.loader
_fingerprint_module = importlib.util.module_from_spec(_fingerprint_spec)
_fingerprint_spec.loader.exec_module(_fingerprint_module)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> dict:
    return YAML(typ="safe").load((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.mark.skipif(os.name == "nt" or not shutil.which("jq"), reason="Requires POSIX bash and jq")
@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
@pytest.mark.parametrize("state", ["missing", "unprotected", "protected", "api-error"])
def test_channel_preflight_skips_only_scheduled_setup_gaps(tmp_path, event, state):
    workflow = _load_workflow("sync-upstream.yml")
    script = workflow["jobs"]["preflight"]["steps"][0]["run"]
    response = {"missing": "", "unprotected": '{"protected":false}', "protected": '{"protected":true}', "api-error": ""}[state]
    env = os.environ | {
        "CHANNEL": "edge", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_EVENT_NAME": event,
        "GITHUB_OUTPUT": str(tmp_path / "output"), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "BRANCH_RESPONSE": response, "API_STATUS": "1" if state == "api-error" else "0",
    }
    stub = 'gh() { printf "%s" "$BRANCH_RESPONSE"; return "$API_STATUS"; }\n'
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", stub + script], env=env, capture_output=True, text=True)
    expected_success = state == "protected" or (event == "schedule" and state != "api-error")
    assert (result.returncode == 0) == expected_success
    output = tmp_path / "output"
    if state != "api-error":
        assert output.read_text().strip() == f"ready={'true' if state == 'protected' else 'false'}"
    if state in {"missing", "unprotected"}:
        assert "No sync or publication was attempted" in (tmp_path / "summary").read_text()


def test_unconfigured_channels_cannot_reach_build_or_publication():
    sync = _load_workflow("sync-upstream.yml")
    weekly = _load_workflow("weekly-sync.yml")
    assert sync["jobs"]["build"]["needs"] == "preflight"
    assert sync["jobs"]["build"]["if"] == "needs.preflight.outputs.ready == 'true'"
    for channel in ("stable", "edge"):
        assert weekly["jobs"][f"publish-{channel}"]["if"] == f"needs.sync-{channel}.outputs.ready == 'true'"


def test_weekly_schedule_checks_stable_and_edge_only():
    weekly = _load_workflow("weekly-sync.yml")
    sync = _load_workflow("sync-upstream.yml")
    publish = _load_workflow("publish.yml")

    assert weekly["on"]["schedule"] == [{"cron": "0 9 * * 1"}]
    assert {job["with"]["channel"] for job in weekly["jobs"].values()} == {"stable", "edge"}
    assert {job["uses"] for job in weekly["jobs"].values()} == {
        "./.github/workflows/sync-upstream.yml", "./.github/workflows/publish.yml",
    }
    assert weekly["jobs"]["publish-stable"]["needs"] == "sync-stable"
    assert weekly["jobs"]["publish-edge"]["needs"] == "sync-edge"
    assert weekly["permissions"] == {"actions": "read", "contents": "read"}
    for name in ("sync-stable", "sync-edge"):
        assert weekly["jobs"][name]["permissions"] == {
            "actions": "read", "contents": "write", "pull-requests": "write",
        }
    for name in ("publish-stable", "publish-edge"):
        assert weekly["jobs"][name]["permissions"] == {"actions": "read", "contents": "write"}
    assert "schedule" not in sync["on"]
    assert "workflow_call" in sync["on"]
    assert sync["on"]["workflow_dispatch"]["inputs"]["channel"]["options"] == ["stable", "prerelease", "edge"]
    assert "schedule" not in publish["on"]
    assert "push" not in publish["on"]
    assert "workflow_call" in publish["on"]
    assert publish["on"]["workflow_dispatch"]["inputs"]["channel"]["options"] == ["stable", "edge"]
    gate = publish["jobs"]["release-gate"]
    assert gate["needs"] == "preflight"
    assert gate["if"] == "needs.preflight.outputs.publish == 'true'"
    assert gate["steps"][0]["with"]["ref"] == "${{ needs.preflight.outputs.source_sha }}"
    publisher = publish["jobs"]["publish-release"]
    assert publisher["steps"][0]["with"]["ref"] == "${{ needs.release-gate.outputs.source_sha }}"
    after_recheck = False
    for step in publisher["steps"]:
        if after_recheck:
            assert step["if"] == "steps.recheck.outputs.publish == 'true'"
        if step.get("id") == "recheck":
            after_recheck = True
            assert 'git archive "$WORKFLOW_SHA" src' in step["run"]
    assert after_recheck


def test_sync_builds_external_distribution_and_only_opens_metadata_pr():
    sync = _load_workflow("sync-upstream.yml")
    build_steps = sync["jobs"]["build"]["steps"]
    select = next(step for step in build_steps if step.get("id") == "select")
    build = next(step for step in build_steps if step.get("name") == "Build the exact upstream commit in an external directory")
    assert "--limit 1" in select["run"]
    assert "generated/processed-sources.json" not in select["run"]
    assert 'lock.get("build_inputs_sha256") == build_inputs_sha256' in select["run"]
    assert "lock.get(\"builder_commit\") == builder_commit" not in select["run"]
    assert 'bash scripts/build.sh --output "$dist"' in build["run"]
    assert 'uv run hve-codex validate --output "$dist" --record' in build["run"]
    assert '"$RUNNER_TEMP/dist"' in build["run"]
    assert sync["jobs"]["build"]["outputs"]["build_inputs_sha256"] == "${{ steps.select.outputs.build_inputs_sha256 }}"
    assert "does not transport reviewer-authored runtime capture files" in sync["jobs"]["open-metadata-pr"]["steps"][-1]["run"]
    pr_steps = sync["jobs"]["open-metadata-pr"]["steps"]
    commit_step = next(step for step in pr_steps if step.get("name", "").startswith("Commit only source-lock"))
    assert 'git add -- "release/$CHANNEL/source-lock.json" "release/$CHANNEL/release-manifest.json" "release/$CHANNEL/checksums.txt"' in commit_step["run"]
    assert "generated/" not in commit_step["run"]
    assert any(step.get("uses", "").startswith("actions/upload-artifact@") for step in build_steps)
    assert sync["jobs"]["open-metadata-pr"]["permissions"] == {
        "actions": "read", "contents": "write", "pull-requests": "write",
    }
    assert not any("create-github-app-token@" in step.get("uses", "") for step in pr_steps)
    assert commit_step["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert "GitHub will not automatically start other workflows" in commit_step["run"]


def test_publisher_attaches_zip_and_verified_installer_assets():
    publish = _load_workflow("publish.yml")
    publisher = publish["jobs"]["publish-release"]
    text = "\n".join(step.get("run", "") for step in publisher["steps"])
    assert publisher["permissions"] == {"actions": "read", "contents": "write"}
    assert "GPG" not in text
    assert "git tag -s" not in text
    assert "git verify-tag" not in text
    assert not any("actions/attest@" in step.get("uses", "") for step in publisher["steps"])
    assert 'uv build --project "$builder_dir"' in text
    assert '"$RUNNER_TEMP/dist.zip"' in text
    assert '"$RUNNER_TEMP/dist.zip.sha256"' in text
    assert '"$RUNNER_TEMP/installer/"*.whl' in text
    assert '"$(basename "$wheel").sha256"' in text
    release_step = next(step for step in publisher["steps"] if step.get("name", "").startswith("Create or verify the immutable unsigned tag"))
    assert release_step["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert "unsigned Git tags without GitHub artifact attestations" in release_step["run"]
    assert "verify assets with the published checksums" in release_step["run"]
    assert release_step["run"].index('git config user.name "github-actions[bot]"') < release_step["run"].index('git tag -a -m')
    assert "create-github-app-token@" not in "\n".join(step.get("uses", "") for step in publisher["steps"])
    rebuild = next(step for step in publish["jobs"]["release-gate"]["steps"] if step.get("name", "").startswith("Rebuild the exact reviewed"))
    recheck = next(step for step in publisher["steps"] if step.get("id") == "recheck")
    assert publish["jobs"]["release-gate"]["permissions"] == {"actions": "read", "contents": "read"}
    assert rebuild["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert rebuild["env"]["GITHUB_REPOSITORY"] == "${{ github.repository }}"
    assert recheck["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert recheck["env"]["GITHUB_REPOSITORY"] == "${{ github.repository }}"
    gate = next(step for step in publish["jobs"]["release-gate"]["steps"] if step.get("name") == "Check branch, channel opt-in, and release eligibility")
    assert '--acceptance-policy mappings/release-acceptance.json' in gate["run"]
    assert '--acceptance-policy mappings/release-acceptance.json' in recheck["run"]


def test_build_input_fingerprint_ignores_docs_and_unrelated_tests_but_tracks_converter_changes(tmp_path):
    for directory in ("src/pkg", "mappings", "schemas", "scripts", "tests/codex-host"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    files = {
        "src/pkg/converter.py": "def convert(): return 1\n",
        "mappings/instruction.yaml": "rules: []\n",
        "schemas/runtime-acceptance.json": "{}\n",
        "scripts/build.sh": "#!/bin/sh\n",
        "scripts/build_distribution.py": "print('build')\n",
        "tests/codex-host/stable.json": "{}\n",
        "tests/codex-host/edge.json": "{}\n",
    }
    for relative, content in files.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")

    (tmp_path / "vally-executor").mkdir(exist_ok=True)
    (tmp_path / "vally-executor/navigation.json").write_text('{"guidance":"fixture"}')
    initial = _fingerprint_module.fingerprint(tmp_path)
    (tmp_path / "README.md").write_text("documentation changed\n", encoding="utf-8")
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/unit/test_converter.py").write_text("def test_new_case(): pass\n", encoding="utf-8")
    (tmp_path / "src/pkg/__pycache__").mkdir()
    (tmp_path / "src/pkg/__pycache__/converter.cpython-311.pyc").write_bytes(b"cache noise")
    assert _fingerprint_module.fingerprint(tmp_path) == initial

    (tmp_path / "src/pkg/converter.py").write_text("def convert(): return 2\n", encoding="utf-8")
    assert _fingerprint_module.fingerprint(tmp_path) != initial


def test_sync_selector_ignores_metadata_only_head_movement_and_reviews_builder_changes(tmp_path):
    sync = _load_workflow("sync-upstream.yml")
    select = next(step for step in sync["jobs"]["build"]["steps"] if step.get("id") == "select")
    python_block = re.search(
        r"^[ \t]*python\b[^\n]*<<'PY'[ \t]*\r?\n(?P<script>.*?)^[ \t]*PY[ \t]*$",
        select["run"],
        re.MULTILINE | re.DOTALL,
    )
    assert python_block
    script = textwrap.dedent(python_block.group("script"))
    script_path = tmp_path / "select_sync_source.py"
    script_path.write_text(script, encoding="utf-8")

    release_dir = tmp_path / "release/stable"
    release_dir.mkdir(parents=True)
    upstream_ref = "v1.2.3"
    upstream_sha = "a" * 40
    adapter_revision = "b" * 40
    reviewed_builder_commit = "c" * 40
    current_builder_commit = "d" * 40
    reviewed_inputs_sha256 = "e" * 64
    (release_dir / "source-lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channel": "stable",
                "upstream_ref": upstream_ref,
                "upstream_sha": upstream_sha,
                "adapter_revision": adapter_revision,
                "build_inputs_sha256": reviewed_inputs_sha256,
                "builder_commit": reviewed_builder_commit,
            }
        ),
        encoding="utf-8",
    )
    (release_dir / "release-manifest.json").write_text(
        json.dumps(
            {
                "source": {"ref": upstream_ref, "sha": upstream_sha},
                "adapter_sha": adapter_revision,
                "converter": {"sha": reviewed_builder_commit},
            }
        ),
        encoding="utf-8",
    )
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps({"sources": [{"source_ref": upstream_ref, "source_sha": upstream_sha}]}),
        encoding="utf-8",
    )

    def select_changed(build_inputs_sha256: str) -> str:
        output_path = tmp_path / "output.txt"
        summary_path = tmp_path / "summary.txt"
        output_path.unlink(missing_ok=True)
        summary_path.unlink(missing_ok=True)
        subprocess.run(
            [
                sys.executable,
                str(script_path),
                str(discovery_path),
                "stable",
                "main",
                adapter_revision,
                build_inputs_sha256,
                current_builder_commit,
                str(output_path),
                str(summary_path),
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        values = dict(line.split("=", 1) for line in output_path.read_text(encoding="utf-8").splitlines())
        return values["changed"]

    assert select_changed(reviewed_inputs_sha256) == "false"
    assert select_changed("f" * 64) == "true"


def test_sync_prs_are_manual_and_publish_rebuilds_under_strict_external_gate():
    validate = _load_workflow("validate.yml")
    assert "auto-merge-eligible-sync" not in validate["jobs"]
    review = validate["jobs"]["human-review-only-sync"]
    assert "manual" in review["name"]
    assert "Automatic merge is disabled" in review["steps"][0]["run"]
    assert not any("gh pr merge" in step.get("run", "") for job in validate["jobs"].values() for step in job["steps"])
    external = validate["jobs"]["external-distribution-validation"]
    external_run = "\n".join(step.get("run", "") for step in external["steps"])
    assert 'bash scripts/build.sh --output "$RUNNER_TEMP/dist"' in external_run
    assert 'hve-codex validate --output "$RUNNER_TEMP/dist" --record' in external_run
    assert "hve-codex validate --output ." not in external_run

    publish = _load_workflow("publish.yml")
    for job_name, gate_name in (
        ("release-gate", "Check branch, channel opt-in, and release eligibility"),
        ("publish-release", "Recheck protected branch and eligibility after environment approval"),
    ):
        job = publish["jobs"][job_name]
        script = "\n".join(step.get("run", "") for step in job["steps"])
        gate = next(step for step in job["steps"] if step.get("name") == gate_name)
        assert "rebuild_release_distribution.sh" in script
        assert "hve-codex release eligibility" in gate["run"]
        assert '"$RUNNER_TEMP/dist"' in gate["run"]
    recheck = _load_workflow("publish.yml")["jobs"]["publish-release"]
    recheck_script = "\n".join(step.get("run", "") for step in recheck["steps"])
    assert 'GITHUB_OUTPUT="$selection" PYTHONPATH="$trusted/src" uv run --project "$PWD" --locked python "$trusted/preflight.py"' in recheck_script
    assert 'bash "$builder_dir/scripts/build.sh" --archive-only --output "$RUNNER_TEMP/dist"' in recheck_script
    assert '"$RUNNER_TEMP/dist.tar.gz"' in recheck_script
    release_assets = next(step for step in recheck["steps"] if step.get("name") == "Create or verify the immutable unsigned tag and GitHub release")
    assert "git tag -a -m" in release_assets["run"]
    assert 'git rev-parse "$RELEASE_TAG^{commit}"' in release_assets["run"]
    assert "Never rewrite it" in release_assets["run"]
    for asset in ("dist.tar.gz", "dist.tar.gz.sha256", "release-manifest.json", "checksums.txt", "source-lock.json"):
        assert asset in release_assets["run"]


def test_workflow_yaml_and_embedded_python_are_valid():
    python_heredoc = re.compile(
        r"^[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^ \t]+)[ \t]+)*(?:uv run\b[^\n]*\b)?python\b[^\n]*<<'PY'[ \t]*\r?\n(?P<script>.*?)^[ \t]*PY[ \t]*$",
        re.MULTILINE | re.DOTALL,
    )
    yaml = YAML(typ="safe")

    for name in ("sync-upstream.yml", "weekly-sync.yml", "publish.yml", "validate.yml"):
        path = WORKFLOWS / name
        workflow_text = path.read_text(encoding="utf-8")
        yaml.load(workflow_text)
        for index, match in enumerate(python_heredoc.finditer(workflow_text), start=1):
            script = textwrap.dedent(match.group("script"))
            compile(script, f"{path.name}:python-heredoc-{index}", "exec")

    helper = ROOT / ".github/scripts/rebuild_release_distribution.sh"
    helper_text = helper.read_text(encoding="utf-8")
    helper_blocks = list(python_heredoc.finditer(helper_text))
    assert any("from hve_codex_porter.release import validate_source_ref" in block.group("script") for block in helper_blocks)
    for index, match in enumerate(helper_blocks, start=1):
        script = textwrap.dedent(match.group("script"))
        compile(script, f"{helper.name}:python-heredoc-{index}", "exec")
