"""Checksums and structural distribution validation, without runtime claims."""
from __future__ import annotations
import json
import re
import tomllib
from pathlib import Path
import jsonschema
from .common import digest, frontmatter, safe_path, text_write
from .references import references
from .security import credential_findings
from .release import HASH_EXCLUDED_PATHS

GENERATED_ROOTS = ("plugins/hve-core-codex", "codex-overlay", "generated", ".agents/plugins")

def distribution_files(root: Path) -> list[Path]:
    result = []
    for relative in GENERATED_ROOTS:
        directory = safe_path(root, relative)
        if directory.exists():
            result.extend(p for p in directory.rglob("*") if p.is_file() and p.name != "checksums.txt")
    return sorted(result, key=lambda p: p.relative_to(root).as_posix())

def write_checksums(root: Path) -> dict[str, str]:
    hashes = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in distribution_files(root)
              if p.relative_to(root).as_posix() not in HASH_EXCLUDED_PATHS}
    text_write(root / "generated/checksums.txt", "".join(f"{value}  {path}\n" for path, value in hashes.items()))
    return hashes


def _check_source_licenses(root: Path, errors: list[str]) -> None:
    inventory_path = root / "generated/source-inventory.json"
    if not inventory_path.is_file():
        return
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    for record in inventory.get("files", []):
        source = record.get("path", "")
        name = Path(source).name.casefold()
        if name not in {"license", "license.md", "license.txt", "copying", "copying.md", "copying.txt"}:
            continue
        outputs = record.get("outputs", [])
        if not outputs and source.casefold() == "license":
            errors.append("Source LICENSE has no packaged output")
            continue
        for relative in outputs:
            try:
                path = safe_path(root, relative)
            except ValueError as exc:
                errors.append(f"Source license output is unsafe: {relative}: {exc}")
                continue
            if not path.is_file():
                errors.append(f"Source license output missing: {relative}")
            elif not record.get("references") and record.get("hash") and digest(path.read_bytes()) != record["hash"]:
                errors.append(f"Source license content changed: {relative}")


def _check_original_sources(root: Path, errors: list[str]) -> None:
    inventory_path = root / "generated/source-inventory.json"
    if not inventory_path.is_file():
        return
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    for record in inventory.get("files", []):
        if not record.get("outputs") or record.get("symlink_target"):
            continue
        originals = record.get("originals")
        if not isinstance(originals, list) or not originals:
            errors.append(f"Packaged source has no immutable original: {record.get('path')}")
            continue
        for relative in originals:
            try:
                path = safe_path(root, relative)
                if not relative.startswith("plugins/hve-core-codex/originals/"):
                    raise ValueError("original is outside the source archive")
                if not path.is_file() or digest(path.read_bytes()) != record.get("hash"):
                    errors.append(f"Original source bytes changed or missing: {relative}")
            except (ValueError, OSError, TypeError) as exc:
                errors.append(f"Original source archive is invalid: {relative}: {exc}")


def _check_mapped_outputs(root: Path, errors: list[str]) -> None:
    mapping_path = root / "generated/source-to-output.json"
    if not mapping_path.is_file():
        errors.append("Source-to-output mapping missing")
        return
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        if not isinstance(mapping, dict):
            raise ValueError("mapping must be an object")
        for source, record in mapping.items():
            if not isinstance(record, dict):
                raise ValueError(f"invalid record for {source}")
            for field in ("outputs", "resources", "originals"):
                targets = record.get(field, [])
                if not isinstance(targets, list) or any(not isinstance(target, str) for target in targets):
                    raise ValueError(f"invalid {field} for {source}")
                for target in targets:
                    path = safe_path(root, target)
                    if not (path.is_file() or (field != "originals" and path.is_dir())):
                        errors.append(f"Mapped {field} missing for {source}: {target}")
    except (ValueError, OSError, TypeError) as exc:
        errors.append(f"Source-to-output mapping invalid: {exc}")


def validate(root: Path, schema: Path | None = None, verify_checksums: bool = True, record: bool = False) -> dict:
    root = root.resolve()
    errors = []
    paths = distribution_files(root)
    errors.extend(credential_findings(root, paths))
    _check_source_licenses(root, errors)
    _check_original_sources(root, errors)
    _check_mapped_outputs(root, errors)
    known = {p.relative_to(root).as_posix() for p in paths}
    seen = {}
    skills = []
    agents = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        safe_path(root, relative)
        if relative.casefold() in seen:
            errors.append(f"Case-insensitive collision: {relative}")
        seen[relative.casefold()] = relative
        if relative.startswith("plugins/hve-core-codex/originals/"):
            # Upstream frontmatter, host config, and links are deliberately
            # unchanged here. Scan secrets and source hashes, not Codex schemas.
            continue
        try:
            if path.suffix == ".json" and not relative.startswith("plugins/hve-core-codex/resources/"):
                json.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".toml":
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                if relative.startswith("codex-overlay/agents/"):
                    for key in ("name", "description", "developer_instructions"):
                        if not isinstance(data.get(key), str) or not data[key]:
                            errors.append(f"{relative}: missing agent key {key}")
                    agents.append(data["name"])
            if relative.startswith("plugins/hve-core-codex/") and path.suffix.casefold() == ".md":
                source_text = path.read_text(encoding="utf-8-sig")
                if path.name == "SKILL.md":
                    meta, body = frontmatter(source_text, relative)
                else:
                    body = source_text
                if path.name == "SKILL.md":
                    name = meta.get("name", "")
                    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
                        errors.append(f"{relative}: invalid skill name")
                    if not isinstance(meta.get("description"), str) or not meta["description"]:
                        errors.append(f"{relative}: description missing")
                    skills.append(name)
                # Validate dependencies in support documents as well as entry
                # points; otherwise a promoted Markdown file can ship with a
                # broken relative link even though conversion reported success.
                for ref in references(body, relative, known):
                    if ref["status"] == "unresolved":
                        errors.append(f"{relative}: broken package link {ref['raw']}")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: {exc}")
    if len(skills) != len(set(skills)):
        errors.append("Duplicate discoverable skill names")
    if len(agents) != len(set(agents)):
        errors.append("Duplicate custom agent names")
    manifest = root / "plugins/hve-core-codex/plugin.json"
    if not manifest.is_file():
        errors.append("Portable plugin manifest missing")
    else:
        obj = json.loads(manifest.read_text(encoding="utf-8"))
        if schema and schema.is_file():
            try:
                jsonschema.validate(obj, json.loads(schema.read_text(encoding="utf-8")))
            except jsonschema.ValidationError as exc:
                errors.append("Plugin schema: " + exc.message)
        elif obj.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
            errors.append("Portable plugin schema is not declared")
    registry = root / "codex-overlay/rules/index.json"
    if registry.exists():
        from .rules import load_registry
        try:
            load_registry(registry.parent)
        except (ValueError, FileNotFoundError) as exc:
            errors.append(f"Rules registry: {exc}")
    if verify_checksums:
        checksum_file = root / "generated/checksums.txt"
        if not checksum_file.exists():
            errors.append("Checksums missing")
        else:
            expected_paths = set()
            for line in checksum_file.read_text(encoding="utf-8").splitlines():
                expected, relative = line.split("  ", 1)
                expected_paths.add(relative)
                path = safe_path(root, relative)
                if not path.is_file() or digest(path.read_bytes()) != expected:
                    errors.append(f"Checksum mismatch: {relative}")
            if known - HASH_EXCLUDED_PATHS != expected_paths:
                errors.append("Checksummed file inventory differs from distribution")
    report_file = root / "generated/compatibility-report.json"
    blockers = json.loads(report_file.read_text(encoding="utf-8"))["release_blockers"] if report_file.is_file() else [{"code": "report-missing"}]
    result = {"valid": not errors, "errors": errors, "skills": len(skills), "agents": len(agents), "release_eligible": not errors and not blockers, "release_blockers": blockers}
    if record and report_file.is_file():
        from .reporting import write_report
        report = json.loads(report_file.read_text(encoding="utf-8"))
        report["coverage"]["structural"] = "passed" if not errors else "failed"
        report["structural_validation"] = {"result": "passed" if not errors else "failed", "errors": errors, "test_ids": ["static-schema", "static-resources", "static-registry", "static-checksums"]}
        for artifact in report["artifacts"]:
            artifact["validation"]["structural"] = "passed" if not errors else "failed"
        write_report(root, report)
        write_checksums(root)
    return result
