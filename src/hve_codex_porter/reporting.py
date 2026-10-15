"""Separate inventory completeness, syntax validation and behavioral evidence."""
from __future__ import annotations
from pathlib import Path
from .common import dump_json, text_write

def build_report(inventory: dict, provenance: dict, findings: list[dict], unresolved: list[dict]) -> dict:
    counts = {}
    for kind, plural in (("skill", "skills"), ("agent", "agents"), ("instruction", "instructions"), ("prompt", "prompts")):
        items = [a for a in inventory["artifacts"] if a["kind"] == kind]
        counts[plural] = {"discovered": len(items), "represented": sum(bool(a["outputs"]) for a in items), "validated": 0}
    blockers = [{"code": "runtime-evidence-missing", "message": "Real Codex CLI and IDE integration and workflow evidence has not been recorded."}]
    blockers += findings
    blockers += [{"code": "unresolved-reference", **r} for r in unresolved]
    blockers += [{"code": "manifest-missing", **m} for m in inventory["manifest_missing"]]
    return {**provenance, "counts": counts, "artifacts": inventory["artifacts"], "unknown_fields": [f for f in findings if f["code"] == "unknown-field"], "unresolved_references": unresolved, "release_blockers": blockers, "coverage": {"inventory": "complete" if all(c["discovered"] == c["represented"] for c in counts.values()) else "incomplete", "structural": "pending", "behavioral": "not-run"}, "limitations": ["Hooks are advisory and require explicit trust.", "External connectors require user configuration and authentication.", "Stable releases require both CLI and IDE behavior evidence."]}

def write_report(output: Path, report: dict) -> None:
    dump_json(output / "generated/compatibility-report.json", report)
    lines = ["# HVE Core → Codex compatibility\n\n", f"Source: `{report['upstream_ref']}` / `{report['upstream_sha']}`\n\n", f"Inventory: **{report['coverage']['inventory']}**. Structural: **{report['coverage']['structural']}**. Behavior: **{report['coverage']['behavioral']}**.\n\n", "| Kind | Discovered | Represented | Behavior validated |\n| --- | ---: | ---: | ---: |\n"]
    for kind, count in report["counts"].items():
        lines.append(f"| {kind} | {count['discovered']} | {count['represented']} | {count['validated']} |\n")
    lines.append("\n## Release blockers\n\n")
    for blocker in report["release_blockers"]:
        lines.append(f"- `{blocker['code']}`: {blocker.get('source', blocker.get('message', ''))} {blocker.get('target', blocker.get('raw', ''))}\n")
    lines.append("\n## Environment prerequisites\n\nRun `hve-codex doctor --target <repo>` to inspect the installation. Install and authenticate the connector named in each workflow's capability prerequisites; installing this plugin does not provision a service. Stop affected operations when the prerequisite is absent.\n\n## Artifact compatibility\n\n| Source | Status | Limitations |\n| --- | --- | --- |\n")
    for artifact in report["artifacts"]:
        limits = "; ".join(artifact.get("limitations", [])).replace("|", "\\|")
        lines.append(f"| `{artifact['source']}` | {artifact['status']} | {limits} |\n")
    text_write(output / "generated/compatibility-report.md", "".join(lines))
