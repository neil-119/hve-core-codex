"""Meaningful conversion regression fixtures, not an upstream-count snapshot."""
import json
import hashlib
from pathlib import Path
import pytest
import tomlkit
from hve_codex_porter.common import PortError, frontmatter, dump_json, safe_path
from hve_codex_porter.inventory import scan
from hve_codex_porter.conversion import convert
from hve_codex_porter.capabilities import classify, load_adapters, rewrite_tool_tokens
from hve_codex_porter.packaging import validate
from hve_codex_porter.references import references, rewrite_links

ROOT = Path(__file__).resolve().parents[2]


def test_allocated_names_remain_valid_when_long_source_names_collide():
    from hve_codex_porter.conversion import allocate_names

    artifacts = [
        {"id": str(index), "kind": kind, "name": "a" * 80, "source": f"{index}.md"}
        for index, kind in enumerate(("skill", "skill", "agent", "prompt"))
    ]
    names = allocate_names(artifacts)
    assert len(set(names.values())) == len(artifacts)
    assert all(len(name) <= 64 and not name.endswith("-") for name in names.values())
    assert allocate_names(list(reversed(artifacts))) == names

def test_empty_apply_to_preserves_on_demand_activation():
    from hve_codex_porter.instruction_converter import rule_record
    artifact = {"id": "manual", "source": "manual.instructions.md", "frontmatter": {"applyTo": ""}, "references": []}
    manual = rule_record(artifact, "manual.md", {})
    assert manual["apply_to"] == []
    assert manual["auto_attach"] is False
    artifact["frontmatter"] = {"applyTo": "**/*.py"}
    assert rule_record(artifact, "manual.md", {})["auto_attach"] is True
    artifact["frontmatter"] = {}
    assert rule_record(artifact, "manual.md", {})["auto_attach"] is False
    artifact["source"] = ".github/copilot-instructions.md"
    assert rule_record(artifact, "manual.md", {})["auto_attach"] is True

def write(root, path, text):
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")

@pytest.fixture
def upstream(tmp_path):
    root = tmp_path / "upstream"
    write(root, "plugin.json", json.dumps({"version": "1.0.0", "skills": [".github/skills/rpi/research"], "agents": [".github/agents/guide.agent.md"], "commands": []}))
    write(root, ".github/skills/rpi/research/SKILL.md", "---\nname: research\ndescription: Research a task\n---\n# Research\n[Resource](references/data.md)\nDo not implement until the user approves.\n")
    write(root, ".github/skills/rpi/research/references/data.md", "# Canonical state\nKeep approvals unchanged.\n")
    write(root, ".github/agents/guide.agent.md", "---\nname: Guide\ndescription: Coordinate research\nagents: [Reviewer]\ndisable-model-invocation: true\nhandoffs:\n  - label: Research\n    agent: Guide\n    prompt: /research\n---\nUse /research. [Resource](../skills/rpi/research/references/data.md)\n")
    write(root, ".github/agents/subagents/reviewer.agent.md", "---\nname: Reviewer\ndescription: Read-only reviewer\nuser-invocable: false\ntools: [read/readFile]\n---\nNever modify files.\n")
    write(root, ".github/instructions/python.instructions.md", "---\napplyTo: '**/*.py'\n---\nRequire type annotations.\n")
    write(root, ".github/prompts/work.prompt.md", "---\ndescription: Work from input\nagent: Guide\n---\nRead ${input:task}. Do not mark review complete.\n")
    write(root, "LICENSE", "MIT License fixture\n")
    return root

def test_python_resource_guidance_preserves_scripts_and_does_not_apply_to_plain_skills(upstream, tmp_path):
    source = ".github/skills/rpi/research/scripts/check.py"
    script = b"# A real resource whose bytes must not be rewritten.\nprint('fixture')\n"
    path = upstream / source
    path.parent.mkdir(parents=True)
    path.write_bytes(script)
    write(upstream, ".github/skills/plain/SKILL.md", "---\nname: plain\ndescription: Plain procedure\n---\n# Plain\n")
    output = tmp_path / "converted"
    convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"}, ROOT / "mappings")
    native = output / "plugins/hve-core-codex/skills/research"
    body = (native / "SKILL.md").read_text(encoding="utf-8")
    assert "Do not implement until the user approves." in body
    assert "## Codex packaged Python utilities" in body
    assert "canonical absolute file paths without `..` segments" in body
    assert "## Codex packaged Python utilities" not in (
        output / "plugins/hve-core-codex/skills/plain/SKILL.md"
    ).read_text(encoding="utf-8")
    assert (native / "scripts/check.py").read_bytes() == script
    source_map = json.loads((output / "generated/source-to-output.json").read_text(encoding="utf-8"))
    assert all((output / original).read_bytes() == script for original in source_map[source]["originals"])


def test_complete_inventory_and_deterministic_conversion(upstream, tmp_path):
    provenance = {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"}
    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        report = convert(upstream, output, provenance, ROOT / "mappings")
        assert report["counts"]["agents"]["represented"] == 2
        assert report["counts"]["prompts"]["represented"] == 1
        assert report["counts"]["agents"]["validated"] == 0
        assert validate(output, ROOT / "schemas/plugin.schema.json")["valid"]
    assert (outputs[0] / "generated/checksums.txt").read_bytes() == (outputs[1] / "generated/checksums.txt").read_bytes()
    # Compare the actual trees, including reports and files omitted from checksums.
    # A matching checksum manifest alone cannot establish deterministic output.
    trees = [
        {path.relative_to(output).as_posix(): path.read_bytes()
         for path in output.rglob("*") if path.is_file()}
        for output in outputs
    ]
    assert trees[0] == trees[1]
    source_outputs = json.loads((outputs[0] / "generated/source-to-output.json").read_text(encoding="utf-8"))
    for source, record in source_outputs.items():
        for original in record.get("originals", []):
            assert (outputs[0] / original).read_bytes() == (upstream / source).read_bytes(), source
    text = (outputs[0] / "plugins/hve-core-codex/resources/.github/agents/guide.agent.md").read_text()
    assert "$research" in text
    assert not (outputs[0] / "plugins/hve-core-codex/skills/reviewer").exists()
    assert (outputs[0] / "plugins/hve-core-codex/skills/research/references/data.md").is_file()
    guide_entry = (outputs[0] / "plugins/hve-core-codex/skills/guide/SKILL.md").read_text(encoding="utf-8")
    assert "## Invocation arguments" not in guide_entry
    guidance = (outputs[0] / "codex-overlay/AGENTS.hve-core.md").read_text()
    assert "python .codex/hooks/hve_rules.py --rules-match" in guidance
    assert "Manual rule lookup does not prove native hooks executed" in guidance
    role = (outputs[0] / "codex-overlay/agents/guide.toml").read_text()
    assert "A task name or nickname alone does not select a custom role" in role
    assert "Source tool scope" not in role  # An omitted source tools field inherits host tools.
    reviewer_role = (outputs[0] / "codex-overlay/agents/reviewer.toml").read_text()
    assert "Source tool scope" in reviewer_role
    assert "Source operation `read/readFile`" in reviewer_role
    assert "This instruction policy does not establish native per-tool enforcement." in reviewer_role
    reviewer_report = next(item for item in json.loads((outputs[0] / "generated/compatibility-report.json").read_text())[
        "artifacts"] if item["source"] == ".github/agents/subagents/reviewer.agent.md")
    assert any("native per-tool enforcement is not verified" in value for value in reviewer_report["limitations"])


def test_agent_entry_skill_preserves_rpi_argument_hint_and_omits_empty_hint(upstream, tmp_path):
    source = ".github/agents/rpi-agent.agent.md"
    hint = "Describe the work to research, plan, implement, and review"
    write(
        upstream,
        source,
        '---\nname: RPI Agent\ndescription: Coordinate the RPI workflow\n'
        f'argument-hint: "{hint}"\ndisable-model-invocation: true\n---\nRun the RPI workflow.\n',
    )
    output = tmp_path / "agent-argument-hint"
    report = convert(
        upstream,
        output,
        {"upstream_ref": "v1", "upstream_sha": "7" * 40, "channel": "edge"},
        ROOT / "mappings",
    )

    rpi = next(item for item in report["artifacts"] if item["source"] == source)
    rpi_entry = (output / rpi["outputs"][1]).read_text(encoding="utf-8")
    assert f"## Invocation arguments\n\n{hint}\n" in rpi_entry
    assert rpi_entry.count(hint) == 1

    guide = next(item for item in report["artifacts"] if item["source"] == ".github/agents/guide.agent.md")
    guide_entry = (output / guide["outputs"][1]).read_text(encoding="utf-8")
    assert "## Invocation arguments" not in guide_entry


def test_non_user_invocable_skill_keeps_automatic_discovery_and_reports_manual_limit(upstream, tmp_path):
    source = ".github/skills/rpi/research/SKILL.md"
    write(
        upstream,
        source,
        "---\nname: research\ndescription: Research a task\nuser-invocable: false\n---\n# Research\n",
    )
    output = tmp_path / "non-user-invocable-skill"
    report = convert(
        upstream,
        output,
        {"upstream_ref": "v1", "upstream_sha": "6" * 40, "channel": "edge"},
        ROOT / "mappings",
    )

    artifact = next(item for item in report["artifacts"] if item["source"] == source)
    skill_path = output / artifact["outputs"][0]
    assert skill_path.is_file()
    assert skill_path.parent.parent == output / "plugins/hve-core-codex/skills"
    assert not (skill_path.parent / "agents/openai.yaml").exists()
    assert "allow_implicit_invocation: false" not in skill_path.read_text(encoding="utf-8")
    assert any("manual-invocation restriction cannot be enforced" in limitation for limitation in artifact["limitations"])


def test_tool_reference_rewriter_only_changes_actionable_mapped_tokens():
    adapters = load_adapters(ROOT / "mappings/tool-adapters.yaml")
    capabilities = [classify(tool, adapters) for tool in ("vscode_askQuestions", "vscode/askQuestions", "runSubagent", "agent/runSubagent")]
    text = (
        "Use `vscode_askQuestions` to ask the user.\n"
        "Use `vscode/askQuestions` to ask the user.\n"
        "Dispatch via `runSubagent` when the host supports delegation.\n"
        "Use `agent/runSubagent` for the delegated task.\n"
        "Example: use `vscode_askQuestions` and `runSubagent` as literal source identifiers.\n"
        "```text\nUse `vscode_askQuestions`; dispatch via `runSubagent`.\n```\n"
        "The documentation mentions `runSubagent` calls for source-host model selection.\n"
        "Unquoted vscode_askQuestions remains a source identifier mention.\n"
    )
    rewritten = rewrite_tool_tokens(text, capabilities)
    assert "Use the host question interface to ask the user." in rewritten
    assert rewritten.count("Use the host question interface to ask the user.") == 2
    assert "Dispatch via native named-agent delegation" in rewritten
    assert "Use native named-agent delegation for the delegated task." in rewritten
    assert "Example: use `vscode_askQuestions` and `runSubagent`" in rewritten
    assert "```text\nUse `vscode_askQuestions`; dispatch via `runSubagent`.\n```" in rewritten
    assert "documentation mentions `runSubagent` calls" in rewritten
    assert "Unquoted vscode_askQuestions remains" in rewritten


def test_tool_reference_rewriter_adapts_pinned_research_and_mcsb_resource_guidance():
    adapters = load_adapters(ROOT / "mappings/tool-adapters.yaml")
    capabilities = [classify("runSubagent", adapters)]
    method_02 = (
        "The coach can dispatch subagents via `runSubagent` for parallel research analysis "
        "(cross-interview patterns, constraint catalogs, assumption validation) while continuing the conversation. "
        "See method-02-deep.md for dispatch protocol and task examples."
    )
    mcsb_lookup = (
        "Execution constraints: complete research within a single invocation and do not delegate to additional subagents. "
        "When neither `runSubagent` nor `task` is available, inform the user that one of these tools is required; "
        "do not synthesize per-service control mappings from training data."
    )
    rewritten = rewrite_tool_tokens(method_02 + "\n" + mcsb_lookup, capabilities)
    assert "via native named-agent delegation for parallel research analysis" in rewritten
    assert "When neither native named-agent delegation nor `task` is available" in rewritten
    assert "do not delegate to additional subagents" in rewritten


def test_explicit_agent_tool_scope_reaches_profile_resource_and_entry_skill(upstream, tmp_path):
    source = ".github/agents/guide.agent.md"
    write(
        upstream,
        source,
        "---\nname: Guide\ndescription: Coordinate research\nagents: [Reviewer]\n"
        "disable-model-invocation: true\ntools: [read/readFile]\nhandoffs:\n"
        "  - label: Research\n    agent: Guide\n    prompt: /research\n---\nUse /research.\n",
    )
    output = tmp_path / "scoped-agent"
    report = convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "8" * 40, "channel": "edge"}, ROOT / "mappings")
    artifact = next(item for item in report["artifacts"] if item["source"] == source)
    profile = (output / artifact["outputs"][0]).read_text(encoding="utf-8")
    resource = (output / "plugins/hve-core-codex/resources" / source).read_text(encoding="utf-8")
    entry_skill = (output / artifact["outputs"][1]).read_text(encoding="utf-8")
    policy = "Source tool scope"
    assert policy in profile
    assert policy in resource
    assert policy in entry_skill
    assert "Source operation `read/readFile`" in resource
    assert "Source operation `read/readFile`" in entry_skill
    original = output / "plugins/hve-core-codex/originals" / source
    assert "Source tool scope" not in original.read_text(encoding="utf-8")
    assert any("native per-tool enforcement is not verified" in value for value in artifact["limitations"])


def test_conversion_rewrites_tool_tokens_in_agent_skill_rule_and_prompt_bodies(upstream, tmp_path):
    operative = (
        "Use `vscode_askQuestions` to ask the user.\n"
        "Dispatch via `runSubagent` for the delegated work.\n"
        "Example: use `vscode_askQuestions` and `runSubagent` as literal source identifiers.\n"
        "```text\nUse `vscode_askQuestions`; dispatch via `runSubagent`.\n```\n"
    )
    skill_path = ".github/skills/rpi/research/SKILL.md"
    write(upstream, skill_path, "---\nname: research\ndescription: Research a task\n---\n# Research\n" + operative)
    write(upstream, ".github/agents/guide.agent.md", "---\nname: Guide\ndescription: Coordinate research\nagents: [Reviewer]\ndisable-model-invocation: true\nhandoffs:\n  - label: Research\n    agent: Guide\n    prompt: /research\n---\n" + operative)
    write(upstream, ".github/instructions/python.instructions.md", "---\napplyTo: '**/*.py'\n---\n" + operative)
    write(upstream, ".github/prompts/work.prompt.md", "---\ndescription: Work from input\nagent: Guide\n---\n" + operative)
    write(
        upstream,
        ".github/skills/rpi/research/references/data.md",
        "Dispatch via `runSubagent` for the approved research task.\n"
        "Example: keep `runSubagent` as a literal source identifier.\n",
    )

    output = tmp_path / "tool-rewrite"
    report = convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "9" * 40, "channel": "edge"}, ROOT / "mappings")
    expected = (
        "Use the host question interface to ask the user.\n"
        "Dispatch via native named-agent delegation for the delegated work.\n"
        "Example: use `vscode_askQuestions` and `runSubagent` as literal source identifiers.\n"
        "```text\nUse `vscode_askQuestions`; dispatch via `runSubagent`.\n```"
    )
    for source in (skill_path, ".github/agents/guide.agent.md", ".github/instructions/python.instructions.md", ".github/prompts/work.prompt.md"):
        artifact = next(item for item in report["artifacts"] if item["source"] == source)
        generated = output / artifact["outputs"][0]
        if source.endswith(".agent.md"):
            body = tomlkit.parse(generated.read_text(encoding="utf-8"))["developer_instructions"]
            assert expected in body
            assert "Dispatch via `runSubagent`" not in body
            original = output / "plugins/hve-core-codex/originals" / source
            assert "Dispatch via `runSubagent`" in original.read_text(encoding="utf-8")
        else:
            assert expected in generated.read_text(encoding="utf-8")
    source_outputs = json.loads((output / "generated/source-to-output.json").read_text(encoding="utf-8"))
    resource_record = source_outputs[".github/skills/rpi/research/references/data.md"]
    resource = output / resource_record["resources"][0]
    assert "Dispatch via native named-agent delegation" in resource.read_text(encoding="utf-8")
    assert "Example: keep `runSubagent` as a literal source identifier." in resource.read_text(encoding="utf-8")
    original_resource = output / resource_record["originals"][0]
    assert "Dispatch via `runSubagent`" in original_resource.read_text(encoding="utf-8")

def test_unknown_semantics_and_missing_references_block_release(upstream, tmp_path):
    write(upstream, ".github/agents/new.agent.md", "---\nname: New\ndescription: New role\nnew-behavior: true\ntools: [surprise/write]\n---\n[Missing](missing.md)\n")
    report = convert(upstream, tmp_path / "out", {"upstream_ref": "v1", "upstream_sha": "b" * 40, "channel": "edge"}, ROOT / "mappings")
    codes = {b["code"] for b in report["release_blockers"]}
    assert {"unknown-field", "unsupported-tool", "unresolved-reference"} <= codes
    assert not validate(tmp_path / "out")["release_eligible"]


def test_validator_rejects_missing_mapped_asset_after_checksums_are_rebuilt(upstream, tmp_path):
    from hve_codex_porter.packaging import write_checksums

    source = ".github/skills/rpi/research/assets/data.bin"
    asset = upstream / source
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"\x00\xffsource asset")
    output = tmp_path / "distribution"
    convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "edge"}, ROOT / "mappings")
    mapping = json.loads((output / "generated/source-to-output.json").read_text(encoding="utf-8"))
    target = mapping[source]["resources"][0]
    (output / target).unlink()
    write_checksums(output)
    result = validate(output)
    assert not result["valid"]
    assert any(target in error and "mapped" in error.lower() for error in result["errors"])


def test_validator_accepts_mapped_directory_alias(upstream, tmp_path):
    from hve_codex_porter.packaging import write_checksums

    output = tmp_path / "distribution"
    convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"}, ROOT / "mappings")
    mapping_path = output / "generated/source-to-output.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping["plugins/legacy/skills"] = {
        "outputs": ["plugins/hve-core-codex/skills"],
        "resources": ["plugins/hve-core-codex/skills"],
        "originals": [],
    }
    dump_json(mapping_path, mapping)
    write_checksums(output)
    assert validate(output)["valid"]

def test_literal_examples_are_preserved():
    text = "[Active](data.md)\n```md\n[Example](data.md)\n```\n"
    migrated = rewrite_links(text, "source.md", "skills/a/SKILL.md", {"data.md": "resources/data.md"}, {"data.md"})
    assert "[Active](../../resources/data.md)" in migrated
    assert "[Example](data.md)" in migrated

def test_inline_file_examples_are_not_references_or_rewritten():
    text = "The literal token `#file:guide.md` is shown; open #file:guide.md.\n"
    known = {"guide.md"}
    refs = references(text, ".github/agents/example.agent.md", known)
    assert [(ref["raw"], ref["kind"]) for ref in refs] == [("guide.md", "file")]

    migrated = rewrite_links(
        text,
        ".github/agents/example.agent.md",
        "plugins/hve-core-codex/skills/example/SKILL.md",
        {"guide.md": "plugins/hve-core-codex/resources/guide.md"},
        known,
    )
    assert "`#file:guide.md`" in migrated
    assert "open `../../resources/guide.md`." in migrated

def test_illustrative_context_does_not_hide_real_broken_links():
    text = "Paths and code here are illustrative.\n\n[walkthrough](walkthrough.md)\n\n[Required](missing.md)\n"
    refs = references(text, "guide.md", set())
    assert [(ref["raw"], ref["status"]) for ref in refs] == [("missing.md", "unresolved")]

def test_extensionless_markdown_links_resolve_unambiguously():
    source = "docs/agents/rai-planning/README.md"
    target = "docs/agents/rai-planning/entry-modes.md"
    text = "See [Entry Modes](entry-modes#capture).\n"
    refs = references(text, source, {source, target})
    assert refs[0]["target"] == target
    assert refs[0]["status"] == "resolved"

    migrated = rewrite_links(
        text,
        source,
        "plugins/hve-core-codex/resources/docs/agents/rai-planning/README.md",
        {target: "plugins/hve-core-codex/resources/docs/agents/rai-planning/entry-modes.md"},
        {source, target},
    )
    assert "(entry-modes.md#capture)" in migrated


def test_skill_catalog_directory_link_rewrites_to_packaged_skills_root():
    source = ".github/agents/catalog.agent.md"
    skill_a = ".github/skills/alpha/SKILL.md"
    skill_b = ".github/skills/beta/SKILL.md"
    text = "Browse [the available skills](../skills/).\n"
    mapping = {
        skill_a: "plugins/hve-core-codex/skills/alpha/SKILL.md",
        skill_b: "plugins/hve-core-codex/skills/beta/SKILL.md",
    }
    refs = references(text, source, {source, skill_a, skill_b})
    assert refs == [{"raw": "../skills/", "kind": "link", "target": ".github/skills", "status": "directory"}]
    migrated = rewrite_links(
        text,
        source,
        "plugins/hve-core-codex/resources/.github/agents/catalog.agent.md",
        mapping,
        {source, skill_a, skill_b},
    )
    assert "[the available skills](../../../skills/)" in migrated

def test_ambiguous_extensionless_markdown_links_remain_unresolved():
    source = "docs/guide.md"
    refs = references(
        "See [Guide](details).\n",
        source,
        {source, "docs/details.md", "docs/details/README.md"},
    )
    assert refs[0]["status"] == "unresolved"

def test_nested_markdown_dependencies_are_packaged_and_validated(upstream, tmp_path):
    write(upstream, ".github/agents/chain.agent.md", "---\nname: Chain\ndescription: Follow linked docs\n---\nRead [guide](vendor/guide.md).\n")
    write(upstream, "vendor/guide.md", "Follow [the next page](nested/step.md).\n")
    write(upstream, "vendor/nested/step.md", "Continue with [the leaf](leaf.md).\n")
    write(upstream, "vendor/nested/leaf.md", "Final packaged dependency.\n")

    output = tmp_path / "distribution"
    convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "d" * 40, "channel": "stable"}, ROOT / "mappings")
    guide = output / "plugins/hve-core-codex/resources/vendor/guide.md"
    step = output / "plugins/hve-core-codex/resources/vendor/nested/step.md"
    leaf = output / "plugins/hve-core-codex/resources/vendor/nested/leaf.md"
    assert guide.is_file() and step.is_file() and leaf.is_file()
    assert "[the next page](nested/step.md)" in guide.read_text(encoding="utf-8")
    assert "[the leaf](leaf.md)" in step.read_text(encoding="utf-8")
    assert validate(output)["valid"]

    step.write_text("Continue with [missing page](missing.md).\n", encoding="utf-8")
    result = validate(output, verify_checksums=False)
    assert not result["valid"]
    assert any("broken package link missing.md" in error for error in result["errors"])
    step.write_text("Continue with [missing directory](missing/).\n", encoding="utf-8")
    result = validate(output, verify_checksums=False)
    assert any("broken package link missing/" in error for error in result["errors"])

def test_packaging_rejects_modified_source_license_and_redacts_credential_match(upstream, tmp_path):
    output = tmp_path / "distribution"
    convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "f" * 40, "channel": "stable"}, ROOT / "mappings")
    license_file = output / "plugins/hve-core-codex/resources/LICENSE"
    license_file.write_text("modified license", encoding="utf-8")
    readme = output / "plugins/hve-core-codex/resources/README.md"
    fake_token = "ghp_" + "A" * 36
    readme.write_text("Example placeholder: " + fake_token, encoding="utf-8")

    result = validate(output, verify_checksums=False)
    assert any("Source license content changed" in error for error in result["errors"])
    findings = [error for error in result["errors"] if "Potential credential" in error]
    assert findings and fake_token not in " ".join(findings)

def test_missing_manifest_entry_is_reported(upstream):
    write(upstream, "plugin.json", '{"skills":["missing"]}')
    assert scan(upstream, {"upstream_sha": "c" * 40})["manifest_missing"]

def test_malformed_known_frontmatter_types_fail_with_actionable_error(upstream):
    write(upstream, ".github/agents/bad.agent.md", "---\nname: Bad\ndescription: Bad fields\ntools: {read_file: true}\n---\nBody\n")
    with pytest.raises(PortError, match=r"bad\.agent\.md: tools must be a string or a list of strings"):
        scan(upstream, {"upstream_sha": "c" * 40})

def test_duplicate_yaml_and_output_boundary_are_rejected(upstream):
    with pytest.raises(PortError):
        frontmatter("---\nname: a\nname: b\n---\n")
    with pytest.raises(PortError):
        convert(upstream, upstream / "out", {"upstream_sha": "c" * 40}, ROOT / "mappings")

def test_metadata_dates_remain_serializable_and_windows_paths_rejected(tmp_path):
    meta, _ = frontmatter("---\nmetadata:\n  last_updated: 2026-10-10\n---\nBody")
    path = tmp_path / "meta.json"
    dump_json(path, meta)
    assert json.loads(path.read_text())["metadata"]["last_updated"] == "2026-10-10"
    for name in ("CON.txt", "x/NUL", "trailing./file", "../escape", "C:/outside"):
        with pytest.raises(PortError):
            safe_path(tmp_path, name)

@pytest.mark.parametrize("directory_link", [False, True])
def test_inventory_rejects_untracked_source_symlinks(tmp_path, directory_link):
    source = tmp_path / "upstream"
    source.mkdir()
    outside = tmp_path / "outside"
    if directory_link:
        outside.mkdir()
        (outside / "hidden.md").write_text("not part of the checkout", encoding="utf-8")
    else:
        outside.write_text("not part of the checkout", encoding="utf-8")
    link = source / "linked"
    try:
        link.symlink_to(outside, target_is_directory=directory_link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(PortError, match="Untracked source symlink/reparse point is unsupported"):
        scan(source, {"upstream_sha": "e" * 40})


def _mock_git_link(monkeypatch, root, path, target, tracked=()):
    """Expose a mode-120000 blob while representing it as a Windows placeholder."""
    import hve_codex_porter.inventory as inventory

    (root / ".git").mkdir(exist_ok=True)
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(target)
    paths = [path, *tracked]
    entries = []
    for index, item in enumerate(paths):
        mode = b"120000" if item == path else b"100644"
        data = target if item == path else (root / item).read_bytes()
        header = b"blob " + str(len(data)).encode("ascii") + b"\0"
        object_id = hashlib.sha1(header + data).hexdigest().encode("ascii")
        entries.append(mode + b" blob " + object_id + b"\t" + item.encode() + b"\0")

    def git_bytes(_root, *args, input_data=None):
        if args[:3] == ("ls-tree", "-r", "-z"):
            return b"".join(entries)
        if args == ("cat-file", "--batch"):
            response = bytearray()
            for object_id in (input_data or b"").splitlines():
                response.extend(object_id + b" blob " + str(len(target)).encode("ascii") + b"\n")
                response.extend(target + b"\n")
            return bytes(response)
        raise AssertionError(args)

    monkeypatch.setattr(inventory, "_git_bytes", git_bytes)
    monkeypatch.setattr(inventory, "git", lambda _root, *_args: "")


def test_inventory_records_git_mirror_symlink_without_following_it(tmp_path, monkeypatch):
    source = tmp_path / "upstream"
    source.mkdir()
    write(source, "canonical.md", "Canonical source.\n")
    _mock_git_link(monkeypatch, source, "plugins/mirror.md", b"../canonical.md", ["canonical.md"])

    inventory = scan(source, {"upstream_sha": "f" * 40})
    mirror = next(record for record in inventory["files"] if record["path"] == "plugins/mirror.md")
    assert mirror["symlink_target"] == "../canonical.md"
    assert mirror["resolved_symlink_target"] == "canonical.md"
    assert mirror["classification"] == "excluded-with-rationale"
    assert mirror["outputs"] == []
    assert next(record for record in inventory["files"] if record["path"] == "canonical.md")["classification"] == "excluded-with-rationale"


@pytest.mark.parametrize(
    ("path", "target", "tracked", "message"),
    [
        ("plugins/escape.md", b"../../../outside.md", [], "escapes upstream"),
        ("plugins/missing.md", b"missing.md", [], "Cyclic|Dangling symlink"),
    ],
)
def test_inventory_rejects_escaping_or_dangling_git_symlinks(tmp_path, monkeypatch, path, target, tracked, message):
    source = tmp_path / "upstream"
    source.mkdir()
    _mock_git_link(monkeypatch, source, path, target, tracked)
    with pytest.raises(PortError, match=message):
        scan(source, {"upstream_sha": "f" * 40})


def test_inventory_rejects_worktree_bytes_that_differ_from_git_blob(tmp_path, monkeypatch):
    import hve_codex_porter.inventory as inventory_module

    source = tmp_path / "upstream"
    (source / ".git").mkdir(parents=True)
    write(source, "README.md", "checkout with CRLF\r\n")
    canonical = b"checkout with LF\n"
    header = b"blob " + str(len(canonical)).encode("ascii") + b"\0"
    expected = hashlib.sha1(header + canonical).hexdigest()
    monkeypatch.setattr(inventory_module, "_git_link_metadata", lambda _root: ({}, {"README.md": expected}))

    with pytest.raises(PortError, match="canonical Git blob.*core.autocrlf=false"):
        scan(source, {"upstream_sha": "f" * 40})


def test_inventory_rejects_staged_changes_against_head(tmp_path, monkeypatch):
    import hve_codex_porter.inventory as inventory_module

    source = tmp_path / "upstream"
    (source / ".git").mkdir(parents=True)
    monkeypatch.setattr(inventory_module, "git", lambda _root, *_args: "README.md")
    with pytest.raises(PortError, match="Staged source changes differ from HEAD: README.md"):
        scan(source, {"upstream_sha": "f" * 40})


@pytest.mark.parametrize("missing", [False, True])
def test_inventory_requires_exact_tracked_file_set(tmp_path, monkeypatch, missing):
    import hve_codex_porter.inventory as inventory_module

    source = tmp_path / "upstream"
    (source / ".git").mkdir(parents=True)
    data = b"canonical\n"
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    blob_id = hashlib.sha1(header + data).hexdigest()
    if not missing:
        (source / "tracked.md").write_bytes(data)
        write(source, "extra.md", "untracked\n")
    monkeypatch.setattr(inventory_module, "git", lambda _root, *_args: "")
    monkeypatch.setattr(inventory_module, "_git_link_metadata", lambda _root: ({}, {"tracked.md": blob_id}))

    expected = "Tracked source files are missing" if missing else "Untracked source files are not permitted"
    with pytest.raises(PortError, match=expected):
        scan(source, {"upstream_sha": "f" * 40})


def test_read_file_role_has_read_only_sandbox(upstream, tmp_path):
    path = upstream / ".github/agents/subagents/reviewer.agent.md"
    path.write_text(path.read_text().replace("read/readFile", "read_file, file_search"))
    output = tmp_path / "distribution"
    convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"}, ROOT / "mappings")
    assert 'sandbox_mode = "read-only"' in (output / "codex-overlay/agents/reviewer.toml").read_text()


@pytest.mark.parametrize("tools_header, expected_read_only", [
    ("", False),
    ("tools: [read_file, file_search]\n", True),
    ("tools: []\n", True),
])
def test_agent_sandbox_uses_declared_allowlist_not_body_mentions(
    upstream, tmp_path, tools_header, expected_read_only
):
    write(upstream, ".github/agents/guide.agent.md",
          "---\nname: Guide\ndescription: Research and save findings\n" + tools_header
          + "---\nUse `read_file` and `runSubagent`; save research with `create_file`.\n")
    output = tmp_path / "distribution"
    convert(upstream, output,
            {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"},
            ROOT / "mappings")
    role = (output / "codex-overlay/agents/guide.toml").read_text()
    assert ('sandbox_mode = "read-only"' in role) is expected_read_only


def test_recursive_yaml_alias_is_rejected():
    with pytest.raises(PortError, match="Recursive YAML"):
        frontmatter("---\nmetadata: &cycle [*cycle]\n---\nbody")


def test_literal_tool_example_is_not_required_capability(upstream, tmp_path):
    path = upstream / ".github/skills/rpi/research/SKILL.md"
    path.write_text(path.read_text() + "\n```markdown\nExample: `mcp_unknown_literal_tool`\n```\n")
    report = convert(upstream, tmp_path / "distribution", {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"}, ROOT / "mappings")
    assert not any(item.get("code") == "unsupported-tool" for item in report["release_blockers"])


def test_legacy_git_mirror_reference_resolves_to_canonical_package(upstream, tmp_path, monkeypatch):
    from hve_codex_porter.inventory import _git_blob_id
    alias = "plugins/legacy/research.md"
    target = "../../.github/skills/rpi/research/SKILL.md"
    write(upstream, alias, target)
    source = upstream / ".github/agents/guide.agent.md"
    source.write_text(source.read_text() + "\n[Legacy procedure](plugins/legacy/research.md)\n")
    blobs = {p.relative_to(upstream).as_posix(): _git_blob_id(p.read_bytes()) for p in upstream.rglob("*") if p.is_file()}
    monkeypatch.setattr("hve_codex_porter.inventory._git_link_metadata", lambda root: ({alias: target.encode()}, blobs))
    output = tmp_path / "output"
    report = convert(upstream, output, {"upstream_ref": "v1", "upstream_sha": "a" * 40, "channel": "stable"}, ROOT / "mappings")
    assert not report["unresolved_references"]
    assert validate(output)["valid"]
    assert not (output / "plugins/hve-core-codex/resources/plugins/legacy/research.md").exists()


def test_model_message_section_and_explicit_output_links_are_inert():
    from hve_codex_porter.references import references
    text = "### A model segment message\n\nThe paths and code here are illustrative.\n\nSample prose.\n\n[Sample](src/example.ts#L1)\n\n## Real references\n[Required](missing.md)\nThe result is written to [log](logs/result.diff).\n"
    refs = references(text, "README.md", set())
    assert not any(item["raw"].startswith("src/example") for item in refs)
    assert next(item for item in refs if item["raw"] == "missing.md")["status"] == "unresolved"
    assert next(item for item in refs if item["raw"] == "logs/result.diff")["status"] == "runtime-path"
