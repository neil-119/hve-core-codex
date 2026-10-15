"""Acceptance planning must not invent runtime proof or lose upstream bytes."""
import json
import hashlib
import shutil
from pathlib import Path

import pytest

from hve_codex_porter.common import PortError
from hve_codex_porter.conversion import convert
from hve_codex_porter.coverage import acceptance_coverage, stage_acceptance
from hve_codex_porter.packaging import validate

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def candidate(tmp_path):
    upstream = tmp_path / "upstream"
    skill = upstream / ".github/skills/research/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"---\nname: research\ndescription: Research local tasks\n---\nUse /research.\n")
    agent = upstream / ".github/agents/reviewer.agent.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: Reviewer\ndescription: Review tasks\ndisable-model-invocation: true\n---\nKeep approvals.\n", encoding="utf-8")
    dist = tmp_path / "distribution"
    convert(upstream, dist, {"upstream_ref": "fixture", "upstream_sha": "a" * 40, "channel": "stable"}, ROOT / "mappings")
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(json.dumps({"schema_version": 1, "scenarios": [{
        "id": "integration-fixture", "source_artifacts": [".github/skills/research/SKILL.md"],
        "entrypoints": [{"id": "primary", "source": ".github/skills/research/SKILL.md"}],
        "prompts": ["Invoke $actual-entrypoint to research src/app.py without edits."],
        "rubric": ["Uses the observed installed procedure."],
        "fixture_files": {"src/app.py": "pass\n"},
        "deterministic_checks": [{"id": "source", "kind": "fixture_exists", "path": "src/app.py"}],
    }]}), encoding="utf-8")
    return upstream, dist, scenarios


def test_original_archive_retains_preconversion_bytes_and_is_checked(candidate):
    upstream, dist, _ = candidate
    source = ".github/skills/research/SKILL.md"
    original = dist / "plugins/hve-core-codex/originals" / source
    adapted = dist / "plugins/hve-core-codex/skills/research/SKILL.md"
    assert original.read_bytes() == (upstream / source).read_bytes()
    assert "$research" in adapted.read_text(encoding="utf-8")
    assert validate(dist)["valid"]
    original.write_bytes(original.read_bytes() + b"changed\n")
    checked = validate(dist, verify_checksums=False)
    assert not checked["valid"]
    assert any("Original source bytes changed" in error for error in checked["errors"])


def test_declared_coverage_never_becomes_behavioral_evidence(candidate):
    _, dist, scenarios = candidate
    report = acceptance_coverage(dist, [scenarios])
    assert report["summary"] == {
        "artifacts_total": 2, "structural_passed": 2,
        "artifacts_with_declared_scenarios": 1, "artifacts_without_declared_scenarios": 1,
        "behaviorally_verified": 0,
    }
    assert not report["acceptance_ready"]
    assert all(item["behavioral_status"] == "not-observed" for item in report["artifacts"])
    assert report["model_sessions_started"] == 0
    assert {item["surface"] for item in report["host_requirements"]} == {"cli", "desktop"}


def test_missing_native_explicit_policy_is_a_structural_defect(candidate):
    _, dist, scenarios = candidate
    (dist / "plugins/hve-core-codex/skills/reviewer/agents/openai.yaml").unlink()
    report = acceptance_coverage(dist, [scenarios])
    reviewer = next(item for item in report["artifacts"] if item["name"] == "reviewer")
    assert "explicit-only native skill policy missing" in reviewer["structural_defects"]


def test_duplicate_suite_ids_and_unknown_sources_are_not_hidden(candidate):
    _, dist, scenarios = candidate
    with pytest.raises(PortError, match="Duplicate scenario ID across suites"):
        acceptance_coverage(dist, [scenarios, scenarios])
    data = json.loads(scenarios.read_text(encoding="utf-8"))
    data["scenarios"][0]["source_artifacts"].append("missing.source.md")
    scenarios.write_text(json.dumps(data), encoding="utf-8")
    assert acceptance_coverage(dist, [scenarios])["unknown_scenario_sources"] == ["missing.source.md"]


def test_persistent_staging_has_no_model_or_trust_side_effects(candidate, tmp_path, monkeypatch):
    _, dist, scenarios = candidate
    monkeypatch.setattr("hve_codex_porter.coverage._init_isolated_repo", lambda *_: {"status": "initialized"})
    staged = tmp_path / "staged"
    report = stage_acceptance(dist, scenarios, "integration-fixture", staged)
    assert report["model_sessions_started"] == 0
    assert report["behavioral_status"] == "not-run"
    assert report["hook_trust"] == "not-granted-review-in-client"
    assert report["staged_model_settings"]["requested_model"] == "gpt-6-luna"
    assert (staged / "project/src/app.py").read_text(encoding="utf-8") == "pass\n"
    assert (staged / "acceptance-stage.json").is_file()
    with pytest.raises(PortError, match="new or empty"):
        stage_acceptance(dist, scenarios, "integration-fixture", staged)


def test_reviewed_override_is_bound_to_each_explicit_source_hash(tmp_path):
    mappings = tmp_path / "mappings"
    shutil.copytree(ROOT / "mappings", mappings)
    upstream = tmp_path / "upstream"
    source = upstream / ".github/skills/installer/hve-core-installer/SKILL.md"
    source.parent.mkdir(parents=True)
    versions = [f"---\nname: hve-core-installer\ndescription: Install\n---\nRevision {number}.\n".encode() for number in (1, 2, 3)]
    reviewed = [hashlib.sha256(data).hexdigest() for data in versions[:2]]
    policy = {"version": 1, "overrides": {".github/skills/installer/hve-core-installer/SKILL.md": {
        "source_sha256": reviewed[0], "reviewed_source_sha256": reviewed,
        "body_file": "overrides/installer.md",
    }}}
    (mappings / "workflow-overrides.yaml").write_text(json.dumps(policy), encoding="utf-8")
    for index, data in enumerate(versions):
        source.write_bytes(data)
        report = convert(upstream, tmp_path / f"dist-{index}", {"upstream_sha": "a" * 40, "upstream_ref": "fixture", "channel": "edge"}, mappings)
        codes = {item["code"] for item in report["release_blockers"]}
        assert ("override-source-changed" in codes) is (index == 2)


def test_post_choice_handoff_checks_structured_synthetic_summary_metadata():
    summary_path = ".copilot-tracking/dt/profile-preferences/handoff-summary.md"
    for suite_name, scenario_id in (
        ("integration-scenarios.json", "integration-05"),
        ("edge-integration-scenarios.json", "edge-integration-05"),
    ):
        suite = json.loads((ROOT / "tests" / "fixtures" / "legacy-scenarios" / suite_name).read_text(encoding="utf-8"))
        scenario = next(item for item in suite["scenarios"] if item["id"] == scenario_id)
        check = next(item for item in scenario["deterministic_checks"] if item["id"] == "synthetic-label-in-summary")

        assert scenario["fixture_provenance"]["classification"] == "synthetic-evaluation-only"
        assert "label the resulting outputs as synthetic evaluation data" in scenario["prompts"][0]
        assert check == {
            "id": "synthetic-label-in-summary",
            "kind": "workspace_file_contains",
            "path": summary_path,
            "text": "synthetic_evaluation_data: true",
        }
