"""Scenario staging and structural/native-host acceptance helpers."""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .common import PortError, digest, safe_path, json_safe as _json_safe
from .rules import match_rules

_MAX_FIXTURE_FILE_BYTES = 2 * 1024 * 1024


_MAX_SCENARIO_FILE_BYTES = 4 * 1024 * 1024


_MAX_PROMPT_BYTES = 24 * 1024


_MAX_CHECKPOINT_FILE_BYTES = 4 * 1024 * 1024


_MAX_CHECKPOINT_TOTAL_BYTES = 32 * 1024 * 1024


_MAX_CHECKPOINT_FILES = 256


_MAX_NATIVE_WORKSPACE_FILES = 32768


_MAX_NATIVE_WORKSPACE_BYTES = 256 * 1024 * 1024


_MAX_NATIVE_WORKSPACE_TIMEOUT_SECONDS = 30


_MAX_NATIVE_WORKSPACE_INPUT_BYTES = 256 * 1024


_MAX_NATIVE_WORKSPACE_INVENTORY_OUTPUT_BYTES = 32 * 1024 * 1024


_MAX_NATIVE_WORKSPACE_READ_OUTPUT_BYTES = ((_MAX_CHECKPOINT_TOTAL_BYTES + 2) // 3) * 4 + 1024 * 1024


_RUNTIME_CHECKS = {
    "approval_gate",
    "handoff_preserves_arguments",
    "no_fake_output",
    "no_source_edit",
    "no_unapproved_mutation",
    "release_blocker",
    "state_resumable",
    "state_write",
    "tracking_artifact_write",
    "fixture_hash_at_turn",
}


_POST_SESSION_CHECKS = {
    "workspace_file_exists",
    "workspace_file_absent",
    "workspace_file_contains",
    "workspace_file_unchanged",
    "workspace_glob_exists",
    "workspace_json_value",
}


_STAGE_PRECONDITION_CHECKS = {"fixture_exists", "fixture_absent", "fixture_contains"}




def _scenario_data(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PortError(f"Scenario file does not exist: {path}")
    if path.stat().st_size > _MAX_SCENARIO_FILE_BYTES:
        raise PortError(f"Scenario file exceeds {_MAX_SCENARIO_FILE_BYTES} bytes: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortError(f"Could not read scenario JSON at {path}: {exc}") from exc
    if (
        not isinstance(data, dict)
        or isinstance(data.get("schema_version"), bool)
        or data.get("schema_version") != 1
        or not isinstance(data.get("scenarios"), list)
    ):
        raise PortError("Scenario file must contain schema_version 1 and a scenarios array")
    seen: set[str] = set()
    for position, scenario in enumerate(data["scenarios"]):
        if not isinstance(scenario, dict):
            raise PortError(f"scenarios[{position}] must be an object")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise PortError(f"scenarios[{position}].id must be a non-empty string")
        if scenario_id in seen:
            raise PortError(f"Duplicate scenario id: {scenario_id}")
        seen.add(scenario_id)
        if not isinstance(scenario.get("prompts"), list) or not scenario["prompts"] or any(not isinstance(prompt, str) for prompt in scenario["prompts"]):
            raise PortError(f"Scenario {scenario_id!r} must declare one or more prompt strings")
        if any(len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES for prompt in scenario["prompts"]):
            raise PortError(f"Scenario {scenario_id!r} contains a prompt larger than {_MAX_PROMPT_BYTES} UTF-8 bytes")
        if not isinstance(scenario.get("rubric"), list) or not scenario["rubric"]:
            raise PortError(f"Scenario {scenario_id!r} must declare a rubric array")
        if not isinstance(scenario.get("deterministic_checks"), list):
            raise PortError(f"Scenario {scenario_id!r} must declare deterministic_checks")
        invocation_mode = scenario.get("invocation_mode", "explicit")
        if not isinstance(invocation_mode, str) or invocation_mode not in {"explicit", "implicit"}:
            raise PortError(f"Scenario {scenario_id!r}.invocation_mode must be 'explicit' or 'implicit'")
    return data


def _normalized_source_path(value: str) -> str:
    return value.strip().replace("\\", "/").removeprefix("./").casefold()


def _installed_plugin_root(stage_root: Path) -> Path | None:
    candidates = (
        stage_root / ".agents" / "plugins" / "hve-core-codex",
        stage_root / ".codex" / "plugins" / "hve-core-codex",
        stage_root / ".codex" / "hve-core-codex" / "plugin-resources",
    )
    return next((path for path in candidates if path.is_dir()), None)


def _resolve_entrypoints(scenario: dict[str, Any], stage_root: Path) -> dict[str, Any]:
    """Resolve prompt placeholders against the plugin actually installed in the fixture."""
    prompts = list(scenario["prompts"])
    invocation_mode = scenario.get("invocation_mode", "explicit")
    declared = scenario.get("entrypoints")
    if declared is None:
        return {"status": "not-declared", "invocation_mode": invocation_mode, "prompts": prompts, "resolved": []}
    if not isinstance(declared, list) or not declared:
        return {
            "status": "invalid-declaration",
            "invocation_mode": invocation_mode,
            "prompts": prompts,
            "resolved": [],
            "errors": ["entrypoints must be a non-empty array"],
        }

    plugin_root = _installed_plugin_root(stage_root)
    aliases_candidates = (
        plugin_root / "aliases.json" if plugin_root else None,
        stage_root / ".codex" / "hve-core-codex" / "plugin-resources" / "aliases.json",
    )
    aliases_path = next((path for path in aliases_candidates if path is not None and path.is_file()), None)
    try:
        raw_aliases = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw_aliases = None
    aliases = raw_aliases if isinstance(raw_aliases, list) else []
    aliases_by_source: dict[str, list[dict[str, Any]]] = {}
    for alias in aliases:
        if isinstance(alias, dict) and isinstance(alias.get("source"), str):
            aliases_by_source.setdefault(_normalized_source_path(alias["source"]), []).append(alias)

    resolved: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for position, entry in enumerate(declared):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("source"), str):
            errors.append(f"entrypoints[{position}] must declare string id and source fields")
            continue
        entry_id = entry["id"]
        matches = aliases_by_source.get(_normalized_source_path(entry["source"]), [])
        usable = [item for item in matches if isinstance(item.get("name"), str) and item.get("name")]
        if not usable:
            errors.append(f"no installed alias maps source {entry['source']!r}")
            continue
        alias = sorted(usable, key=lambda item: (not bool(item.get("user_invocable")), str(item["name"]).casefold()))[0]
        if entry_id in resolved:
            errors.append(f"duplicate entrypoint id {entry_id!r}")
            continue
        user_invocable = bool(alias.get("user_invocable"))
        if entry_id == "primary" and not user_invocable:
            errors.append(f"primary entrypoint {entry['source']!r} is not user-invocable")
            continue
        name = str(alias["name"])
        plugin_skill = plugin_root / "skills" / name / "SKILL.md" if plugin_root else None
        standalone_skill = stage_root / ".agents" / "skills" / name / "SKILL.md"
        skill_path = plugin_skill if plugin_skill and plugin_skill.is_file() else standalone_skill
        if entry_id == "primary" and (skill_path is None or not skill_path.is_file()):
            errors.append(f"primary entrypoint {name!r} has no installed skill entry")
            continue
        resolved[entry_id] = {
            "id": entry_id,
            "source": entry["source"],
            "name": name,
            "kind": alias.get("kind"),
            "user_invocable": user_invocable,
            "invocation": f"${name}" if user_invocable else name,
            "skill_path": skill_path.relative_to(stage_root).as_posix() if skill_path and skill_path.is_file() else None,
        }

    primary = resolved.get("primary")
    if primary is None:
        errors.append("entrypoints must resolve a user-invocable primary entrypoint")
    elif invocation_mode == "implicit":
        if "$actual-entrypoint" in "\n".join(prompts) or any(re.search(r"\$entrypoint\[[^]]+\]", prompt) for prompt in prompts):
            errors.append("implicit invocation_mode prompts must not contain explicit entrypoint placeholders")
    else:
        if "$actual-entrypoint" not in "\n".join(prompts):
            errors.append("prompts must invoke $actual-entrypoint")
        else:
            for index, prompt in enumerate(prompts):
                prompts[index] = prompt.replace("$actual-entrypoint", primary["invocation"])

    for index, prompt in enumerate(prompts):
        def replace_entrypoint(match: re.Match[str]) -> str:
            entry_id = match.group(1)
            item = resolved.get(entry_id)
            if item is None:
                errors.append(f"prompt references unresolved entrypoint id {entry_id!r}")
                return match.group(0)
            return item["invocation"]

        prompts[index] = re.sub(r"\$entrypoint\[([A-Za-z0-9_-]+)\]", replace_entrypoint, prompt)
        if "$actual-entrypoint" in prompts[index] or re.search(r"\$entrypoint\[[^]]+\]", prompts[index]):
            errors.append(f"prompt {index + 1} contains an unresolved entrypoint placeholder")

    for index, prompt in enumerate(prompts):
        if len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
            errors.append(f"resolved prompt {index + 1} exceeds {_MAX_PROMPT_BYTES} UTF-8 bytes")
    return {
        "status": "resolved" if not errors else "unresolved",
        "invocation_mode": invocation_mode,
        "prompts": prompts,
        "resolved": [resolved[key] for key in sorted(resolved)],
        "errors": errors,
        "aliases_path": aliases_path.relative_to(stage_root).as_posix() if aliases_path and aliases_path.is_file() else None,
    }


def _source_artifact_evidence(distribution: Path, sources: list[str]) -> list[dict[str, Any]]:
    inventory_path = distribution / "generated" / "source-inventory.json"
    provenance_path = distribution / "generated" / "provenance.json"
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [{"source": source, "status": "source-inventory-unavailable"} for source in sources]
    records = inventory.get("artifacts", []) if isinstance(inventory, dict) else []
    by_source = {str(record.get("source")): record for record in records if isinstance(record, dict)}
    upstream_sha = provenance.get("upstream_sha") if isinstance(provenance, dict) else None
    result: list[dict[str, Any]] = []
    for source in sources:
        record = by_source.get(source)
        if record is None:
            result.append({"source": source, "status": "source-artifact-unmapped", "upstream_sha": upstream_sha})
            continue
        result.append({
            "source": source,
            "status": "mapped",
            "upstream_sha": record.get("source_sha"),
            "distribution_upstream_sha": upstream_sha,
            "source_file_sha256": record.get("hash"),
            "outputs": record.get("outputs", []),
            "originals": record.get("originals", []),
            "original_source_verified": None,
            "original_source_verification_status": "not-checked",
        })
    return result


def _fixture_entries(scenario: dict[str, Any]) -> list[tuple[str, str]]:
    raw = scenario.get("fixture_files", scenario.get("fixtures", {}))
    if raw is None:
        return []
    if isinstance(raw, dict):
        result: list[tuple[str, str]] = []
        for relative, value in raw.items():
            content = value.get("content") if isinstance(value, dict) and set(value) == {"content"} else value
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if not isinstance(content, str):
                raise PortError(f"Scenario {scenario['id']!r} fixture {relative!r} must have text content")
            result.append((str(relative), content))
        return result
    if isinstance(raw, list):
        result = []
        for position, item in enumerate(raw):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
                raise PortError(f"Scenario {scenario['id']!r} fixture_files[{position}] needs path and content strings")
            result.append((item["path"], item["content"]))
        return result
    raise PortError(f"Scenario {scenario['id']!r} fixture_files must be an object or array")


def _stage_fixtures(scenario: dict[str, Any], stage_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total_bytes = 0
    seen_paths: set[str] = set()
    for relative, content in _fixture_entries(scenario):
        normalized_key = relative.replace("\\", "/").casefold()
        if normalized_key in seen_paths:
            errors.append({"code": "duplicate-fixture-path", "path": relative, "message": "Scenario fixture paths must be unique, including case-insensitive aliases."})
            continue
        seen_paths.add(normalized_key)
        encoded = content.encode("utf-8")
        total_bytes += len(encoded)
        if len(encoded) > _MAX_FIXTURE_FILE_BYTES or total_bytes > _MAX_FIXTURE_FILE_BYTES:
            errors.append({"code": "fixture-size-limit", "path": relative, "message": "Scenario fixture data exceeds the 2 MiB staging budget."})
            continue
        try:
            target = safe_path(stage_root, relative)
            if target.exists():
                errors.append({"code": "fixture-collision", "path": relative, "message": "A curated fixture would overwrite an installed distribution file."})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
        except (OSError, PortError) as exc:
            errors.append({"code": "fixture-path-or-write-error", "path": relative, "message": str(exc)})
            continue
        records.append({"path": relative.replace("\\", "/"), "bytes": len(encoded), "sha256": digest(encoded)})
    return records, errors


def _json_pointer(value: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, value
    current = value
    for raw_part in pointer.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current


def _distribution_rules_root(distribution: Path) -> Path | None:
    for candidate in (distribution, distribution / "codex-overlay", distribution / "rules", distribution / "codex-overlay" / "rules"):
        if (candidate / "rules" / "index.json").is_file() or (candidate / "index.json").is_file():
            return candidate
    return None


def _path_matches_glob(relative: str, pattern: str) -> bool:
    """Match a POSIX workspace glob against an already-validated relative path."""
    path_parts = PurePosixPath(relative).parts
    pattern_parts = PurePosixPath(pattern).parts
    memo: dict[tuple[int, int], bool] = {}

    def match(pattern_index: int, path_index: int) -> bool:
        key = (pattern_index, path_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern_parts):
            result = path_index == len(path_parts)
        elif pattern_parts[pattern_index] == "**":
            result = match(pattern_index + 1, path_index) or (
                path_index < len(path_parts) and match(pattern_index, path_index + 1)
            )
        else:
            result = (
                path_index < len(path_parts)
                and fnmatch.fnmatchcase(path_parts[path_index], pattern_parts[pattern_index])
                and match(pattern_index + 1, path_index + 1)
            )
        memo[key] = result
        return result

    return bool(pattern_parts) and match(0, 0)


def _validate_workspace_glob_pattern(value: Any) -> str:
    """Validate a relative workspace glob without resolving it on the host filesystem."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise PortError("Workspace glob returned an unsafe relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(part.casefold() == ".git" for part in parts):
        raise PortError("Workspace glob returned an unsafe relative path")
    for part in parts:
        if part.rstrip(" .") != part or any(character in '<>"|' or ord(character) < 32 for character in part):
            raise PortError("Workspace glob returned an unsafe relative path")
    return value


def _workspace_evidence_file_bytes(
    workspace: Path,
    relative: str,
    *,
    workspace_evidence: dict[str, Any] | None,
    native_command: str | None,
    native_sandbox_mode: str | None,
) -> tuple[bytes | None, str | None]:
    try:
        target = safe_path(workspace, relative)
        return target.read_bytes(), None
    except (OSError, PortError, ValueError) as exc:
        if (
            isinstance(exc, (PortError, ValueError))
            or not _is_native_windows()
            or not native_command
            or native_sandbox_mode not in {"elevated", "unelevated"}
        ):
            return None, str(exc)[:500]
    files = workspace_evidence.get("files", {}) if isinstance(workspace_evidence, dict) else {}
    if not isinstance(files, dict) or relative not in files:
        return None, "The workspace snapshot did not contain the requested file."
    found, errors = _native_workspace_read_files(workspace, [relative], native_command, native_sandbox_mode)
    if errors:
        return None, errors[0].get("reason", "Native sandbox file read failed.")
    return found.get(relative), None if relative in found else "Native sandbox did not return the requested file."


def _check_result(
    scenario_id: str,
    check: dict[str, Any],
    stage_root: Path,
    distribution: Path,
    baseline_hashes: dict[str, str] | None = None,
    *,
    workspace_evidence: dict[str, Any] | None = None,
    native_command: str | None = None,
    native_sandbox_mode: str | None = None,
) -> dict[str, Any]:
    check_id = str(check.get("id") or check.get("kind") or "check")
    kind = check.get("kind")
    result: dict[str, Any] = {"scenario_id": scenario_id, "check_id": check_id, "kind": kind}
    if kind in _RUNTIME_CHECKS or check.get("runtime_required") is True:
        return {**result, "status": "pending-runtime", "reason": "Requires an authorized Codex session and observed post-run state."}
    if kind in {"fixture_exists", "fixture_absent", "fixture_contains"}:
        try:
            target = safe_path(stage_root, str(check.get("path", "")))
        except PortError as exc:
            return {**result, "status": "failed", "reason": str(exc)}
        exists = target.is_file()
        if kind == "fixture_exists":
            passed = exists
        elif kind == "fixture_absent":
            passed = not target.exists()
        else:
            try:
                passed = exists and str(check.get("text", "")) in target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return {**result, "status": "failed", "reason": str(exc)}
        return {**result, "status": "passed" if passed else "failed", "path": str(check.get("path", ""))}
    if kind in {"workspace_file_exists", "workspace_file_absent", "workspace_file_contains", "workspace_file_unchanged", "workspace_glob_exists", "workspace_json_value"}:
        relative = str(check.get("path", ""))
        snapshot_files = workspace_evidence.get("files", {}) if isinstance(workspace_evidence, dict) else None
        snapshot_errors = workspace_evidence.get("errors", []) if isinstance(workspace_evidence, dict) else []
        if not isinstance(snapshot_files, dict):
            snapshot_files = None
        if not isinstance(snapshot_errors, list):
            snapshot_errors = []
        target: Path | None = None
        try:
            if kind == "workspace_glob_exists":
                _validate_workspace_glob_pattern(relative)
            else:
                _native_relative_path_parts(relative)
                if snapshot_files is None:
                    target = safe_path(stage_root, relative)
        except PortError as exc:
            return {**result, "status": "failed", "reason": str(exc)}
        except OSError as exc:
            return {**result, "status": "pending-runtime", "reason": f"Workspace path metadata could not be verified: {str(exc)[:500]}"}
        if kind == "workspace_file_exists":
            if snapshot_files is not None:
                passed = relative in snapshot_files
                if not passed and snapshot_errors:
                    return {**result, "status": "pending-runtime", "reason": "Workspace inventory was incomplete, so file absence could not be established."}
            else:
                try:
                    passed = target is not None and target.is_file()
                except OSError as exc:
                    return {**result, "status": "pending-runtime", "reason": f"Workspace file status could not be verified: {str(exc)[:500]}"}
            evidence: dict[str, Any] = {"path": relative}
        elif kind == "workspace_file_absent":
            if snapshot_files is not None:
                passed = relative not in snapshot_files
                if passed and snapshot_errors:
                    return {**result, "status": "pending-runtime", "reason": "Workspace inventory was incomplete, so file absence could not be established."}
            else:
                try:
                    passed = target is not None and not target.exists()
                except OSError as exc:
                    return {**result, "status": "pending-runtime", "reason": f"Workspace file absence could not be verified: {str(exc)[:500]}"}
            evidence = {"path": relative}
        elif kind == "workspace_file_contains":
            try:
                if snapshot_files is not None and relative not in snapshot_files:
                    if snapshot_errors:
                        return {**result, "status": "pending-runtime", "reason": "Workspace inventory was incomplete, so file absence could not be established."}
                    passed = False
                else:
                    raw = _workspace_evidence_file_bytes(
                        stage_root,
                        relative,
                        workspace_evidence=workspace_evidence,
                        native_command=native_command,
                        native_sandbox_mode=native_sandbox_mode,
                    )[0]
                    if raw is None:
                        return {**result, "status": "pending-runtime", "reason": "The workspace file could not be read for its post-session check."}
                    passed = str(check.get("text", "")) in raw.decode("utf-8")
                evidence = {"path": relative}
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                return {**result, "status": "failed", "reason": str(exc)}
        elif kind == "workspace_file_unchanged":
            expected_hash = (baseline_hashes or {}).get(relative)
            if snapshot_files is not None:
                record = snapshot_files.get(relative)
                actual_hash = record.get("sha256") if isinstance(record, dict) else None
                if record is None and snapshot_errors:
                    return {**result, "status": "pending-runtime", "reason": "Workspace inventory was incomplete, so the file hash could not be established."}
            else:
                try:
                    actual_hash = digest(target.read_bytes()) if target.is_file() else None
                except OSError as exc:
                    return {**result, "status": "pending-runtime", "reason": str(exc)}
            passed = expected_hash is not None and actual_hash == expected_hash
            evidence = {"path": relative, "before_sha256": expected_hash, "after_sha256": actual_hash}
        elif kind == "workspace_glob_exists":
            if snapshot_files is not None:
                matches = sorted(path for path in snapshot_files if _path_matches_glob(path, relative))
            else:
                try:
                    matches = []
                    for item in stage_root.glob(relative):
                        if not item.is_file():
                            continue
                        try:
                            candidate = item.relative_to(stage_root).as_posix()
                            matches.append(_validate_native_relative_path(stage_root, candidate))
                        except PortError:
                            continue
                except PermissionError as exc:
                    return {**result, "status": "pending-runtime", "reason": f"Workspace glob evidence could not be read: {str(exc)[:500]}"}
                except (OSError, RuntimeError, ValueError) as exc:
                    return {**result, "status": "failed", "reason": str(exc)[:500]}
            minimum = check.get("minimum", 1)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
                return {**result, "status": "failed", "reason": "minimum must be a positive integer"}
            passed = len(matches) >= minimum
            if not passed and snapshot_files is not None and snapshot_errors:
                return {**result, "status": "pending-runtime", "reason": "Workspace inventory was incomplete, so the glob result could not be established."}
            evidence = {"pattern": relative, "matches": matches, "minimum": minimum}
        else:
            try:
                if snapshot_files is not None and relative not in snapshot_files:
                    if snapshot_errors:
                        return {**result, "status": "pending-runtime", "reason": "Workspace inventory was incomplete, so file absence could not be established."}
                    return {**result, "status": "failed", "reason": "Workspace JSON file does not exist."}
                raw = _workspace_evidence_file_bytes(
                    stage_root,
                    relative,
                    workspace_evidence=workspace_evidence,
                    native_command=native_command,
                    native_sandbox_mode=native_sandbox_mode,
                )[0]
                if raw is None:
                    return {**result, "status": "pending-runtime", "reason": "The workspace JSON file could not be read for its post-session check."}
                data = json.loads(raw.decode("utf-8"))
                present, actual = _json_pointer(data, str(check.get("pointer", "")))
                passed = present and actual == check.get("expected")
                evidence = {"path": relative, "actual": _json_safe(actual) if present else None}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                return {**result, "status": "failed", "reason": str(exc)}
        return {**result, "status": "passed" if passed else "failed", **evidence}
    if kind in {"distribution_file_exists", "distribution_file_absent"}:
        try:
            target = safe_path(distribution, str(check.get("path", "")))
        except PortError as exc:
            return {**result, "status": "failed", "reason": str(exc)}
        exists = target.is_file()
        passed = exists if kind == "distribution_file_exists" else not target.exists()
        return {**result, "status": "passed" if passed else "failed", "path": str(check.get("path", ""))}
    if kind == "distribution_json_value":
        try:
            target = safe_path(distribution, str(check.get("path", "")))
            data = json.loads(target.read_text(encoding="utf-8"))
            present, actual = _json_pointer(data, str(check.get("pointer", "")))
            passed = present and actual == check.get("expected")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, PortError) as exc:
            return {**result, "status": "failed", "reason": str(exc)}
        return {**result, "status": "passed" if passed else "failed", "actual": _json_safe(actual) if present else None}
    if kind == "rules_match":
        rules_root = _distribution_rules_root(distribution)
        if rules_root is None:
            return {**result, "status": "failed", "reason": "No indexed rules registry exists in the distribution."}
        try:
            actual = [rule["id"] for rule in match_rules(rules_root, [str(check.get("path", ""))])]
        except (OSError, ValueError, PortError) as exc:
            return {**result, "status": "failed", "reason": str(exc)}
        expected = set(str(item) for item in check.get("expected_ids", []))
        excluded = set(str(item) for item in check.get("excluded_ids", []))
        passed = expected.issubset(actual) and not excluded.intersection(actual)
        return {**result, "status": "passed" if passed else "failed", "actual_rule_ids": actual}
    if kind == "source_artifact_recovery":
        sources = check.get("sources")
        if not isinstance(sources, list) or not sources or any(not isinstance(item, str) for item in sources):
            return {**result, "status": "unsupported", "reason": "sources must be a non-empty array of upstream source paths."}
        mapping_path = distribution / "generated" / "source-to-output.json"
        inventory_path = distribution / "generated" / "source-inventory.json"
        provenance_path = distribution / "generated" / "provenance.json"
        try:
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {**result, "status": "failed", "reason": f"source provenance files are unavailable: {exc}"}
        inventory_records = {
            str(item.get("source")): item
            for item in inventory.get("artifacts", [])
            if isinstance(item, dict) and isinstance(item.get("source"), str)
        }
        plugin_root = _installed_plugin_root(stage_root)
        outcomes: list[dict[str, Any]] = []
        for source in sources:
            record = inventory_records.get(source)
            destinations = mapping.get(source) if isinstance(mapping, dict) else None
            resource_paths = destinations.get("resources", []) if isinstance(destinations, dict) else []
            original_paths = destinations.get("originals") if isinstance(destinations, dict) else None
            resource_results: list[dict[str, Any]] = []
            original_results: list[dict[str, Any]] = []
            if not isinstance(record, dict) or plugin_root is None:
                outcomes.append({
                    "source": source,
                    "status": "unmapped-or-uninstalled",
                    "resource_recovery": resource_results,
                    "original_source_verified": False,
                })
                continue
            source_sha = record.get("source_sha")
            expected_source_sha = provenance.get("upstream_sha") if isinstance(provenance, dict) else None
            if isinstance(resource_paths, list):
                for resource_path in resource_paths:
                    prefix = "plugins/hve-core-codex/"
                    if not isinstance(resource_path, str) or not resource_path.startswith(prefix):
                        resource_results.append({"distribution_path": resource_path, "status": "invalid-resource-mapping"})
                        continue
                    suffix = resource_path[len(prefix):]
                    try:
                        packaged = safe_path(distribution, resource_path)
                        standalone = stage_root / ".agents" / "skills"
                        installed = (
                            safe_path(standalone, suffix[len("skills/"):])
                            if suffix.startswith("skills/") and standalone.is_dir()
                            else safe_path(plugin_root, suffix)
                        )
                        packaged_hash = digest(packaged.read_bytes()) if packaged.is_file() else None
                        installed_hash = digest(installed.read_bytes()) if installed.is_file() else None
                    except (OSError, PortError) as exc:
                        resource_results.append({"distribution_path": resource_path, "status": "read-error", "reason": str(exc)[:500]})
                        continue
                    resource_results.append({
                        "distribution_path": resource_path,
                        "installed_path": installed.relative_to(stage_root).as_posix(),
                        "packaged_sha256": packaged_hash,
                        "installed_sha256": installed_hash,
                        "status": "recovered" if packaged_hash and packaged_hash == installed_hash else "mismatch",
                    })

            originals_declared = "originals" in record or (isinstance(destinations, dict) and "originals" in destinations)
            record_originals = record.get("originals")
            original_source_verified = False
            if originals_declared:
                if (
                    not isinstance(original_paths, list)
                    or not original_paths
                    or not isinstance(record_originals, list)
                    or original_paths != record_originals
                ):
                    original_results.append({
                        "status": "original-source-mapping-mismatch",
                        "mapping_originals": original_paths,
                        "inventory_originals": record_originals,
                    })
                else:
                    source_hash = record.get("hash")
                    for original_path in original_paths:
                        prefix = "plugins/hve-core-codex/originals/"
                        if not isinstance(original_path, str) or not original_path.startswith(prefix):
                            original_results.append({"distribution_path": original_path, "status": "invalid-original-mapping"})
                            continue
                        suffix = original_path[len("plugins/hve-core-codex/"):]
                        try:
                            packaged = safe_path(distribution, original_path)
                            installed = safe_path(plugin_root, suffix)
                            packaged_hash = digest(packaged.read_bytes()) if packaged.is_file() else None
                            installed_hash = digest(installed.read_bytes()) if installed.is_file() else None
                        except (OSError, PortError) as exc:
                            original_results.append({"distribution_path": original_path, "status": "read-error", "reason": str(exc)[:500]})
                            continue
                        verified = bool(
                            source_hash
                            and packaged_hash == source_hash
                            and installed_hash == source_hash
                        )
                        original_results.append({
                            "distribution_path": original_path,
                            "installed_path": installed.relative_to(stage_root).as_posix(),
                            "inventory_source_sha256": source_hash,
                            "packaged_sha256": packaged_hash,
                            "installed_sha256": installed_hash,
                            "status": "verified" if verified else "mismatch",
                        })
                    original_source_verified = bool(original_results) and all(
                        item.get("status") == "verified" for item in original_results
                    )

            resource_recovered = (
                isinstance(resource_paths, list)
                and bool(resource_results)
                and all(item.get("status") == "recovered" for item in resource_results)
            )
            passed = (
                source_sha is not None
                and source_sha == expected_source_sha
                and resource_recovered
                and (not originals_declared or original_source_verified)
            )
            outcomes.append({
                "source": source,
                "source_sha": source_sha,
                "source_file_sha256": record.get("hash"),
                "distribution_upstream_sha": expected_source_sha,
                "status": "passed" if passed else "failed",
                "resource_recovery": resource_results,
                "original_source_verified": original_source_verified,
                "original_source_recovery": original_results,
            })
        passed = len(outcomes) == len(sources) and all(item.get("status") == "passed" for item in outcomes)
        return {**result, "status": "passed" if passed else "failed", "source_artifacts": outcomes}
    if kind == "staged_rules_match":
        paths = check.get("expected_by_path")
        if not isinstance(paths, dict) or not paths:
            return {**result, "status": "unsupported", "reason": "expected_by_path must map staged repo paths to expected rule IDs."}
        outcomes: dict[str, list[str]] = {}
        for path, expected_ids in sorted(paths.items(), key=lambda item: str(item[0]).casefold()):
            try:
                actual = [rule["id"] for rule in match_rules(stage_root, [str(path)])]
            except (OSError, ValueError, PortError) as exc:
                return {**result, "status": "failed", "path": str(path), "reason": str(exc)}
            outcomes[str(path)] = actual
            expected = {str(item) for item in expected_ids} if isinstance(expected_ids, list) else {str(expected_ids)}
            if set(actual) != expected:
                return {**result, "status": "failed", "actual_by_path": outcomes}
        return {**result, "status": "passed", "actual_by_path": outcomes}
    return {**result, "status": "unsupported", "reason": "No deterministic evaluator is registered for this check kind."}


def _is_native_windows() -> bool:
    return os.name == "nt"


_NATIVE_WORKSPACE_SCRIPT = r'''import base64, hashlib, json, os, stat, sys
from pathlib import Path

MAX_FILES = 32768
MAX_DIRS = 32768
MAX_INVENTORY_BYTES = 268435456
MAX_READ_FILES = 256
MAX_READ_FILE_BYTES = 4194304
MAX_READ_TOTAL_BYTES = 33554432
MAX_INPUT_BYTES = 262144

def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()

def relative_path(path, root):
    try:
        value = Path(path).relative_to(root).as_posix()
    except (OSError, ValueError):
        return "<workspace>"
    return value[:1000]

def safe_target(raw, root):
    if not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw or "\x00" in raw:
        raise ValueError("unsafe relative path")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts) or any(part.casefold() == ".git" for part in parts):
        raise ValueError("unsafe relative path")
    target = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if link_like(current):
            raise ValueError("symlink path is not eligible for evidence")
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("path escapes workspace")
    return target

def link_like(path):
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    if callable(checker) and checker():
        return True
    metadata = path.lstat()
    return bool(int(getattr(metadata, "st_file_attributes", 0)) & 1024)

def main():
    try:
        root = Path(sys.argv[1]).resolve(strict=True)
        action = sys.argv[2]
        raw_request = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw_request) > MAX_INPUT_BYTES:
            raise ValueError("request exceeds input limit")
        request = json.loads(raw_request.decode("utf-8")) if raw_request else None
        if not root.is_dir():
            raise ValueError("workspace root is unavailable")
    except Exception as exc:
        emit({"version": 1, "complete": False, "files": [], "errors": [{"path": "", "reason": str(exc)[:300]}]})
        return

    if action == "inventory":
        files = []
        errors = []
        total_bytes = 0
        directories_seen = 0
        truncated = False

        def onerror(exc):
            errors.append({"path": relative_path(getattr(exc, "filename", ""), root), "reason": (str(exc) or type(exc).__name__)[:300]})

        for directory, dirnames, filenames in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
            directories_seen += 1
            if directories_seen > MAX_DIRS:
                errors.append({"path": relative_path(directory, root), "reason": "native inventory directory limit exceeded"})
                truncated = True
                break
            current = Path(directory)
            retained_dirs = []
            for name in sorted(dirnames):
                child = current / name
                if name == ".git":
                    continue
                try:
                    if link_like(child):
                        continue
                except OSError as exc:
                    errors.append({"path": relative_path(child, root), "reason": (str(exc) or type(exc).__name__)[:300]})
                    continue
                retained_dirs.append(name)
            dirnames[:] = retained_dirs
            for name in sorted(filenames):
                if len(files) >= MAX_FILES:
                    errors.append({"path": relative_path(current / name, root), "reason": "native inventory file limit exceeded"})
                    truncated = True
                    break
                path = current / name
                try:
                    if link_like(path):
                        continue
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        errors.append({"path": relative_path(path, root), "reason": "file escapes workspace"})
                        continue
                    metadata = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(metadata.st_mode):
                        continue
                    if metadata.st_size < 0 or metadata.st_size > MAX_INVENTORY_BYTES - total_bytes:
                        errors.append({"path": relative_path(path, root), "reason": "native inventory byte limit exceeded"})
                        continue
                    hasher = hashlib.sha256()
                    size = 0
                    with path.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(65536), b""):
                            size += len(chunk)
                            if size > MAX_INVENTORY_BYTES - total_bytes:
                                raise ValueError("native inventory byte limit exceeded")
                            hasher.update(chunk)
                    total_bytes += size
                    files.append({"path": relative_path(path, root), "bytes": size, "sha256": hasher.hexdigest()})
                except (OSError, ValueError) as exc:
                    errors.append({"path": relative_path(path, root), "reason": (str(exc) or type(exc).__name__)[:300]})
            if truncated:
                break
        emit({"version": 1, "complete": not errors and not truncated, "files": files, "errors": errors, "truncated": truncated})
        return

    if action == "read":
        files = []
        errors = []
        total_bytes = 0
        if not isinstance(request, list) or len(request) > MAX_READ_FILES:
            emit({"version": 1, "complete": False, "files": [], "errors": [{"path": "", "reason": "invalid native file-read request"}]})
            return
        for raw in request:
            try:
                target = safe_target(raw, root)
                metadata = target.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("target is not a regular file")
                if metadata.st_size < 0 or metadata.st_size > MAX_READ_FILE_BYTES or metadata.st_size > MAX_READ_TOTAL_BYTES - total_bytes:
                    raise ValueError("native checkpoint read size limit exceeded")
                with target.open("rb") as stream:
                    content = stream.read(MAX_READ_FILE_BYTES + 1)
                if len(content) > MAX_READ_FILE_BYTES or len(content) > MAX_READ_TOTAL_BYTES - total_bytes:
                    raise ValueError("native checkpoint read size limit exceeded")
                total_bytes += len(content)
                files.append({"path": raw, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "content_base64": base64.b64encode(content).decode("ascii")})
            except Exception as exc:
                errors.append({"path": raw[:1000] if isinstance(raw, str) else "<invalid>", "reason": (str(exc) or type(exc).__name__)[:300]})
        emit({"version": 1, "complete": not errors, "files": files, "errors": errors})
        return

    emit({"version": 1, "complete": False, "files": [], "errors": [{"path": "", "reason": "unsupported native evidence action"}]})

main()
'''


def _validate_native_relative_path(workspace: Path, value: Any) -> str:
    """Reject malformed or escaping paths returned by a native sandbox process."""
    parts = _native_relative_path_parts(value)
    try:
        safe_path(workspace, "/".join(parts))
        current = Path(workspace).resolve()
        for part in parts:
            current = current / part
            if _is_link_or_reparse(current):
                raise PortError("Native workspace evidence cannot cross a symlink or reparse point")
    except (OSError, PortError, ValueError) as exc:
        raise PortError("Native workspace evidence returned an unsafe relative path") from exc
    return "/".join(parts)


def _validate_native_attested_path(workspace: Path, value: Any) -> str:
    """Validate a path returned by the fixed native walker without requiring host ACL access.

    The child script checks every component for symlinks, junctions, and reparse points
    before hashing or reading a file. This host-side pass still rejects malformed paths
    and any reparse point it can inspect; PermissionError is the one case where the
    already-bounded native attestation supplies the missing metadata check.
    """
    parts = _native_relative_path_parts(value)
    current = Path(workspace)
    for part in parts:
        current = current / part
        try:
            if _is_link_or_reparse(current):
                raise PortError("Native workspace evidence cannot cross a symlink or reparse point")
        except PermissionError:
            # Native inventory/read checked the full ancestor chain using the same
            # workspace-scoped sandbox, so denied host metadata is not treated as proof
            # that this already-attested path is unsafe.
            continue
        except OSError as exc:
            raise PortError("Native workspace evidence path metadata could not be verified") from exc
    return "/".join(parts)


def _native_relative_path_parts(value: Any) -> list[str]:
    """Apply filesystem-independent validation to a native relative path."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise PortError("Native workspace evidence returned an unsafe relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(part.casefold() == ".git" for part in parts):
        raise PortError("Native workspace evidence returned an unsafe relative path")
    reserved = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)), *("lpt" + str(i) for i in range(1, 10))}
    for part in parts:
        if (
            part.rstrip(" .") != part
            or part.split(".", 1)[0].casefold() in reserved
            or any(character in '<>"|?*' or ord(character) < 32 for character in part)
        ):
            raise PortError("Native workspace evidence returned an unsafe relative path")
        try:
            part.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise PortError("Native workspace evidence returned an unsafe relative path") from exc
    return parts


def _is_link_or_reparse(path: Path) -> bool:
    """Identify symlinks, junctions, and other Windows reparse points."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    metadata = path.lstat()
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(int(getattr(metadata, "st_file_attributes", 0)) & reparse_flag)


def _run_native_workspace_command(
    workspace: Path,
    executable: str,
    sandbox_mode: str,
    action: str,
    request: Any,
) -> tuple[bytes | None, str | None]:
    """Run one bounded, model-free Python command under the configured native sandbox."""
    if not _is_native_windows():
        return None, "Native workspace evidence is available only on native Windows."
    if not isinstance(executable, str) or not executable or sandbox_mode not in {"elevated", "unelevated"}:
        return None, "Native workspace evidence requires the resolved Codex executable and an existing sandbox mode."
    root = Path(workspace).resolve()
    try:
        safe_request = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return None, f"Native workspace evidence request could not be encoded: {exc}"
    if len(safe_request) > _MAX_NATIVE_WORKSPACE_INPUT_BYTES:
        return None, "Native workspace evidence request exceeded its input limit."
    command = [
        executable,
        "--config",
        f"windows.sandbox={json.dumps(sandbox_mode)}",
        "sandbox",
        "--include-managed-config",
        "--permission-profile",
        ":workspace",
        "--",
        str(Path(sys.executable).resolve()),
        "-I",
        "-S",
        "-c",
        _NATIVE_WORKSPACE_SCRIPT,
        str(root),
        action,
    ]
    output_limit = (
        _MAX_NATIVE_WORKSPACE_INVENTORY_OUTPUT_BYTES
        if action == "inventory"
        else _MAX_NATIVE_WORKSPACE_READ_OUTPUT_BYTES
    )
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    overflow = threading.Event()
    writer_error: list[str] = []
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(root),
            shell=False,
        )
        if process.stdin is None or process.stdout is None:
            return None, "Native sandbox command did not expose the expected pipes."

        def collect_stdout() -> None:
            assert process is not None and process.stdout is not None
            while len(output) <= output_limit:
                remaining = output_limit + 1 - len(output)
                chunk = process.stdout.read(min(64 * 1024, remaining))
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > output_limit:
                    overflow.set()
                    return

        def send_request() -> None:
            assert process is not None and process.stdin is not None
            try:
                process.stdin.write(safe_request)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                writer_error.append(type(exc).__name__)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        reader = threading.Thread(target=collect_stdout, name="hve-native-workspace-reader", daemon=True)
        writer = threading.Thread(target=send_request, name="hve-native-workspace-writer", daemon=True)
        reader.start()
        writer.start()
        deadline = time.monotonic() + _MAX_NATIVE_WORKSPACE_TIMEOUT_SECONDS
        timed_out = False
        while process.poll() is None:
            if overflow.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.01)
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return None, "Native sandbox evidence command did not stop within its bounded timeout."
        reader.join(timeout=2)
        writer.join(timeout=2)
        if timed_out:
            return None, "Native sandbox evidence command exceeded its 30-second timeout."
        if overflow.is_set() or len(output) > output_limit:
            return None, "Native sandbox evidence command exceeded its stdout limit."
        if reader.is_alive() or writer.is_alive():
            return None, "Native sandbox evidence command did not finish its bounded I/O."
        if process.returncode != 0:
            return None, f"Native sandbox evidence command exited with status {process.returncode}."
        if writer_error:
            return None, "Native sandbox evidence request could not be delivered to the child process."
        return bytes(output), None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        return None, f"Native sandbox evidence command failed: {type(exc).__name__}: {str(exc)[:300]}"
    finally:
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def _native_workspace_response(
    workspace: Path,
    executable: str,
    sandbox_mode: str,
    action: str,
    request: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    output, error = _run_native_workspace_command(workspace, executable, sandbox_mode, action, request)
    if output is None:
        return None, error
    try:
        parsed = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "Native sandbox evidence command did not return valid JSON."
    if not isinstance(parsed, dict) or parsed.get("version") != 1 or not isinstance(parsed.get("files"), list) or not isinstance(parsed.get("errors"), list):
        return None, "Native sandbox evidence response had an unsupported shape."
    return parsed, None


def _init_isolated_repo(workspace: Path, timeout_seconds: float = 15) -> dict[str, Any]:
    git = shutil.which("git")
    if not git:
        return {"status": "unavailable", "reason": "Git is required to mark the isolated temporary workspace as a project."}
    try:
        completed = subprocess.run(
            [git, "init", "--quiet"],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=max(0.1, min(timeout_seconds, 15)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "reason": str(exc)[:500]}
    return {"status": "initialized" if completed.returncode == 0 else "error", "returncode": completed.returncode}


def _install_scenario(distribution: Path, scenario_root: Path) -> dict[str, Any]:
    from .installer import install

    result = install(
        distribution=distribution,
        target=scenario_root,
        scope="project",
        mode="full",
        dry_run=False,
        no_plugin=True,
    )
    if result.get("conflicts") or result.get("status") not in {"installed", "updated"}:
        raise PortError(f"Full distribution installation did not complete cleanly: {result}")
    return result


def _pin_staged_model_settings(
    stage_root: Path,
    model: str,
    reasoning_effort: str,
    *,
    supports_agent_default_config: bool = True,
    agent_default_config_status: str | None = None,
) -> dict[str, Any]:
    """Pin model settings inside the disposable scenario, never the distribution."""
    import tomlkit

    config_path = stage_root / ".codex" / "config.toml"
    agents_path = stage_root / ".codex" / "agents"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.is_symlink() or (config_path.exists() and not config_path.is_file()):
        raise PortError("Staged .codex/config.toml is not a regular file")
    try:
        document = tomlkit.parse(config_path.read_text(encoding="utf-8") if config_path.is_file() else "")
    except (OSError, UnicodeDecodeError, Exception) as exc:
        raise PortError(f"Staged .codex/config.toml cannot be parsed safely: {exc}") from exc

    document["model"] = model
    document["model_reasoning_effort"] = reasoning_effort
    # A project default permits each custom role to retain its own policy.
    # A live --sandbox override would be reapplied to every delegated child.
    document["sandbox_mode"] = "workspace-write"
    agents_defaults = document.get("agents")
    if agents_defaults is None and supports_agent_default_config:
        agents_defaults = tomlkit.table()
        document["agents"] = agents_defaults
    if agents_defaults is not None and not hasattr(agents_defaults, "get"):
        raise PortError("Staged .codex/config.toml [agents] setting is not a table")
    if supports_agent_default_config:
        agents_defaults["default_subagent_model"] = model
        agents_defaults["default_subagent_reasoning_effort"] = reasoning_effort
    elif agents_defaults is not None:
        agents_defaults.pop("default_subagent_model", None)
        agents_defaults.pop("default_subagent_reasoning_effort", None)
        if len(agents_defaults) == 0:
            del document["agents"]
    config_path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")

    pinned_profiles: list[str] = []
    if agents_path.is_symlink() or (agents_path.exists() and not agents_path.is_dir()):
        raise PortError("Staged .codex/agents path is not a real directory")
    if agents_path.is_dir():
        for profile in sorted(agents_path.glob("*.toml"), key=lambda item: item.name.casefold()):
            if profile.is_symlink() or not profile.is_file():
                raise PortError(f"Staged custom agent profile is not a regular file: {profile.name}")
            try:
                profile_document = tomlkit.parse(profile.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, Exception) as exc:
                raise PortError(f"Staged custom agent profile cannot be parsed safely ({profile.name}): {exc}") from exc
            profile_document["model"] = model
            profile_document["model_reasoning_effort"] = reasoning_effort
            profile.write_text(tomlkit.dumps(profile_document), encoding="utf-8", newline="\n")
            pinned_profiles.append(profile.relative_to(stage_root).as_posix())
    return {
        "status": "pinned-in-disposable-stage",
        "scope": "scenario project only",
        "requested_model": model,
        "requested_reasoning_effort": reasoning_effort,
        "config_path": config_path.relative_to(stage_root).as_posix(),
        "custom_agent_profiles_pinned": pinned_profiles,
        "custom_agent_profile_count": len(pinned_profiles),
        "agent_default_config_status": agent_default_config_status or (
            "supported" if supports_agent_default_config else "omitted-unverified"
        ),
        "generic_subagent_model_status": (
            "requested-model-default-pinned" if supports_agent_default_config else "unverified"
        ),
    }


def _fixture_baseline(scenario: dict[str, Any], scenario_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative, _content in _fixture_entries(scenario):
        try:
            target = safe_path(scenario_root, relative)
        except PortError:
            continue
        if target.is_file():
            hashes[relative.replace("\\", "/")] = digest(target.read_bytes())
    return hashes


def _native_workspace_read_files(
    workspace: Path,
    relatives: list[str],
    executable: str,
    sandbox_mode: str,
) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    """Read a bounded set of validated workspace paths through the native sandbox."""
    if not relatives:
        return {}, []
    validated: list[str] = []
    errors: list[dict[str, str]] = []
    for relative in relatives:
        try:
            validated.append(_validate_native_attested_path(workspace, relative))
        except PortError as exc:
            errors.append({"path": str(relative)[:500], "reason": str(exc)})
    if len(validated) > _MAX_CHECKPOINT_FILES:
        return {}, errors + [{"path": ".", "reason": "Native checkpoint read exceeded the file-count limit."}]

    batches: list[list[str]] = []
    current: list[str] = []
    for relative in validated:
        candidate = [*current, relative]
        encoded = json.dumps(candidate, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(candidate) > _MAX_CHECKPOINT_FILES or len(encoded) > _MAX_NATIVE_WORKSPACE_INPUT_BYTES:
            if not current:
                errors.append({"path": relative, "reason": "Native checkpoint path exceeded the request limit."})
                continue
            batches.append(current)
            current = [relative]
        else:
            current = candidate
    if current:
        batches.append(current)

    requested = set(validated)
    returned: dict[str, bytes] = {}
    total_bytes = 0
    for batch in batches:
        response, response_error = _native_workspace_response(
            workspace,
            executable,
            sandbox_mode,
            "read",
            batch,
        )
        if response is None:
            errors.append({"path": ".", "reason": f"Native checkpoint read failed: {response_error or 'unknown error'}"})
            continue
        for item in response.get("errors", []):
            if isinstance(item, dict):
                raw_path = item.get("path")
                try:
                    relative_error = _validate_native_attested_path(workspace, raw_path)
                except PortError:
                    relative_error = "."
                errors.append({"path": relative_error, "reason": str(item.get("reason") or "Native checkpoint read failed.")[:500]})
            else:
                errors.append({"path": ".", "reason": "Native checkpoint read returned a malformed error record."})
        for item in response.get("files", []):
            if not isinstance(item, dict):
                errors.append({"path": ".", "reason": "Native checkpoint read returned a malformed file record."})
                continue
            try:
                relative = _validate_native_attested_path(workspace, item.get("path"))
            except PortError as exc:
                errors.append({"path": ".", "reason": str(exc)})
                continue
            if relative not in requested or relative in returned:
                errors.append({"path": relative, "reason": "Native checkpoint read returned an unexpected or duplicate path."})
                continue
            encoded_content = item.get("content_base64")
            size = item.get("bytes")
            file_hash = item.get("sha256")
            try:
                content = base64.b64decode(encoded_content, validate=True) if isinstance(encoded_content, str) else None
            except (ValueError, TypeError):
                content = None
            if (
                content is None
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size != len(content)
                or size > _MAX_CHECKPOINT_FILE_BYTES
                or total_bytes + size > _MAX_CHECKPOINT_TOTAL_BYTES
                or not isinstance(file_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", file_hash) is None
                or digest(content) != file_hash
            ):
                errors.append({"path": relative, "reason": "Native checkpoint read returned invalid or oversized file content."})
                continue
            total_bytes += size
            returned[relative] = content
        if response.get("complete") is not True and not response.get("errors"):
            errors.append({"path": ".", "reason": "Native checkpoint read did not complete."})
    return returned, errors


def _scenario_checks(
    scenario: dict[str, Any],
    scenario_root: Path,
    distribution_root: Path,
    baseline_hashes: dict[str, str],
    *,
    execute_postchecks: bool = True,
    pending_reason: str = "This deterministic file check runs after a Codex session; preparation did not execute prompts.",
    stage_preconditions: dict[int, dict[str, Any]] | None = None,
    workspace_evidence: dict[str, Any] | None = None,
    native_command: str | None = None,
    native_sandbox_mode: str | None = None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for position, check in enumerate(scenario["deterministic_checks"]):
        if not isinstance(check, dict):
            checks.append({
                "scenario_id": scenario["id"],
                "check_id": f"check-{position + 1}",
                "status": "unsupported",
                "reason": "Check entry is not an object.",
            })
            continue
        check_id = str(check.get("id") or check.get("kind"))
        if check.get("kind") in _STAGE_PRECONDITION_CHECKS:
            if stage_preconditions is not None:
                stage_result = stage_preconditions.get(position)
                if stage_result is not None:
                    checks.append(stage_result)
                else:
                    checks.append({
                        "scenario_id": scenario["id"],
                        "check_id": check_id,
                        "kind": check.get("kind"),
                        "status": "pending-runtime",
                        "reason": "The stage precondition was not captured before the Codex session.",
                    })
                continue
            if execute_postchecks:
                checks.append({
                    "scenario_id": scenario["id"],
                    "check_id": check_id,
                    "kind": check.get("kind"),
                    "status": "pending-runtime",
                    "reason": "Stage preconditions must be captured before the Codex session.",
                })
                continue
        if not execute_postchecks and check.get("kind") in _POST_SESSION_CHECKS:
            checks.append({
                "scenario_id": scenario["id"],
                "check_id": str(check.get("id") or check.get("kind")),
                "kind": check.get("kind"),
                "status": "pending-runtime",
                "reason": pending_reason,
            })
            continue
        checks.append(_check_result(
            scenario["id"],
            check,
            scenario_root,
            distribution_root,
            baseline_hashes,
            workspace_evidence=workspace_evidence,
            native_command=native_command,
            native_sandbox_mode=native_sandbox_mode,
        ))
    return checks


def _scenario_stage_preconditions(
    scenario: dict[str, Any],
    scenario_root: Path,
    distribution_root: Path,
    baseline_hashes: dict[str, str],
) -> dict[int, dict[str, Any]]:
    """Capture fixture checks against the staged workspace before a model turn."""
    results: dict[int, dict[str, Any]] = {}
    for position, check in enumerate(scenario["deterministic_checks"]):
        if not isinstance(check, dict) or check.get("kind") not in _STAGE_PRECONDITION_CHECKS:
            continue
        result = _check_result(
            scenario["id"],
            check,
            scenario_root,
            distribution_root,
            baseline_hashes,
        )
        results[position] = result
    return results


def _scenario_report(
    scenario: dict[str, Any],
    *,
    fixtures: list[dict[str, Any]],
    fixture_errors: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    runtime_status: str,
    install_result: dict[str, Any] | None,
    entrypoint_resolution: dict[str, Any] | None = None,
    distribution: Path | None = None,
    model_settings: dict[str, Any] | None = None,
    native_host_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolution = entrypoint_resolution or {"status": "not-declared", "resolved": [], "errors": []}
    sources = scenario.get("source_artifacts", [])
    if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources):
        sources = []
    return {
        "id": scenario["id"],
        "key": str(scenario.get("key", "")),
        "title": str(scenario.get("title", scenario["id"])),
        "invocation_mode": scenario.get("invocation_mode", "explicit"),
        "prompts_declared": len(scenario["prompts"]),
        "rubric_items_declared": len(scenario["rubric"]),
        "entrypoint_resolution": {
            "status": resolution.get("status"),
            "resolved": resolution.get("resolved", []),
            "errors": resolution.get("errors", []),
            "aliases_path": resolution.get("aliases_path"),
        },
        "source_artifact_evidence": _source_artifact_evidence(distribution, sources) if distribution else [],
        "rubric_review": [
            {"criterion": item, "status": "manual-review-pending"}
            for item in scenario["rubric"]
        ],
        "full_installer": install_result,
        "model_settings": model_settings,
        "native_host_preflight": native_host_preflight,
        "fixtures_staged": fixtures,
        "fixture_errors": fixture_errors,
        "deterministic_checks": checks,
        "turns": turns,
        "behavioral_status": runtime_status,
        "status": "fixture-error" if fixture_errors else runtime_status,
    }
