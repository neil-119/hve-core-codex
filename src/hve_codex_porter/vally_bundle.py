"""Build a deterministic Vally eval bundle using the Codex spec converter.

Preparation copies files and rewrites YAML only. It never executes an eval,
script, command, grader, executor, or model.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML

from .common import PortError, digest, dump_json, safe_path
from .vally_conversion import convert_vally_spec
from .vally_dependencies import supplement_environment_dependencies


_MANIFEST = "generated/source-to-output.json"
_REPORT = "generated/vally-bundle-report.json"
_SOURCE_MAP = "generated/vally-source-to-output.json"
_DEFAULT_EVAL_FILENAMES = ("eval.yaml", "eval.yml")
_SKIPPED_SPEC_KEYS = {"assertions", "fixtures", "graders", "runs", "scoring", "tags", "threshold", "thresholds"}
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_MAX_FILES = 20_000


def _yaml() -> YAML:
    result = YAML(typ="safe")
    result.allow_duplicate_keys = False
    return result


def _read_yaml(raw: bytes, source: str) -> Any:
    try:
        docs = list(_yaml().load_all(raw.decode("utf-8-sig")))
    except Exception as exc:
        raise PortError(f"Could not parse YAML at {source}: {exc}") from exc
    if len(docs) != 1:
        raise PortError(f"Expected one YAML document at {source}, found {len(docs)}.")
    return docs[0]


def _dump_yaml(value: Any) -> bytes:
    """Emit valid YAML while guaranteeing the parser sees the same values.

    JSON is a YAML subset and avoids block-scalar re-indentation/wrapping that
    can alter prompt text. Values outside JSON's data model use the YAML
    emitter with a wide line limit, followed by the same equality check.
    """
    try:
        raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        from io import StringIO

        output = StringIO()
        writer = YAML()
        writer.default_flow_style = False
        writer.allow_unicode = True
        writer.width = 2**31 - 1
        writer.dump(value, output)
        raw = (output.getvalue().rstrip() + "\n").encode("utf-8")
    if _read_yaml(raw, "serialized Vally bundle") != value:
        raise PortError("Serializing the Vally YAML changed evaluation data; bundle output was rejected.")
    return raw


def _source_id(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _walk_eval_files(upstream: Path, findings: list[dict[str, str]]) -> list[Path]:
    eval_root = upstream / "evals"
    if eval_root.is_symlink():
        findings.append({"code": "symlink-skipped", "source": "evals", "severity": "error", "message": "The evals root is a symlink and was not followed."})
        return []
    if not eval_root.is_dir():
        findings.append({"code": "evals-directory-missing", "source": "evals", "severity": "warning", "message": "No upstream evals directory exists."})
        return []

    files: list[Path] = []
    for current, dirs, names in os.walk(eval_root, topdown=True, followlinks=False):
        here = Path(current)
        allowed: list[str] = []
        for name in sorted(dirs, key=lambda item: (item.casefold(), item)):
            candidate = here / name
            if candidate.is_symlink():
                findings.append({"code": "symlink-skipped", "source": _source_id(candidate, upstream), "severity": "error", "message": "A symlinked eval directory was not followed."})
            else:
                allowed.append(name)
        dirs[:] = allowed
        for name in sorted(names, key=lambda item: (item.casefold(), item)):
            candidate = here / name
            if candidate.is_symlink():
                findings.append({"code": "symlink-skipped", "source": _source_id(candidate, upstream), "severity": "error", "message": "A symlinked eval file was not followed."})
            elif candidate.is_file():
                files.append(candidate)
    return sorted(files, key=lambda item: (_source_id(item, upstream).casefold(), _source_id(item, upstream)))


def _source_identity(reference: Any, spec_directory: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise PortError("Vally environment source must be a non-empty relative path.")
    if reference.startswith(("/", "\\")) or "\\" in reference or ":" in reference or "\x00" in reference:
        raise PortError(f"Unsafe Vally environment source: {reference!r}")
    parts = list(PurePosixPath(spec_directory).parts)
    for part in reference.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise PortError(f"Vally environment source escapes the upstream root: {reference!r}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise PortError(f"Vally environment source resolves to the upstream root: {reference!r}")
    return PurePosixPath(*parts).as_posix()


def _environment_references(spec: Any, spec_directory: str) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    active: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            node_id = id(node)
            if node_id in active:
                raise PortError("Recursive Vally YAML data is not supported.")
            active.add(node_id)
            try:
                for key, value in node.items():
                    if key in {"environment", "agent_environment", "grading_environment"}:
                        if not isinstance(value, Mapping):
                            continue
                        skills = value.get("skills", [])
                        if isinstance(skills, list):
                            for item in skills:
                                identity = _source_identity(item, spec_directory)
                                if PurePosixPath(identity).name.casefold() != "skill.md":
                                    identity = f"{identity.rstrip('/')}/SKILL.md"
                                references.append(("skill", identity))
                        files = value.get("files", [])
                        if isinstance(files, list):
                            for item in files:
                                if isinstance(item, Mapping) and "src" in item:
                                    references.append(("file", _source_identity(item["src"], spec_directory)))
                        continue
                    if str(key).casefold() in _SKIPPED_SPEC_KEYS:
                        continue
                    visit(value)
            finally:
                active.remove(node_id)
        elif isinstance(node, list):
            node_id = id(node)
            if node_id in active:
                raise PortError("Recursive Vally YAML data is not supported.")
            active.add(node_id)
            try:
                for value in node:
                    visit(value)
            finally:
                active.remove(node_id)

    visit(spec)
    return references


def _load_manifest(distribution: Path) -> tuple[dict[str, Any], bytes]:
    manifest_path = safe_path(distribution, _MANIFEST)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PortError(f"Distribution source-to-output manifest is missing: {manifest_path}")
    if manifest_path.stat().st_size > _MAX_FILE_BYTES:
        raise PortError("Distribution source-to-output manifest is too large.")
    raw = manifest_path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortError(f"Could not parse distribution source-to-output manifest: {exc}") from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(entry, Mapping) for key, entry in value.items()):
        raise PortError("Distribution source-to-output manifest must map source paths to objects.")
    return value, raw


def _load_source_inventory(distribution: Path) -> dict[str, Any] | None:
    """Capture the converter's pinned upstream identity when it is present."""
    relative = "generated/source-inventory.json"
    path = safe_path(distribution, relative)
    if path.is_symlink() or not path.is_file():
        return None
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise PortError("Distribution source inventory is too large.")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortError(f"Could not parse distribution source inventory: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PortError("Distribution source inventory must be a JSON object.")
    return {
        "path": relative,
        "sha256": digest(raw),
        **{
            key: value[key]
            for key in ("upstream_sha", "upstream_ref", "upstream_url", "channel")
            if isinstance(value.get(key), str)
        },
    }


def _manifest_resource(manifest: Mapping[str, Any], identity: str) -> str:
    entry = manifest.get(identity)
    resources = entry.get("resources") if isinstance(entry, Mapping) else None
    if entry is None:
        # Whole-directory fixtures are expressed as file sources by upstream
        # Vally. Infer only a directory whose mapped members preserve every
        # relative filename under one converted resource root.
        prefix = f"{identity.rstrip('/')}/"
        roots: set[str] = set()
        members = [(source, value) for source, value in manifest.items() if source.startswith(prefix)]
        for source, value in members:
            mapped = value.get("resources") if isinstance(value, Mapping) else None
            tail = source[len(prefix):]
            if not isinstance(mapped, list) or len(mapped) != 1 or not isinstance(mapped[0], str):
                raise PortError(f"Unmapped or ambiguous Vally directory member: {source!r}.")
            suffix = f"/{tail}"
            if not mapped[0].endswith(suffix):
                raise PortError(f"Vally directory member does not preserve its relative path: {source!r}.")
            roots.add(mapped[0][:-len(suffix)])
        if members and len(roots) == 1:
            resources = list(roots)
    if not isinstance(resources, list) or len(resources) != 1 or not isinstance(resources[0], str):
        raise PortError(f"Unmapped or ambiguous Vally source reference: {identity!r}.")
    resource = resources[0]
    if (
        not resource
        or resource.startswith(("/", "\\"))
        or "\\" in resource
        or ":" in resource
        or "\x00" in resource
        or ".." in PurePosixPath(resource).parts
    ):
        raise PortError(f"Invalid distribution resource for {identity!r}: {resource!r}.")
    return resource


def _write_bytes(root: Path, relative: str, raw: bytes) -> str:
    target = safe_path(root, relative)
    if target.exists():
        if target.is_file() and target.read_bytes() == raw:
            return digest(raw)
        raise PortError(f"Output path collision at {relative!r}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return digest(raw)


def _record(records: dict[str, dict[str, Any]], source: str, path: str, status: str, sha256: str | None = None) -> None:
    entry = records.setdefault(source, {"source": source, "outputs": []})
    output: dict[str, str] = {"path": path, "status": status}
    if sha256:
        output["sha256"] = sha256
    if output not in entry["outputs"]:
        entry["outputs"].append(output)


def _copy_file(
    source_root: Path,
    source_identity: str,
    output_root: Path,
    output_relative: str,
    records: dict[str, dict[str, Any]],
    status: str,
) -> None:
    source = safe_path(source_root, source_identity)
    if source.is_symlink() or not source.is_file():
        raise PortError(f"Referenced resource is missing or not a regular file: {source_identity!r}.")
    if source.stat().st_size > _MAX_FILE_BYTES:
        raise PortError(f"Referenced resource exceeds {_MAX_FILE_BYTES} bytes: {source_identity!r}.")
    raw = source.read_bytes()
    output_sha = _write_bytes(output_root, output_relative, raw)
    _record(records, source_identity, output_relative, status, output_sha)


def _copy_resource(
    source_root: Path,
    source_identity: str,
    resource: str,
    output_root: Path,
    spec_directory: str,
    records: dict[str, dict[str, Any]],
    *,
    is_skill: bool = False,
) -> None:
    source = safe_path(source_root, resource)
    if source.is_symlink():
        raise PortError(f"Symlinked converted resource is not copied: {resource!r}.")
    source_resource = source
    source_resource_relative = resource
    if is_skill and source.is_file() and source.name.casefold() == "skill.md":
        source_resource = source.parent
        source_resource_relative = PurePosixPath(resource).parent.as_posix()
    target_root = (
        PurePosixPath("evals")
        / PurePosixPath(spec_directory).relative_to("evals")
        / PurePosixPath(source_resource_relative)
    ).as_posix()
    if source_resource.is_file():
        _copy_file(source_root, resource, output_root, target_root, records, "manifest-resource-copy")
        if source_identity != resource:
            _record(records, source_identity, target_root, "manifest-resource-copy", digest(source.read_bytes()))
        return
    if not source_resource.is_dir():
        raise PortError(f"Converted resource is missing: {resource!r}.")
    files = sorted(source_resource.rglob("*"), key=lambda item: (item.relative_to(source_resource).as_posix().casefold(), item.relative_to(source_resource).as_posix()))
    if any(path.is_symlink() for path in files):
        raise PortError(f"Converted resource directory contains symlinks: {resource!r}.")
    regular_files = [path for path in files if path.is_file()]
    if len(regular_files) > _MAX_FILES or sum(path.stat().st_size for path in regular_files) > 256 * 1024 * 1024:
        raise PortError(f"Converted resource directory exceeds supported size: {resource!r}.")
    copied = 0
    source_identity_root = PurePosixPath(source_identity).parent.as_posix() if is_skill else source_identity
    for path in files:
        if not path.is_file():
            continue
        relative_tail = path.relative_to(source_resource).as_posix()
        member_identity = f"{source_identity_root.rstrip('/')}/{relative_tail}"
        member_resource = f"{source_resource_relative.rstrip('/')}/{relative_tail}"
        member_output = f"{target_root.rstrip('/')}/{relative_tail}"
        _copy_file(source_root, member_resource, output_root, member_output, records, "manifest-resource-copy")
        _record(records, member_identity, member_output, "manifest-resource-copy", digest(path.read_bytes()))
        copied += 1
    if not copied:
        raise PortError(f"Converted resource directory is empty: {resource!r}.")
    source_directory_identity = PurePosixPath(source_identity).parent.as_posix() if is_skill else source_identity
    _record(records, source_directory_identity, target_root, "manifest-resource-directory")


def _copy_native_role_profiles(
    spec: Any,
    distribution: Path,
    staging: Path,
    spec_directory: str,
    records: dict[str, dict[str, Any]],
) -> int:
    """Stage generated roles without changing their instructions or boundaries.

    The only relocation maps the package root to the same resource layout
    used by the managed installer, without overlaying consumer project files.
    """
    profiles: set[str] = set()
    resource_mirrors: set[str] = set()
    active: set[int] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            if id(node) in active:
                raise PortError("Recursive Vally YAML data is not supported.")
            active.add(id(node))
            try:
                for key, value in node.items():
                    if key in {"environment", "agent_environment"} and isinstance(value, Mapping):
                        for item in value.get("files", []):
                            if not isinstance(item, Mapping):
                                continue
                            source = item.get("src")
                            if source == "plugins/hve-core-codex/resources":
                                if item.get("dest") not in {
                                    ".agents/resources", ".codex/hve-core-codex/plugin-resources/resources",
                                }:
                                    raise PortError("Unexpected package resource mirror destination.")
                                resource_mirrors.add(source)
                            if source in {"plugins/hve-core-codex/resources/scripts", "plugins/hve-core-codex/resources/schemas"}:
                                if item.get("dest") != str(source).rsplit("/", 1)[-1]:
                                    raise PortError("Unexpected repository support destination.")
                                resource_mirrors.add(source)
                            if source == "plugins/hve-core-codex/resources/.github":
                                if item.get("dest") != ".github":
                                    raise PortError("Unexpected repository context destination.")
                                resource_mirrors.add(source)
                            if isinstance(source, str) and source.startswith("plugins/hve-core-codex/skills/") and str(item.get("dest", "")).startswith(".github/skills/") and safe_path(distribution, source).is_dir():
                                resource_mirrors.add(source)
                            if isinstance(source, str) and source.startswith("codex-overlay/agents/"):
                                path = PurePosixPath(source)
                                if len(path.parts) != 3 or path.suffix != ".toml":
                                    raise PortError(f"Invalid native role resource: {source!r}.")
                                if item.get("dest") != f".codex/agents/{path.name}":
                                    raise PortError(f"Unexpected native role destination: {item.get('dest')!r}.")
                                profiles.add(source)
                        continue
                    if key == "grading_environment" or str(key).casefold() in _SKIPPED_SPEC_KEYS:
                        continue
                    visit(value)
            finally:
                active.remove(id(node))
        elif isinstance(node, list):
            if id(node) in active:
                raise PortError("Recursive Vally YAML data is not supported.")
            active.add(id(node))
            try:
                for value in node:
                    visit(value)
            finally:
                active.remove(id(node))

    visit(spec)
    for resource in sorted(resource_mirrors):
        if not safe_path(distribution, resource).is_dir():
            raise PortError(f"Package resource mirror is not a directory: {resource!r}.")
        _copy_resource(distribution, resource, resource, staging, spec_directory, records)
    for profile in sorted(profiles):
        source = safe_path(distribution, profile)
        if source.is_symlink() or not source.is_file() or source.stat().st_size > _MAX_FILE_BYTES:
            raise PortError(f"Native role profile is missing, unsafe or oversized: {profile!r}.")
        raw = source.read_bytes()
        relocated = raw.replace(b"@@HVE_PLUGIN_ROOT@@", b".codex/hve-core-codex/plugin-resources")
        if b"@@HVE_PLUGIN_ROOT@@" in relocated:
            raise PortError(f"Native role profile has an unsupported package reference: {profile!r}.")
        try:
            parsed = tomllib.loads(relocated.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise PortError(f"Native role profile is invalid TOML: {profile!r}.") from exc
        if any(not isinstance(parsed.get(key), str) or not parsed[key].strip()
               for key in ("name", "description", "developer_instructions")):
            raise PortError(f"Native role profile lacks required metadata: {profile!r}.")
        output = (PurePosixPath(spec_directory) / profile).as_posix()
        output_sha = _write_bytes(staging, output, relocated)
        _record(records, profile, output, "native-role-profile", output_sha)
    return len(profiles)


def _local_resource(
    upstream: Path,
    identity: str,
    spec_directory: str,
    output_root: Path,
    records: dict[str, dict[str, Any]],
) -> str:
    source = safe_path(upstream, identity)
    if source.is_symlink() or not (source.is_file() or source.is_dir()):
        raise PortError(f"Referenced upstream eval resource is missing: {identity!r}.")
    source_spec = PurePosixPath(spec_directory)
    source_path = PurePosixPath(identity)
    try:
        relative = source_path.relative_to(source_spec).as_posix()
        if relative == ".":
            raise ValueError("reference is the spec directory")
        target_directory = spec_directory
    except ValueError:
        token = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        relative = f".vally-imports/{token}/{source.name}"
        target_directory = spec_directory

    if source.is_file():
        if source.stat().st_size > _MAX_FILE_BYTES:
            raise PortError(f"Referenced upstream eval resource exceeds {_MAX_FILE_BYTES} bytes: {identity!r}.")
        if not PurePosixPath(identity).is_relative_to(source_spec):
            _copy_file(upstream, identity, output_root, f"{target_directory}/{relative}", records, "local-resource-copy")
        else:
            raw = source.read_bytes()
            _record(records, identity, identity, "preserved-eval-resource", digest(raw))
        return relative

    copied = 0
    for path in sorted(source.rglob("*"), key=lambda item: (item.relative_to(source).as_posix().casefold(), item.relative_to(source).as_posix())):
        if path.is_symlink():
            raise PortError(f"Referenced upstream eval directory contains a symlink: {identity!r}.")
        if path.is_file():
            if path.stat().st_size > _MAX_FILE_BYTES:
                raise PortError(f"Referenced upstream eval resource exceeds {_MAX_FILE_BYTES} bytes: {path}.")
            tail = path.relative_to(source).as_posix()
            member_source = f"{identity.rstrip('/')}/{tail}"
            if not PurePosixPath(identity).is_relative_to(source_spec):
                _copy_file(upstream, member_source, output_root, f"{target_directory}/{relative}/{tail}", records, "local-resource-copy")
            else:
                _record(records, member_source, member_source, "preserved-eval-resource", digest(path.read_bytes()))
            copied += 1
    if not copied:
        raise PortError(f"Referenced upstream eval directory is empty: {identity!r}.")
    return relative


def _eval_patterns(
    config: Any,
    findings: list[dict[str, str]],
    overrides: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, ...]:
    if overrides is not None:
        patterns: Any = list(overrides)
    else:
        paths = config.get("paths") if isinstance(config, Mapping) else None
        patterns = paths.get("evalFilenames") if isinstance(paths, Mapping) else None
    if patterns is None:
        return _DEFAULT_EVAL_FILENAMES
    if isinstance(patterns, str):
        patterns = [patterns]
    invalid = not isinstance(patterns, list) or not patterns or any(
        not isinstance(item, str) or not item or "/" in item or "\\" in item or ".." in item for item in patterns
    )
    if invalid and overrides is not None:
        raise PortError("Eval filename pattern overrides must be safe basename patterns.")
    if invalid:
        findings.append({"code": "invalid-eval-filename-patterns", "source": ".vally.yaml", "severity": "error", "message": "paths.evalFilenames must contain filename patterns; defaults were used."})
        return _DEFAULT_EVAL_FILENAMES
    return tuple(patterns)


def _bundle_config(
    config: Any,
    findings: list[dict[str, str]],
    eval_filename_patterns: tuple[str, ...],
) -> dict[str, Any]:
    result = dict(config) if isinstance(config, Mapping) else {}
    if not isinstance(config, Mapping):
        findings.append({"code": "invalid-vally-config", "source": ".vally.yaml", "severity": "error", "message": "The Vally config root must be a mapping; a minimal bundle config was generated."})
    paths = result.get("paths")
    paths = dict(paths) if isinstance(paths, Mapping) else {}
    if result.get("paths") is not None and not isinstance(result.get("paths"), Mapping):
        findings.append({"code": "invalid-vally-path-config", "source": ".vally.yaml", "severity": "error", "message": "The Vally paths setting must be a mapping; bundle paths were generated."})
    paths["evals"] = "evals"
    paths["results"] = "results"
    paths["evalFilenames"] = list(eval_filename_patterns)
    result["paths"] = paths
    return result


def prepare_vally_bundle(
    upstream_dir: Path,
    distribution: Path,
    output: Path,
    *,
    custom_dir: Path | None = None,
    fixture_dependencies: Path | None = None,
    eval_filename_patterns: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Prepare a Vally-compatible bundle from pinned upstream evals.

    Original eval specs and config are archived under ``upstream/``. All other
    eval files retain their relative paths under ``evals/``. Converted resource
    paths are copied beneath the corresponding spec directory.
    """
    upstream = Path(upstream_dir).expanduser().resolve()
    distribution_root = Path(distribution).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    fixture_dependency_root: Path | None = None
    fixture_dependency_receipt: dict[str, Any] | None = None
    fixture_package_paths: set[str] = set()
    if fixture_dependencies is not None:
        from .locked_fixture_dependencies import verify_prepared_fixture_dependencies

        fixture_dependency_receipt, fixture_dependency_root = verify_prepared_fixture_dependencies(
            upstream, fixture_dependencies
        )
        fixture_package_paths = {
            item["lock_path"] for item in fixture_dependency_receipt["packages"]
        }
    custom = None
    if custom_dir is not None:
        custom_input = Path(custom_dir).expanduser()
        if custom_input.is_symlink():
            raise PortError("Custom Vally source root must not be a symlink.")
        custom = custom_input.resolve()
        if not custom.is_dir():
            raise PortError("Custom Vally source input must be a directory.")
        if destination == custom or destination.is_relative_to(custom):
            raise PortError("Bundle output must not be inside the custom Vally source tree.")
    if not upstream.is_dir() or not distribution_root.is_dir():
        raise PortError("Upstream eval and converted distribution inputs must be directories.")
    if destination == upstream or destination.is_relative_to(upstream):
        raise PortError("Bundle output must not be inside the upstream source tree.")
    if destination == distribution_root or destination.is_relative_to(distribution_root):
        raise PortError("Bundle output must not be inside the converted distribution tree.")
    if destination.exists() or destination.is_symlink():
        raise PortError(f"Bundle output already exists: {destination}")

    artifact_manifest, manifest_bytes = _load_manifest(distribution_root)
    source_inventory = _load_source_inventory(distribution_root)
    eval_files: list[dict[str, str]] = []
    conversion_errors: list[dict[str, str]] = []
    source_records: dict[str, dict[str, Any]] = {}
    eval_paths = _walk_eval_files(upstream, eval_files)
    custom_paths = _walk_eval_files(custom, eval_files) if custom is not None else []
    if len(eval_paths) + len(custom_paths) > _MAX_FILES:
        raise PortError(f"Upstream eval tree exceeds {_MAX_FILES} files.")

    raw_files: dict[str, bytes] = {}
    input_roots: dict[str, Path] = {}
    custom_identities: set[str] = set()
    total_bytes = 0
    inputs = [(upstream, path, False) for path in eval_paths]
    inputs.extend((custom, path, True) for path in custom_paths)
    for input_root, path, is_custom in inputs:
        identity = _source_id(path, input_root)
        if is_custom and not (identity.startswith("evals/codex-port/") or identity.startswith("evals/codex-port-")):
            raise PortError(f"Custom Vally sources must use the evals/codex-port namespace: {identity}")
        if identity in raw_files:
            raise PortError(f"Custom Vally source collides with an upstream file: {identity}")
        input_roots[identity] = input_root
        if is_custom:
            custom_identities.add(identity)
        if path.stat().st_size > _MAX_FILE_BYTES:
            conversion_errors.append({"source": identity, "error": f"File exceeds {_MAX_FILE_BYTES} bytes."})
            continue
        raw = path.read_bytes()
        total_bytes += len(raw)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise PortError(f"Upstream eval tree exceeds {_MAX_TOTAL_BYTES} bytes.")
        raw_files[identity] = raw

    config_source = upstream / ".vally.yaml"
    config_raw: bytes | None = None
    config_value: Any = None
    findings: list[dict[str, str]] = []
    if config_source.is_symlink():
        findings.append({"code": "symlink-skipped", "source": ".vally.yaml", "severity": "error", "message": "The Vally config is a symlink and was not followed."})
    elif config_source.is_file():
        config_raw = config_source.read_bytes()
        if len(config_raw) > _MAX_FILE_BYTES:
            conversion_errors.append({"source": ".vally.yaml", "error": f"Config exceeds {_MAX_FILE_BYTES} bytes."})
        else:
            try:
                config_value = _read_yaml(config_raw, ".vally.yaml")
            except PortError as exc:
                conversion_errors.append({"source": ".vally.yaml", "error": str(exc)})
                findings.append({"code": "config-parse-error", "source": ".vally.yaml", "severity": "error", "message": str(exc)[:1000]})
    else:
        findings.append({"code": "vally-config-missing", "source": ".vally.yaml", "severity": "info", "message": "No upstream config exists; a minimal bundle config was generated."})

    patterns = _eval_patterns(config_value, findings, eval_filename_patterns)
    casefolded: dict[str, str] = {}
    for identity in raw_files:
        prior = casefolded.get(identity.casefold())
        if prior and prior != identity:
            raise PortError(f"Case-insensitive output collision between {prior!r} and {identity!r}.")
        casefolded[identity.casefold()] = identity

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.vally-staging-", dir=destination.parent))
    try:
        (staging / "evals").mkdir(parents=True, exist_ok=True)
        (staging / "results").mkdir(parents=True, exist_ok=True)
        manifest_hash = _write_bytes(staging, _MANIFEST, manifest_bytes)
        _record(source_records, _MANIFEST, _MANIFEST, "distribution-source-manifest", manifest_hash)
        dependency_artifacts: list[dict[str, Any]] = []
        if source_inventory is not None:
            inventory_raw = safe_path(distribution_root, source_inventory["path"]).read_bytes()
            if digest(inventory_raw) != source_inventory["sha256"]:
                raise PortError("Distribution source inventory changed during preparation.")
            dependency_artifacts = json.loads(inventory_raw).get("artifacts", [])
            inventory_hash = _write_bytes(staging, source_inventory["path"], inventory_raw)
            _record(source_records, source_inventory["path"], source_inventory["path"], "distribution-source-inventory", inventory_hash)
        fixture_bundle_receipt_path = "generated/vally-fixture-dependencies.json"
        fixture_bundle_receipt_bytes: bytes | None = None
        fixture_proof_count = 0
        if fixture_dependency_receipt is not None and fixture_dependency_root is not None:
            from .locked_fixture_dependencies import _package_root_from_reference

            bundle_packages: list[dict[str, Any]] = []
            copied_proofs: dict[str, dict[str, Any]] = {}
            for package in fixture_dependency_receipt["packages"]:
                proof = dict(package["tarball"])
                selected_integrity = proof["integrity"]
                bundled = copied_proofs.get(selected_integrity)
                if bundled is None:
                    output_path = (
                        "generated/vally-fixture-dependencies/tarballs/"
                        + PurePosixPath(proof["path"]).name
                    )
                    _copy_file(
                        fixture_dependency_root,
                        proof["path"],
                        staging,
                        output_path,
                        source_records,
                        "fixture-dependency-sri-proof",
                    )
                    bundled = {**proof, "path": output_path}
                    copied_proofs[selected_integrity] = bundled
                bundle_packages.append({**package, "tarball": bundled})
            fixture_proof_count = len(copied_proofs)
            bundle_receipt = {
                "schema_version": fixture_dependency_receipt["schema_version"],
                "source": fixture_dependency_receipt["source"],
                "verification": fixture_dependency_receipt["verification"],
                **({
                    "extra_roots": fixture_dependency_receipt["extra_roots"],
                    "selected_optional_roots": fixture_dependency_receipt["selected_optional_roots"],
                } if "extra_roots" in fixture_dependency_receipt else {}),
                "packages": bundle_packages,
                "summary": fixture_dependency_receipt["summary"],
            }
            fixture_bundle_receipt_bytes = (
                json.dumps(bundle_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            fixture_bundle_receipt_hash = _write_bytes(
                staging, fixture_bundle_receipt_path, fixture_bundle_receipt_bytes
            )
            _record(
                source_records,
                "prepared-fixture-dependencies/fixture-dependencies.json",
                fixture_bundle_receipt_path,
                "fixture-dependency-receipt",
                fixture_bundle_receipt_hash,
            )
        converted_count = 0
        custom_converted_count = 0
        copied_count = 0
        resource_count = 0
        dependency_file_supplements = 0
        native_role_profiles = 0

        for identity in sorted(raw_files, key=lambda item: (item.casefold(), item)):
            raw = raw_files[identity]
            output_bytes = raw
            status = "copied-unchanged"
            if identity in custom_identities and not any(
                fnmatch.fnmatchcase(PurePosixPath(identity).name, pattern) for pattern in patterns
            ):
                archive_path = f"custom/{identity}"
                archive_hash = _write_bytes(staging, archive_path, raw)
                _record(source_records, identity, archive_path, "preserved-original", archive_hash)
            if any(fnmatch.fnmatchcase(PurePosixPath(identity).name, pattern) for pattern in patterns):
                try:
                    spec = _read_yaml(raw, identity)
                    if not isinstance(spec, Mapping):
                        raise PortError(f"Eval spec root must be a mapping: {identity}.")
                    spec_directory = PurePosixPath(identity).parent.as_posix()
                    additions = supplement_environment_dependencies(
                        spec, artifact_manifest=artifact_manifest,
                        artifacts=dependency_artifacts, spec_directory=spec_directory,
                    )
                    dependency_file_supplements += len(additions)
                    local_manifest: dict[str, Any] = {}
                    refs = _environment_references(spec, spec_directory)
                    for field, source_id in refs:
                        if source_id in local_manifest:
                            if field == "skill" and not source_id.startswith("evals/"):
                                resource = _manifest_resource(artifact_manifest, source_id)
                                _copy_resource(
                                    distribution_root,
                                    source_id,
                                    resource,
                                    staging,
                                    spec_directory,
                                    source_records,
                                    is_skill=True,
                                )
                                resource_count += 1
                            continue
                        if source_id.startswith("evals/") and source_id not in additions:
                            resource = _local_resource(input_roots.get(source_id, input_roots[identity]), source_id, spec_directory, staging, source_records)
                            local_manifest[source_id] = {"resources": [resource]}
                        elif source_id.startswith("node_modules/"):
                            from .locked_fixture_dependencies import _package_root_from_reference

                            if fixture_dependency_root is None:
                                raise PortError(
                                    f"Vally fixture dependency {source_id!r} requires a prepared lock-SRI dependency root."
                                )
                            package_root = _package_root_from_reference(source_id)
                            if package_root not in fixture_package_paths:
                                raise PortError(
                                    f"Vally fixture dependency {source_id!r} is outside the prepared lockfile closure."
                                )
                            resource = source_id
                            local_manifest[source_id] = {"resources": [resource]}
                            _copy_resource(
                                fixture_dependency_root,
                                source_id,
                                resource,
                                staging,
                                spec_directory,
                                source_records,
                            )
                            resource_count += 1
                        else:
                            resource = _manifest_resource(artifact_manifest, source_id)
                            local_manifest[source_id] = {**artifact_manifest.get(source_id, {}), "resources": [resource]}
                            _copy_resource(
                                distribution_root,
                                source_id,
                                resource,
                                staging,
                                spec_directory,
                                source_records,
                                is_skill=field == "skill",
                            )
                            resource_count += 1
                    converted = convert_vally_spec(spec, {**artifact_manifest, **local_manifest}, spec_directory=spec_directory)
                    native_role_profiles += _copy_native_role_profiles(
                        converted, distribution_root, staging, spec_directory, source_records,
                    )
                    output_bytes = _dump_yaml(converted)
                    status = "converted-eval-spec"
                    converted_count += 1
                    custom_converted_count += int(identity in custom_identities)
                    original_path = f"{'custom' if identity in custom_identities else 'upstream'}/{identity}"
                    original_hash = _write_bytes(staging, original_path, raw)
                    _record(source_records, identity, original_path, "preserved-original", original_hash)
                except (PortError, OSError, TypeError, ValueError) as exc:
                    message = str(exc)[:1500]
                    findings.append({"code": "eval-conversion-error", "source": identity, "severity": "error", "message": message})
                    conversion_errors.append({"source": identity, "error": message})
                    status = "preserved-unconverted"
                    original_path = f"{'custom' if identity in custom_identities else 'upstream'}/{identity}"
                    original_hash = _write_bytes(staging, original_path, raw)
                    _record(source_records, identity, original_path, "preserved-original", original_hash)
            output_hash = _write_bytes(staging, identity, output_bytes)
            _record(source_records, identity, identity, status, output_hash)
            copied_count += 1

        if config_raw is not None:
            original_config_hash = _write_bytes(staging, "upstream/.vally.yaml", config_raw)
            _record(source_records, ".vally.yaml", "upstream/.vally.yaml", "preserved-original", original_config_hash)
        config_bytes = _dump_yaml(_bundle_config(config_value, findings, patterns))
        config_hash = _write_bytes(staging, ".vally.yaml", config_bytes)
        _record(source_records, ".vally.yaml", ".vally.yaml", "bundle-config", config_hash)

        normalized: dict[str, Any] = {}
        for source in sorted(source_records, key=lambda item: (item.casefold(), item)):
            record = source_records[source]
            if source in custom_identities:
                record["origin"] = "codex-custom"
            record["outputs"] = sorted(record["outputs"], key=lambda item: (item["path"].casefold(), item["path"], item["status"]))
            normalized[source] = record
        dump_json(staging / _SOURCE_MAP, normalized)

        blocking = any(item["severity"] in {"warning", "error"} for item in findings) or bool(conversion_errors)
        report: dict[str, Any] = {
            "schema_version": 1,
            "status": "partial" if blocking else "complete",
            "success": not blocking,
            "source": {
                "upstream_root_name": upstream.name,
                "distribution_root_name": distribution_root.name,
                "manifest": _MANIFEST,
                "manifest_sha256": digest(manifest_bytes),
                **({"source_inventory": source_inventory} if source_inventory is not None else {}),
                **({
                    "fixture_dependencies": {
                        "receipt": fixture_bundle_receipt_path,
                        "receipt_sha256": digest(fixture_bundle_receipt_bytes),
                        "upstream_sha": fixture_dependency_receipt["source"]["upstream_sha"],
                        "package_json_sha256": fixture_dependency_receipt["source"]["package_json_sha256"],
                        "package_lock_sha256": fixture_dependency_receipt["source"]["package_lock_sha256"],
                        "package_count": len(fixture_dependency_receipt["packages"]),
                        "proof_tarball_count": fixture_proof_count,
                    }
                } if fixture_dependency_receipt is not None and fixture_bundle_receipt_bytes is not None else {}),
                **({"custom": {"root_name": custom.name, "files": [{"path": identity, "sha256": digest(raw_files[identity])} for identity in sorted(custom_identities)]}} if custom is not None else {}),
            },
            "report_path": _REPORT,
            "execution": {
                "commands_executed": False,
                "upstream_scripts_executed": False,
                "upstream_executors_run": False,
                "upstream_graders_run": False,
                "models_run": False,
            },
            "config": {"bundle_config": ".vally.yaml", "evals_path": "evals", "results_path": "results", "eval_filename_patterns": list(patterns)},
            "summary": {
                "upstream_eval_files_discovered": len(eval_paths),
                "eval_files_copied": copied_count,
                "eval_specs_converted": converted_count,
                "upstream_eval_specs_converted": converted_count - custom_converted_count,
                "custom_eval_files_discovered": len(custom_paths),
                "custom_eval_specs_converted": custom_converted_count,
                "manifest_resource_references": resource_count,
                "dependency_file_supplements": dependency_file_supplements,
                "native_role_profiles": native_role_profiles,
                "fixture_dependency_packages": len(fixture_dependency_receipt["packages"]) if fixture_dependency_receipt is not None else 0,
                "fixture_dependency_proof_tarballs": fixture_proof_count,
                "source_map_records": len(normalized),
                "findings": len(findings),
                "conversion_errors": len(conversion_errors),
            },
            "unsupported": {"conversion_errors": sorted(conversion_errors, key=lambda item: (item["source"].casefold(), item["error"]))},
            "findings": sorted(findings, key=lambda item: (item["source"].casefold(), item["code"], item["message"])),
            "source_to_output": _SOURCE_MAP,
        }
        dump_json(staging / _REPORT, report)
        if destination.exists() or destination.is_symlink():
            raise PortError(f"Bundle output appeared during preparation: {destination}")
        os.replace(staging, destination)
        report["output_dir"] = destination.as_posix()
        report["report_path"] = (destination / _REPORT).as_posix()
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


__all__ = ["prepare_vally_bundle"]
