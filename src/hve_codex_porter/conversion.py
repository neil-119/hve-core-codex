"""Deterministic whole-distribution conversion using an explicit artifact graph."""
from __future__ import annotations
import json
import posixpath
import re
import shutil
from pathlib import Path
from ruamel.yaml import YAML
from .common import PortError, digest, dump_json, frontmatter, safe_path, slug, text_write
from .inventory import scan
from .references import operative_lines, rewrite_links, _mapped_directory
from .capabilities import load_adapters, tools_in_artifact, classify, guidance, rewrite_tool_tokens
from .agent_converter import render_agent, delegation_guidance, source_tool_policy
from .skill_converter import render_skill, python_resource_guidance
from .prompt_converter import prompt_guidance
from .instruction_converter import rule_record

PLUGIN = "plugins/hve-core-codex"
STATUSES = {"converted", "adapted", "host-specific-alternative", "requires-external-service", "unresolved", "excluded-with-rationale"}

def allocate_names(artifacts: list[dict]) -> dict[str, str]:
    names = {}
    used = {"hve-catalog"}
    def bounded(value: str, source: str) -> str:
        if len(value) <= 64:
            return value
        return value[:55].rstrip("-") + "-" + digest(source.encode())[:8]

    # Original skills take priority over matching prompts and agent entry points.
    for item in sorted(artifacts, key=lambda a: ({"skill": 0, "agent": 1, "prompt": 2, "instruction": 3}[a["kind"]], a["source"])):
        base = slug(str(item["name"]))
        candidate = bounded(base, item["source"])
        if candidate in used:
            candidate = bounded(item["kind"] + "-" + base, item["source"])
        if candidate in used:
            suffix = "-" + digest(item["source"].encode())[:8]
            candidate = candidate[:64 - len(suffix)].rstrip("-") + suffix
        collision = 0
        stem = candidate
        while candidate in used:
            collision += 1
            suffix = f"-{collision}"
            candidate = stem[:64 - len(suffix)].rstrip("-") + suffix
        used.add(candidate)
        names[item["id"]] = candidate
    return names

def convert(upstream: Path, output: Path, provenance: dict, mappings: Path) -> dict:
    upstream, output = upstream.resolve(), output.resolve()
    if output == upstream or output.is_relative_to(upstream):
        raise PortError("Output must not be inside the upstream checkout")
    # Generate in staging, never leave half a new release beside old output.
    stage = output.parent / ("." + output.name + "-staging")
    if stage.exists():
        raise PortError(f"Staging directory already exists; inspect it before retrying: {stage}")
    stage.mkdir(parents=True)
    try:
        report = _convert(upstream, stage, provenance, mappings)
        managed = ["plugins/hve-core-codex", "codex-overlay", "generated", ".agents/plugins/marketplace.json"]
        owned = (output / "generated/provenance.json").is_file()
        for relative in managed:
            target = safe_path(output, relative)
            if target.exists() and not owned:
                raise PortError(f"Refusing to replace unowned generated path: {target}")
        completed = []
        try:
            for relative in managed:
                source, target, backup = stage / relative, safe_path(output, relative), stage / "old" / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.move(str(target), str(backup))
                completed.append((target, backup))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
        except Exception:
            for target, backup in reversed(completed):
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                if backup.exists():
                    shutil.move(str(backup), str(target))
            raise
        return report
    finally:
        shutil.rmtree(stage)

def _convert(upstream: Path, output: Path, provenance: dict, mappings: Path) -> dict:
    inventory = scan(upstream, provenance)
    artifacts = inventory["artifacts"]
    names = allocate_names(artifacts)
    by_source = {a["source"]: a for a in artifacts}
    known = {f["path"] for f in inventory["files"] if not f.get("symlink_target")}
    files_by_source = {f["path"]: f for f in inventory["files"]}
    adapters = load_adapters(mappings / "tool-adapters.yaml")
    instruction_config = YAML(typ="safe").load((mappings / "instruction-mappings.yaml").read_text(encoding="utf-8"))
    if instruction_config.get("ordering") != "explicit-precedence-then-source-path" or instruction_config.get("context_budget_bytes") != 8192:
        raise PortError("Unsupported rule ordering/context budget; update the resolver and hook tests before changing this mapping")
    host_limitations = YAML(typ="safe").load((mappings / "known-host-limitations.yaml").read_text(encoding="utf-8"))["limitations"]
    overrides = YAML(typ="safe").load((mappings / "workflow-overrides.yaml").read_text(encoding="utf-8")).get("overrides", {})
    mapping = {}
    selected = set()
    for file in inventory["files"]:
        p = file["path"]
        if file.get("symlink_target"):
            continue
        if file["classification"] == "runtime-resource" or p in {"LICENSE", "README.md"} or "THIRD-PARTY" in p.upper():
            selected.add(p)
    selected.update(a["source"] for a in artifacts)
    # Explicit references promote support code/docs into the package without executing them.
    queue = list(selected)
    while queue:
        p = queue.pop()
        targets = list(by_source.get(p, {}).get("dependencies", []))
        targets += [r["target"] for r in files_by_source[p].get("references", []) if r["target"]]
        for target in targets:
            matches = [target] if target in known else [x for x in known if x.startswith(target.rstrip("/") + "/")]
            for match in matches:
                if match not in selected:
                    selected.add(match)
                    queue.append(match)
    for p in selected:
        mapping[p] = PLUGIN + "/resources/" + p
    # Every original skill's complete directory is copied preserving nested resources.
    for a in artifacts:
        name = names[a["id"]]
        if a["kind"] == "skill":
            parent = str(Path(a["source"]).parent).replace("\\", "/")
            for p in known:
                if p.startswith(parent + "/"):
                    mapping[p] = PLUGIN + "/skills/" + name + p[len(parent):]
                    selected.add(p)
            a["outputs"] = [mapping[a["source"]]]
        elif a["kind"] == "agent":
            a["outputs"] = ["codex-overlay/agents/" + name + ".toml"]
            if a["user_invocable"]:
                a["outputs"].append(PLUGIN + "/skills/" + name + "/SKILL.md")
        elif a["kind"] == "instruction":
            a["outputs"] = ["codex-overlay/rules/" + a["source"] + ".md"]
        else:
            a["outputs"] = [PLUGIN + "/skills/" + name + "/SKILL.md"]
    # Legacy plugin mirror links resolve to canonical resources without being
    # followed or copied as duplicate artifacts.
    for file in inventory["files"]:
        target = file.get("resolved_symlink_target")
        if target:
            destination = mapping.get(target) or _mapped_directory(target, mapping)
            if destination:
                mapping[file["path"]] = destination
                known.add(file["path"])
    agent_names = {}
    for a in artifacts:
        if a["kind"] == "agent":
            for key in (str(a["name"]), slug(str(a["name"])), Path(a["source"]).name.removesuffix(".agent.md")):
                agent_names[key] = names[a["id"]]
    invocations = {}
    for a in artifacts:
        if a["kind"] in {"skill", "prompt"} or (a["kind"] == "agent" and a["user_invocable"]):
            for key in (slug(str(a["name"])), Path(a["source"]).name.split(".")[0]):
                invocations.setdefault(key, names[a["id"]])
    findings = list(inventory["findings"])
    unresolved = []
    capability_map = []
    alias_map = []
    rules = []

    def adapt(text: str, capabilities: list[dict] | None = None) -> str:
        lines = text.splitlines(keepends=True)
        for i in operative_lines(text):
            # Rewrite known slash invocation tokens only, not arbitrary prose paths.
            lines[i] = re.sub(r"(?<![\w.:/])/(?P<name>[a-z][a-z0-9-]*)(?![/\w-])", lambda m: "$" + invocations[m["name"]] if m["name"] in invocations else m[0], lines[i])
        adapted = "".join(lines)
        if capabilities is None:
            discovered_tools = tools_in_artifact({"required_tools": []}, adapted)
            capabilities = [classify(tool, adapters) for tool in discovered_tools]
        return rewrite_tool_tokens(adapted, capabilities)

    for p in sorted(selected):
        source = safe_path(upstream, p)
        # Keep immutable source separately from executable, adapted procedures.
        # This archive is evidence, not another discoverable skill directory.
        original = PLUGIN + "/originals/" + p
        original_path = safe_path(output, original)
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, original_path)
        files_by_source[p]["originals"] = [original]
        if p in by_source:
            by_source[p]["originals"] = [original]
        dest = safe_path(output, mapping[p])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        if source.stat().st_mode & 0o111:
            dest.chmod(dest.stat().st_mode | 0o111)
        if p not in by_source and p.endswith(".md"):
            text_write(dest, adapt(rewrite_links(source.read_text(encoding="utf-8-sig"), p, mapping[p], mapping, known)))
        record = files_by_source[p]
        record.update(outputs=[mapping[p]], status="host-specific-alternative" if p.startswith(".vscode/") else "converted", classification="packaged-runtime-resource", rationale="Included by agent-facing package or explicit dependency closure; source code is never executed during conversion")
        if p not in by_source:
            for r in record.get("references", []):
                if r["status"] == "unresolved":
                    unresolved.append({"source": p, **r})

    for a in artifacts:
        meta, body = frontmatter(safe_path(upstream, a["source"]).read_text(encoding="utf-8-sig"), a["source"])
        override = overrides.get(a["source"])
        if override:
            reviewed_hashes = override.get("reviewed_source_sha256", [override.get("source_sha256")])
            if not isinstance(reviewed_hashes, list) or not reviewed_hashes or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in reviewed_hashes):
                raise PortError("Semantic overrides require an explicit list of reviewed source SHA-256 hashes")
            if a["hash"] not in reviewed_hashes:
                findings.append({"source": a["source"], "code": "override-source-changed", "message": "Reviewed semantic override does not match these source bytes; review the new upstream behavior."})
            else:
                body = safe_path(mappings, override["body_file"]).read_text(encoding="utf-8")
                metadata_overrides = override.get("metadata_overrides", {})
                if set(metadata_overrides) - {"compatibility", "description"}:
                    raise PortError("Semantic overrides cannot replace license, governance, or routing metadata")
                meta = {**meta, **metadata_overrides}
                a["strategy"] = "reviewed-semantic-override"
                a["override"] = override
        capabilities = [classify(t, adapters) for t in tools_in_artifact(a, body)]
        capability_map.append({"artifact_id": a["id"], "capabilities": capabilities})
        unsupported = [c for c in capabilities if c["classification"] == "unsupported"]
        for c in unsupported:
            findings.append({"source": a["source"], "code": "unsupported-tool", "tool": c["tool"]})
        for r in a["references"]:
            if r["status"] == "unresolved":
                unresolved.append({"source": a["source"], **r})
        output_path = a["outputs"][0]
        converted = adapt(rewrite_links(body, a["source"], mapping[a["source"]], mapping, known), capabilities)
        source_notice = f"\n<!-- Ported from microsoft/hve-core:{a['source']} @ {provenance['upstream_sha']}. Original licenses and authorship retained. -->\n"
        extra = guidance(capabilities)
        name = names[a["id"]]
        description = str(meta.get("description", a["name"]))
        limitations = ["Behavior not yet verified across Codex CLI and desktop sessions."]
        if a["kind"] == "skill" and any(
            p.endswith(".py") and mapping[p].startswith(PLUGIN + "/skills/" + name + "/")
            for p in selected
        ):
            extra += python_resource_guidance()
            limitations.append(
                "Codex utility guidance separates installed Python resource locations "
                "from authorized project paths while preserving source safety gates."
            )
        if a["kind"] == "skill" and not a["user_invocable"]:
            limitations.append(
                "The source hides this skill from manual invocation. Codex skill policy only controls implicit invocation; it cannot hide the manual picker or calls. This port preserves automatic selection, so the source's manual-invocation restriction cannot be enforced."
            )
        if meta.get("model"):
            limitations.append(f"Source host model preference {meta['model']!r} is retained in provenance; this portable role uses the user's configured Codex model. Review model availability before delegation.")
        if meta.get("excludeAgent"):
            findings.append({"source": a["source"], "code": "unsupported-source-exclusion", "message": "Source agent exclusions require a reviewed host enforcement adapter; they cannot be silently dropped."})
        # The mirror is the common dependency target and stays inside the portable package.
        mirror = source_notice + converted + extra
        if a["kind"] == "skill":
            text_write(output / output_path, render_skill(name, description, mirror, meta))
        elif a["kind"] == "agent":
            routing, agent_findings = delegation_guidance(a, agent_names, adapt)
            findings.extend(agent_findings)
            declared_tools = meta.get("tools")
            has_tool_allowlist = isinstance(declared_tools, (list, str))
            if isinstance(declared_tools, str):
                declared_tools = [declared_tools]
            source_tools = [classify(tool, adapters) for tool in (declared_tools or [])] if has_tool_allowlist else None
            source_scope_policy = source_tool_policy(source_tools)
            mirror += routing + source_scope_policy
            # Agent instructions point to package resources through install-time root tokens.
            agent_body = rewrite_links(adapt(body, capabilities), a["source"], mapping[a["source"]], mapping, known)
            # Relative package links remain relative to this explicitly named resource base.
            base = "@@HVE_PLUGIN_ROOT@@/resources/" + posixpath.dirname(a["source"])
            instructions = source_notice + f"\nResolve package-relative references below from `{base}`. Workspace output paths retain their original meaning.\n" + agent_body + extra + routing
            # Operative tool mentions describe workflow requirements, not a
            # source allowlist. An omitted tools field inherits the host tools.
            allowed_capabilities = [item["capability"] for item in (source_tools or [])]
            read_only = has_tool_allowlist and not any(
                capability in {"filesystem-write", "filesystem-access", "shell-execution"}
                for capability in allowed_capabilities
            )
            text_write(output / output_path, render_agent(name, description, instructions, read_only, source_tools))
            if source_tools is not None:
                limitations.append("Source tool allowlist is preserved as instruction policy; native per-tool enforcement is not verified.")
            if a["user_invocable"]:
                hint = meta.get("argument-hint")
                argument_guidance = f"\n## Invocation arguments\n\n{hint}\n" if hint else ""
                entry = f"# {a['name']}\n\nExplicitly selected workflow. Read the packaged procedure at [{a['name']}](../../resources/{a['source']}) in full, then execute its interactive workflow in the current conversation. Preserve its state, arguments and approval boundaries; use the named custom roles for delegated work. The installed custom role is `{name}`.\n" + argument_guidance + routing + extra + source_notice + source_scope_policy
                text_write(output / a["outputs"][1], render_skill(name, description, entry))
            limitations.append("Copilot picker and handoff buttons are replaced by explicit skill invocation and named chat actions.")
        elif a["kind"] == "instruction":
            rule = rule_record(a, a["source"] + ".md", by_source)
            rules.append(rule)
            rules_body = source_notice + f"\nPackage reference base: `@@HVE_PLUGIN_ROOT@@/resources/{posixpath.dirname(a['source'])}`.\n" + converted + extra
            text_write(output / output_path, rules_body)
            limitations.append("Scoped selection uses the registry; hook context is advisory and requires host trust.")
        else:
            contract, prompt_findings = prompt_guidance(a, body, agent_names)
            findings.extend(prompt_findings)
            destination = output / output_path
            prompt_body = rewrite_links(adapt(body, capabilities), a["source"], output_path, mapping, known)
            text_write(destination, render_skill(name, description, source_notice + contract + prompt_body + extra))
        if a["disable_model_invocation"]:
            for path in a["outputs"]:
                if path.endswith("/SKILL.md"):
                    text_write(output / posixpath.dirname(path) / "agents/openai.yaml", "policy:\n  allow_implicit_invocation: false\n")
        if a["kind"] != "skill":
            text_write(output / mapping[a["source"]], mirror)
        a["status"] = "requires-external-service" if any(c["classification"] == "MCP" for c in capabilities) else "adapted"
        if unsupported or any(f["source"] == a["source"] for f in findings) or any(r["source"] == a["source"] for r in unresolved):
            a["status"] = "unresolved"
        a["required_capabilities"] = capabilities
        a["limitations"] = limitations
        a["test_ids"] = ["static-schema", "static-resources", "determinism"]
        a["validation"] = {"structural": "pending", "behavioral": "not-run"}
        alias_map.append({"source": a["source"], "upstream_name": a["name"], "name": name, "kind": a["kind"], "user_invocable": a["user_invocable"], "explicit_only": a["disable_model_invocation"]})
    catalog = ["# HVE Core catalog\n\nLook up every original name below. Read the selected SKILL.md in full before executing it. Delegated-only roles must be selected by their orchestrator; explicit-only workflows require user selection. Missing capabilities are blockers for that operation.\n\n"]
    for alias in alias_map:
        if alias["kind"] == "instruction":
            continue
        if not alias["user_invocable"]:
            if alias["kind"] == "agent":
                catalog.append(f"- {alias['upstream_name']}: delegated custom role `{alias['name']}`.\n")
            else:
                catalog.append(f"- {alias['upstream_name']}: supporting skill `{alias['name']}` — [procedure](../{alias['name']}/SKILL.md); select only as relevant workflow guidance, not a user entry point.\n")
        else:
            catalog.append(f"- {alias['upstream_name']}: `${alias['name']}` — [procedure](../{alias['name']}/SKILL.md){'; explicit invocation required' if alias['explicit_only'] else ''}.\n")
    text_write(output / PLUGIN / "skills/hve-catalog/SKILL.md", render_skill("hve-catalog", "Find every HVE workflow, original alias, delegated role, and required setup.", "".join(catalog)))
    dump_json(output / PLUGIN / "aliases.json", alias_map)
    dump_json(output / "codex-overlay/rules/index.json", {"version": 1, "rules": rules})
    general = next((a for a in artifacts if a["source"] == instruction_config["general_source"]), None)
    managed = "# HVE Core integration\n\nUse `$hve-catalog` to select a workflow. Preserve existing project and administrator policies. Read specialty rules before editing: `python .codex/hooks/hve_rules.py --rules-match <repo-relative-paths>` runs from the project root without installing the porter CLI globally. If the CLI is available, `hve-codex rules match <repo-relative-paths>` and `rules explain` also provide matching guidance. Manual rule lookup does not prove native hooks executed. Hooks require explicit trust and provide advisory context only. Never mark human approval complete without user evidence.\n"
    if general:
        managed += "\nGeneral upstream instructions are available at `@@HVE_RULES_ROOT@@/" + general["source"] + ".md`; read them at session start.\n"
    navigation_path = Path(__file__).resolve().parents[2] / "vally-executor/navigation.json"
    if not navigation_path.is_file():
        navigation_path = Path(__file__).parent / "data/vally-executor/navigation.json"
    managed += "\n## Finding HVE guidance\n\n" + json.loads(navigation_path.read_text(encoding="utf-8"))["guidance"] + "\n"
    text_write(output / "codex-overlay/AGENTS.hve-core.md", managed)
    package_root = Path(__file__).parent
    for filename, source in (("hve_rules.py", "hook_runtime.py"), ("rules.py", "rules.py")):
        if (package_root / source).exists():
            text_write(output / "codex-overlay/hooks" / filename, (package_root / source).read_text(encoding="utf-8"))
    hook_labels = {
        "SessionStart": "HVE: Load guidance at session start",
        "SubagentStart": "HVE: Load guidance for a subagent",
        "UserPromptSubmit": "HVE: Load guidance for your prompt",
        "PreToolUse": "HVE: Load scoped rules before tool use",
    }
    dump_json(output / "codex-overlay/hooks/hooks.json", {"hooks": {event: [{"hooks": [{"type": "command", "command": "python HVE_HOOK_SCRIPT", "statusMessage": label}]}] for event, label in hook_labels.items()}})
    version = next((m["metadata"].get("version") for m in inventory["manifests"] if m["metadata"].get("version")), None) or "0.0.0"
    dump_json(output / PLUGIN / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "hve-core-codex", "version": version + "-codex.1", "description": "Independent Codex adaptation of Microsoft HVE Core workflows; no Microsoft endorsement.", "license": "SEE LICENSE AND THIRD-PARTY-NOTICES.md", "extensions": {"com.openai": {}}})
    dump_json(output / ".agents/plugins/marketplace.json", {"name": "hve-core-codex-local", "interface": {"displayName": "HVE Core for Codex"}, "plugins": [{"name": "hve-core-codex", "source": {"source": "local", "path": "./plugins/hve-core-codex"}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]})
    if (upstream / "LICENSE").is_file():
        text_write(output / PLUGIN / "LICENSE", (upstream / "LICENSE").read_text(encoding="utf-8"))
    notices = "# Third-party notices\n\nHVE Core source: https://github.com/microsoft/hve-core at " + provenance["upstream_sha"] + ". Upstream authorship and individual resource licenses are retained. This port is independent and is not endorsed by Microsoft. The distribution includes resources with licenses other than MIT; their source notices govern those resources.\n"
    for f in inventory["files"]:
        if f["path"] in selected and ("license" in f["path"].lower() or "third-party" in f["path"].lower()):
            notices += "\n- [" + f["path"] + "](" + posixpath.relpath(mapping[f["path"]], PLUGIN) + ")\n"
    text_write(output / PLUGIN / "THIRD-PARTY-NOTICES.md", notices)
    map_hash = digest(b"".join(p.relative_to(mappings).as_posix().encode() + p.read_bytes() for p in sorted(mappings.rglob("*")) if p.is_file()))
    converter_hash = digest(b"".join(p.name.encode() + p.read_bytes() for p in sorted(package_root.glob("*.py"))))
    provenance = {**provenance, "schema_version": 1, "adapter_revision": provenance.get("adapter_revision", converter_hash), "converter_version": "0.1.0", "converter_hash": converter_hash, "mapping_hash": map_hash, "upstream_manifest_version": version}
    from .reporting import build_report, write_report
    report = build_report(inventory, provenance, findings, unresolved)
    report["host_limitations"] = host_limitations
    report["rule_policy"] = instruction_config
    dump_json(output / "generated/source-inventory.json", inventory)
    dump_json(output / "generated/provenance.json", provenance)
    dump_json(output / "generated/capability-map.json", capability_map)
    dump_json(output / "generated/source-to-output.json", {p: {"resources": [target], "outputs": by_source[p]["outputs"] if p in by_source else [target], "originals": files_by_source.get(p, {}).get("originals", [])} for p, target in sorted(mapping.items())})
    write_report(output, report)
    from .packaging import write_checksums
    write_checksums(output)
    return report
