"""Release verification installs only approved Vally code, without model runs."""
from pathlib import Path

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[2]


def _steps(filename, job):
    workflow = YAML(typ="safe").load((ROOT / ".github/workflows" / filename).read_text())
    return workflow["jobs"][job]["steps"]


def _assert_verifier_setup(steps, working_directory, gate_name):
    node = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/setup-node@"))
    install = next(i for i, step in enumerate(steps) if "npm ci" in step.get("run", ""))
    gate = next(i for i, step in enumerate(steps) if step.get("name") == gate_name)
    assert node < install < gate
    assert steps[node]["with"]["node-version"] == "22"
    assert steps[node]["with"]["package-manager-cache"] is False
    assert steps[install]["working-directory"] == working_directory
    assert steps[install]["run"] == "npm ci --ignore-scripts --no-audit --no-fund"
    assert not any("vally-run" in step.get("run", "") for step in steps)


def test_source_only_sync_has_manual_human_review_and_no_auto_merge_token():
    workflow = YAML(typ="safe").load((ROOT / ".github/workflows/validate.yml").read_text())
    assert "auto-merge-eligible-sync" not in workflow["jobs"]
    review = workflow["jobs"]["human-review-only-sync"]
    assert "Automatic merge is disabled" in review["steps"][0]["run"]
    assert review["permissions"] == {"contents": "read"}
    assert not any("actions/create-github-app-token@" in step.get("uses", "") for step in review["steps"])


def test_both_publication_gates_have_model_free_verifier_runtime():
    for job, gate in (
        ("release-gate", "Check branch, channel opt-in, and release eligibility"),
        ("publish-release", "Recheck protected branch and eligibility after environment approval"),
    ):
        _assert_verifier_setup(_steps("publish.yml", job), "vally-executor", gate)
