"""Artifact-level acceptance planning without inferring behavioral success."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .common import PortError, digest, dump_json, frontmatter, safe_path
from .host_acceptance import (
    _fixture_baseline,
    _init_isolated_repo,
    _install_scenario,
    _pin_staged_model_settings,
    _resolve_entrypoints,
    _scenario_checks,
    _scenario_data,
    _stage_fixtures,
)


def _read(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise PortError(f"Missing or oversized coverage input: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def acceptance_coverage(
    distribution: Path, scenario_files: list[Path], output: Path | None = None,
) -> dict[str, Any]:
    """Inventory structural evidence and declared coverage, never model claims."""
    root = distribution.resolve()
    inventory = _read(root / "generated/source-inventory.json")
    provenance = _read(root / "generated/provenance.json")
    aliases = _read(root / "plugins/hve-core-codex/aliases.json")
    aliases_by_source = {item["source"]: item for item in aliases}
    declarations: dict[str, set[str]] = {}
    definitions: list[dict[str, Any]] = []
    ids: set[str] = set()
    for path in scenario_files:
        data = _scenario_data(path)
        definitions.append({"file": path.name, "sha256": digest(path.read_bytes())})
        for case in data["scenarios"]:
            if case["id"] in ids:
                raise PortError(f"Duplicate scenario ID across suites: {case['id']}")
            ids.add(case["id"])
            sources = set(case.get("source_artifacts", []))
            sources.update(item["source"] for item in case.get("entrypoints", []))
            for source in sources:
                declarations.setdefault(source, set()).add(case["id"])

    records = []
    for artifact in sorted(inventory["artifacts"], key=lambda item: item["source"]):
        defects: list[str] = []
        source = artifact["source"]
        outputs = artifact.get("outputs", [])
        for relative in outputs:
            if not safe_path(root, relative).is_file():
                defects.append(f"output missing: {relative}")
        originals = artifact.get("originals", [])
        if not originals:
            defects.append("immutable original source missing")
        for relative in originals:
            path = safe_path(root, relative)
            if not path.is_file() or digest(path.read_bytes()) != artifact["hash"]:
                defects.append(f"immutable original mismatch: {relative}")
        alias = aliases_by_source.get(source)
        if alias is None:
            defects.append("alias record missing")
        else:
            needs_skill = artifact["kind"] in {"skill", "prompt"} or (
                artifact["kind"] == "agent" and artifact["user_invocable"]
            )
            skill_root = root / "plugins/hve-core-codex/skills" / alias["name"]
            if needs_skill:
                if not (skill_root / "SKILL.md").is_file():
                    defects.append("discoverable skill missing")
                else:
                    meta, _ = frontmatter((skill_root / "SKILL.md").read_text(encoding="utf-8"))
                    if meta.get("name") != alias["name"] or not meta.get("description"):
                        defects.append("skill discovery metadata mismatch")
                if artifact.get("disable_model_invocation"):
                    policy_path = skill_root / "agents/openai.yaml"
                    policy = YAML(typ="safe").load(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
                    if (policy or {}).get("policy", {}).get("allow_implicit_invocation") is not False:
                        defects.append("explicit-only native skill policy missing")
            elif artifact["kind"] == "agent" and (skill_root / "SKILL.md").exists():
                defects.append("delegated-only agent exposed as a user skill")
        records.append({
            "source": source,
            "kind": artifact["kind"],
            "name": alias.get("name") if alias else None,
            "source_file_sha256": artifact["hash"],
            "structural_status": "failed" if defects else "passed",
            "structural_defects": defects,
            "declared_scenario_ids": sorted(declarations.get(source, set())),
            "behavioral_status": "not-observed",
            "external_prerequisites": [
                item.get("tool") for item in artifact.get("required_capabilities", [])
                if item.get("classification") == "MCP"
            ],
        })
    known_sources = {item["source"] for item in records}
    unknown = sorted(set(declarations) - known_sources)
    report = {
        "schema_version": 1,
        "upstream_sha": provenance["upstream_sha"],
        "converter_hash": provenance["converter_hash"],
        "scenario_definitions": definitions,
        "model_sessions_started": 0,
        "summary": {
            "artifacts_total": len(records),
            "structural_passed": sum(item["structural_status"] == "passed" for item in records),
            "artifacts_with_declared_scenarios": sum(bool(item["declared_scenario_ids"]) for item in records),
            "artifacts_without_declared_scenarios": sum(not item["declared_scenario_ids"] for item in records),
            "behaviorally_verified": 0,
        },
        "unknown_scenario_sources": unknown,
        "artifacts": records,
        "host_requirements": [
            {"surface": surface, "skills": "pending-native-discovery", "agents": "pending-native-discovery",
             "hooks": "pending-trusted-event-delivery", "implicit_selection": "pending-behavioral-eval"}
            for surface in ("cli", "desktop")
        ],
        "acceptance_ready": False,
        "note": "Declared coverage is a test plan, not proof that any artifact executed successfully.",
    }
    if output:
        dump_json(output, report)
    return report


def stage_acceptance(distribution: Path, scenario_file: Path, scenario_id: str, output: Path) -> dict[str, Any]:
    """Persist an isolated fixture for human hook trust and client validation."""
    root = output.expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise PortError("Acceptance staging requires a new or empty output directory")
    scenarios = _scenario_data(scenario_file)
    matching = [item for item in scenarios["scenarios"] if item["id"] == scenario_id]
    if len(matching) != 1:
        raise PortError(f"Unknown acceptance scenario: {scenario_id}")
    scenario = matching[0]
    root.mkdir(parents=True, exist_ok=True)
    project = root / "project"
    project.mkdir()
    installed = _install_scenario(distribution.resolve(), project)
    fixtures, errors = _stage_fixtures(scenario, project)
    model_settings = _pin_staged_model_settings(project, "gpt-6-luna", "max")
    resolution = _resolve_entrypoints(scenario, project)
    baseline = _fixture_baseline(scenario, project)
    git = _init_isolated_repo(project)
    checks = _scenario_checks(scenario, project, distribution.resolve(), baseline, execute_postchecks=False)
    report = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "project": str(project),
        "scenario_definition_sha256": digest(scenario_file.read_bytes()),
        "provenance": _read(distribution / "generated/provenance.json"),
        "fixture_errors": errors,
        "fixtures_staged": fixtures,
        "entrypoint_resolution": resolution,
        "prompts": resolution["prompts"],
        "rubric": scenario["rubric"],
        "baseline_hashes": baseline,
        "deterministic_checks": checks,
        "full_installer": installed,
        "git_initialization": git,
        "model_sessions_started": 0,
        "behavioral_status": "not-run",
        "hook_trust": "not-granted-review-in-client",
        "staged_model_settings": model_settings,
        "next_steps": [
            "Open only this disposable project in the client being tested.",
            "Select GPT-6 Luna and Max reasoning; verify delegated role settings before running.",
            "Review project trust and the installed hooks using the client's supported trust controls.",
            "After live-evaluation authorization, submit each prompt separately and retain native events and changed files.",
            "Record the client version and human review; this staging report is not runtime acceptance evidence.",
        ],
    }
    dump_json(root / "acceptance-stage.json", report)
    return report
