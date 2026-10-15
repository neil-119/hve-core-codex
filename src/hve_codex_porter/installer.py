"""Safe, ownership-aware installation of the HVE Core Codex distribution.

The installer deliberately treats the distribution as data. It never runs
files from the distribution and only writes beneath the selected project or,
for user scope, beneath the selected user home (including its Codex home).
"""

from __future__ import annotations

import hashlib
import base64
import difflib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .common import is_canonical_var_alias


_MANIFEST_VERSION = 1
_BEGIN = "<!-- BEGIN HVE CORE CODEX MANAGED SECTION -->"
_END = "<!-- END HVE CORE CODEX MANAGED SECTION -->"
_PLUGIN_KEY = "hve-core-codex@hve-core-codex-local"
_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class InstallerError(RuntimeError):
    """Raised when the target or distribution cannot be handled safely."""


def install(
    distribution: Path,
    target: Path,
    scope: str = "project",
    mode: str = "full",
    dry_run: bool = False,
    no_hooks: bool = False,
    no_plugin: bool = False,
    codex_home: Path | None = None,
    user_home: Path | None = None,
) -> dict[str, Any]:
    """Install a distribution, preserving user files and recording ownership."""
    return _apply_distribution(
        distribution, target, scope, mode, dry_run, no_hooks, no_plugin,
        codex_home, user_home, operation="install",
    )


def update(
    distribution: Path,
    target: Path,
    scope: str = "project",
    mode: str = "full",
    dry_run: bool = False,
    no_hooks: bool = False,
    no_plugin: bool = False,
    codex_home: Path | None = None,
    user_home: Path | None = None,
) -> dict[str, Any]:
    """Update managed files, merging disjoint edits in UTF-8 text files."""
    return _apply_distribution(
        distribution, target, scope, mode, dry_run, no_hooks, no_plugin,
        codex_home, user_home, operation="update",
    )


def uninstall(
    target: Path,
    scope: str = "project",
    dry_run: bool = False,
    codex_home: Path | None = None,
    user_home: Path | None = None,
) -> dict[str, Any]:
    """Remove files still owned by this installer; retain locally edited files."""
    root, meta_root, manifest_path, agents_file = _layout(target, scope, codex_home, user_home)
    if scope == "user" and not root.exists():
        return _result("not-installed", scope=scope, target=str(root), operations=[], conflicts=[])
    _validate_root(root, may_create=False)
    _assert_safe_path(root, manifest_path)
    previous = _read_manifest(manifest_path)
    if previous is None:
        return _result("not-installed", scope=scope, target=str(root), operations=[], conflicts=[])

    operations: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    actions: list[tuple[Path, bytes | None]] = []
    retained_files: dict[str, dict[str, Any]] = {}

    for rel, record in sorted(previous.get("managed_files", {}).items()):
        path = _target_path(root, rel)
        _assert_safe_path(root, path)
        if not path.exists():
            operations.append({"action": "already-absent", "path": rel})
            continue
        if not path.is_file() or path.is_symlink():
            conflicts.append({"path": rel, "reason": "managed path is no longer a regular file"})
            retained_files[rel] = record
            continue
        if _sha256(path.read_bytes()) != record.get("sha256"):
            conflicts.append({"path": rel, "reason": "file was modified after installation; preserved"})
            retained_files[rel] = record
            continue
        if record.get("locally_merged") is True:
            conflicts.append({"path": rel, "reason": "file contains preserved local edits from an update; preserved"})
            retained_files[rel] = record
            continue
        actions.append((path, None))
        operations.append({"action": "remove", "path": rel})

    retained_sections: list[dict[str, Any]] = []
    for section in previous.get("managed_sections", []):
        section_path = _target_path(root, section["path"])
        _assert_safe_path(root, section_path)
        if not section_path.exists():
            operations.append({"action": "section-absent", "path": section["path"]})
            continue
        current = section_path.read_bytes()
        block = section.get("block", "").encode("utf-8")
        updated, removed, safe = _remove_owned_section(current, block)
        if not safe:
            conflicts.append({"path": section["path"], "reason": "managed AGENTS.md section was modified or is ambiguous; preserved"})
            retained_sections.append(section)
            continue
        if removed:
            if not section.get("originally_existed", True) and not updated.strip():
                actions.append((section_path, None))
            else:
                actions.append((section_path, updated))
            operations.append({"action": "remove-managed-section", "path": section["path"]})

    retained_json_sections: list[dict[str, Any]] = []
    for section in previous.get("managed_json_sections", []):
        section_path = _target_path(root, section["path"])
        _assert_safe_path(root, section_path)
        if not section_path.exists():
            operations.append({"action": "json-section-absent", "path": section["path"]})
            continue
        updated, safe = _remove_json_hooks(section_path, section)
        if not safe:
            conflicts.append({"path": section["path"], "reason": "managed hook entries were modified or are ambiguous; preserved"})
            retained_json_sections.append(section)
            continue
        if updated is None:
            actions.append((section_path, None))
        else:
            actions.append((section_path, updated))
        operations.append({"action": "remove-managed-hook-entries", "path": section["path"]})

    retained_toml_sections: list[dict[str, Any]] = []
    for section in previous.get("managed_toml_sections", []):
        section_path = _target_path(root, section["path"])
        _assert_safe_path(root, section_path)
        if not section_path.exists():
            operations.append({"action": "toml-setting-absent", "path": section["path"]})
            continue
        updated, safe = _remove_plugin_toml(section_path, section)
        if not safe:
            conflicts.append({"path": section["path"], "reason": "managed plugin enable setting was modified; preserved"})
            retained_toml_sections.append(section)
            continue
        actions.append((section_path, updated))
        operations.append({"action": "remove-plugin-enable-setting", "path": section["path"]})

    retained_marketplace_sections: list[dict[str, Any]] = []
    for section in previous.get("managed_marketplace_sections", []):
        section_path = _target_path(root, section["path"])
        _assert_safe_path(root, section_path)
        if not section_path.exists():
            operations.append({"action": "marketplace-entry-absent", "path": section["path"]})
            continue
        updated, safe = _remove_marketplace_entry(section_path, section)
        if not safe:
            conflicts.append({"path": section["path"], "reason": "managed marketplace entry was modified; preserved"})
            retained_marketplace_sections.append(section)
            continue
        actions.append((section_path, updated))
        operations.append({"action": "remove-marketplace-entry", "path": section["path"]})

    if dry_run:
        return _result(
            "conflict" if conflicts else "dry-run", scope=scope, target=str(root),
            operations=operations, conflicts=conflicts,
            warnings=["Backups created by prior updates are retained for recovery."],
        )

    if conflicts:
        # Remove safely owned content, but keep an updated manifest for any
        # locally edited paths so a later uninstall can be retried.
        remaining = dict(previous)
        remaining["managed_files"] = retained_files
        remaining["managed_sections"] = retained_sections
        remaining["managed_json_sections"] = retained_json_sections
        remaining["managed_toml_sections"] = retained_toml_sections
        remaining["managed_marketplace_sections"] = retained_marketplace_sections
        if retained_files or retained_sections or retained_json_sections or retained_toml_sections or retained_marketplace_sections:
            actions.append((manifest_path, _json_bytes(remaining)))
        else:
            actions.append((manifest_path, None))
    else:
        actions.append((manifest_path, None))

    _commit_actions(root, meta_root, actions)
    _prune_empty_owned_dirs(root, previous.get("managed_files", {}).keys())
    return _result(
        "conflict" if conflicts else "uninstalled", scope=scope, target=str(root),
        operations=operations, conflicts=conflicts,
        warnings=["Backups created by prior updates are retained for recovery."],
    )


def doctor(
    target: Path,
    scope: str = "project",
    codex_home: Path | None = None,
    user_home: Path | None = None,
) -> dict[str, Any]:
    """Check the installed ownership manifest and report missing or edited files."""
    root, _, manifest_path, _ = _layout(target, scope, codex_home, user_home)
    if scope == "user" and not root.exists():
        return _result("not-installed", scope=scope, target=str(root), issues=[], components={})
    _validate_root(root, may_create=False)
    _assert_safe_path(root, manifest_path)
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        return _result("not-installed", scope=scope, target=str(root), issues=[], components={})

    issues: list[dict[str, str]] = []
    for rel, record in sorted(manifest.get("managed_files", {}).items()):
        path = _target_path(root, rel)
        _assert_safe_path(root, path)
        if not path.exists():
            issues.append({"path": rel, "kind": "missing"})
        elif not path.is_file() or path.is_symlink():
            issues.append({"path": rel, "kind": "unsafe-type"})
        elif _sha256(path.read_bytes()) != record.get("sha256"):
            issues.append({"path": rel, "kind": "modified"})
        elif str(record.get("source", "")).startswith(("codex-overlay/agents/", "codex-overlay/rules/")) or record.get("source") == "codex-overlay/AGENTS.hve-core.md":
            content = path.read_bytes()
            if b"@@HVE_PLUGIN_ROOT@@" in content or b"@@HVE_RULES_ROOT@@" in content:
                issues.append({"path": rel, "kind": "unresolved-reference-token"})
    for section in manifest.get("managed_sections", []):
        rel = section["path"]
        path = _target_path(root, rel)
        _assert_safe_path(root, path)
        block = section.get("block", "").encode("utf-8")
        if not path.exists():
            issues.append({"path": rel, "kind": "missing-managed-section"})
        elif not _has_exact_owned_section(path.read_bytes(), block):
            issues.append({"path": rel, "kind": "modified-managed-section"})
    for section in manifest.get("managed_json_sections", []):
        rel = section["path"]
        path = _target_path(root, rel)
        _assert_safe_path(root, path)
        if not path.exists():
            issues.append({"path": rel, "kind": "missing-managed-hook-config"})
        elif not _has_json_hooks(path, section):
            issues.append({"path": rel, "kind": "modified-managed-hook-config"})
    for section in manifest.get("managed_toml_sections", []):
        rel = section["path"]
        path = _target_path(root, rel)
        _assert_safe_path(root, path)
        if not path.exists():
            issues.append({"path": rel, "kind": "missing-plugin-enable-setting"})
        elif not _has_plugin_toml(path, section):
            issues.append({"path": rel, "kind": "modified-plugin-enable-setting"})
    for section in manifest.get("managed_marketplace_sections", []):
        rel = section["path"]
        path = _target_path(root, rel)
        _assert_safe_path(root, path)
        if not path.exists():
            issues.append({"path": rel, "kind": "missing-marketplace-entry"})
        elif not _has_marketplace_entry(path, section):
            issues.append({"path": rel, "kind": "modified-marketplace-entry"})

    warnings = list(manifest.get("warnings", []))
    if manifest.get("components", {}).get("hooks") and not manifest.get("components", {}).get("hooks_registered", False):
        warnings.append("Hook files are installed but are not registered in Codex config; review and register them explicitly.")
    return _result(
        "issues" if issues else "healthy", scope=scope, target=str(root),
        issues=issues, warnings=_unique(warnings), components=manifest.get("components", {}),
        source_version=manifest.get("source_version"),
    )


def _apply_distribution(
    distribution: Path,
    target: Path,
    scope: str,
    mode: str,
    dry_run: bool,
    no_hooks: bool,
    no_plugin: bool,
    codex_home: Path | None,
    user_home: Path | None,
    operation: str,
) -> dict[str, Any]:
    if mode != "full":
        raise ValueError("Only mode='full' is currently supported")
    root, meta_root, manifest_path, agents_file = _layout(target, scope, codex_home, user_home)
    codex_root = _codex_root(root, scope, codex_home)
    _validate_root(root, may_create=scope == "user")
    _assert_safe_path(root, codex_root)
    if codex_root.exists() and (codex_root.is_symlink() or not codex_root.is_dir()):
        raise InstallerError(f"Codex home must be a real directory: {codex_root}")
    source = Path(distribution).expanduser().absolute()
    if not source.exists() or not source.is_dir() or source.is_symlink():
        raise InstallerError(f"Distribution must be an existing non-symlink directory: {source}")
    source = source.resolve()
    plugin_root = source / "plugins" / "hve-core-codex"
    overlay_root = source / "codex-overlay"
    if not plugin_root.is_dir() or plugin_root.is_symlink() or not overlay_root.is_dir() or overlay_root.is_symlink():
        raise InstallerError("Distribution must contain plugins/hve-core-codex and codex-overlay")
    previous = _read_manifest(manifest_path)

    desired: dict[str, tuple[bytes, str]] = {}
    _collect_overlay(overlay_root, root, meta_root, desired, no_hooks, scope, codex_root)
    hook_source = overlay_root / "hooks" / "hooks.json"
    if not no_hooks and not hook_source.is_file():
        raise InstallerError("Hook installation requested, but codex-overlay/hooks/hooks.json is missing")
    if not no_hooks:
        runtime_source = overlay_root / "hooks" / "hve_rules.py"
        if not runtime_source.is_file():
            runtime_source = source / "src" / "hve_codex_porter" / "hook_runtime.py"
        if not runtime_source.is_file():
            raise InstallerError("Hook runtime is missing from codex-overlay/hooks and src/hve_codex_porter")
        rel_runtime = _relative(root, _hooks_dest(root, scope, codex_root) / "hve_rules.py")
        desired[rel_runtime] = (runtime_source.read_bytes(), runtime_source.relative_to(source).as_posix())
    agents_dir = overlay_root / "agents"
    if agents_dir.exists():
        for file in _source_files(agents_dir, overlay_root):
            if file.suffix.lower() != ".toml":
                continue
            rel_source = f"codex-overlay/{file.relative_to(overlay_root).as_posix()}"
            dest = _join_rel(_relative(root, _agents_dest(root, scope, codex_root)), file.relative_to(agents_dir).as_posix())
            desired[dest] = (file.read_bytes(), rel_source)

    components: dict[str, Any] = {"agents": True, "rules": True, "hooks": not no_hooks, "plugin": not no_plugin, "standalone_skills": no_plugin, "hooks_registered": not no_hooks}
    warnings: list[str] = []
    if no_hooks:
        warnings.append("Hooks were skipped; path-scoped rule injection and any hook-based guidance are unavailable.")
    else:
        warnings.append("Hooks are registered in the selected Codex hooks config; review the commands and approve hook trust in Codex.")

    plugin_meta = _load_plugin_meta(plugin_root)
    source_version = plugin_meta.get("version")
    plugin_reference_root = _plugin_dest(root, scope, codex_root) if not no_plugin else meta_root / "plugin-resources"
    rules_reference_root = _rules_dest(root, scope, codex_root)
    if no_plugin:
        _collect_standalone_skills(plugin_root, root, scope, desired, codex_root)
        _collect_plugin_resources(plugin_root, root, meta_root, desired)
    else:
        plugin_dest = _plugin_dest(root, scope, codex_root)
        for file in _source_files(plugin_root, source):
            rel_inside = file.relative_to(plugin_root).as_posix()
            dest = _join_rel(_relative(root, plugin_dest), rel_inside)
            desired[dest] = (file.read_bytes(), f"plugins/hve-core-codex/{rel_inside}")

    desired = {
        rel: (
            _materialize_reference_tokens(content, source_rel, plugin_reference_root, rules_reference_root),
            source_rel,
        )
        for rel, (content, source_rel) in desired.items()
    }

    portable_paths: dict[str, str] = {}
    for rel in desired:
        portable_key = unicodedata.normalize("NFC", rel).casefold()
        duplicate = portable_paths.get(portable_key)
        if duplicate is not None and duplicate != rel:
            raise InstallerError(f"Case-insensitive or Unicode path collision: {duplicate!r} and {rel!r}")
        portable_paths[portable_key] = rel

    agents_source = overlay_root / "AGENTS.hve-core.md"
    if agents_source.is_file():
        # The managed copy is useful for inspection; a bounded, marked section
        # in the host AGENTS.md makes the general guidance discoverable.
        agent_content = _materialize_reference_tokens(
            agents_source.read_bytes(), "codex-overlay/AGENTS.hve-core.md",
            plugin_reference_root, rules_reference_root,
        )
        desired[_relative(root, meta_root / "AGENTS.hve-core.md")] = (
            agent_content, "codex-overlay/AGENTS.hve-core.md"
        )
        agent_text = agent_content.decode("utf-8")
        block = _format_section(agent_text)
        section_dest = _relative(root, agents_file)
    else:
        block = ""
        section_dest = ""

    # Ensure that source mappings never silently overwrite the same path.
    old_files = previous.get("managed_files", {}) if previous else {}
    conflicts: list[dict[str, str]] = []
    operations: list[dict[str, str]] = []
    write_actions: dict[str, bytes] = {}
    delete_rels: set[str] = set()
    installed_contents: dict[str, bytes] = {}

    for rel, (content, source_rel) in sorted(desired.items()):
        dest = _target_path(root, rel)
        _assert_safe_path(root, dest)
        old_record = old_files.get(rel)
        installed_content = content
        if dest.exists():
            if not dest.is_file() or dest.is_symlink():
                conflicts.append({"path": rel, "reason": "destination exists and is not a regular file"})
                continue
            current = dest.read_bytes()
            current_hash = _sha256(current)
            if old_record is None:
                if current_hash != _sha256(content):
                    conflicts.append({"path": rel, "reason": "unowned destination already exists"})
                    continue
                # Adopting an identical file would make uninstall remove a
                # file the user may have placed there, so leave it unowned.
                conflicts.append({"path": rel, "reason": "identical unowned destination already exists"})
                continue
            baseline = _decode_baseline(old_record)
            if baseline is not None:
                agent_profile = _is_codex_agent_profile(rel)
                merged = (
                    _three_way_merge_agent_profile(baseline, current, content)
                    if agent_profile
                    else _three_way_merge(baseline, current, content)
                )
                if merged is None:
                    reason = (
                        "local and upstream profile edits overlap or the managed profile is invalid TOML or UTF-8 text"
                        if agent_profile
                        else "local and upstream edits overlap or the managed file is not UTF-8 text"
                    )
                    conflicts.append({"path": rel, "reason": reason})
                    continue
                installed_content = merged
            elif current_hash not in {old_record.get("sha256"), _sha256(content)}:
                conflicts.append({"path": rel, "reason": "managed file was locally modified and has no text merge baseline"})
                continue
        elif old_record is None and operation == "update" and previous:
            # A newly introduced artifact can be added when its path is free.
            pass
        installed_contents[rel] = installed_content
        if not dest.exists() or dest.read_bytes() != installed_content:
            write_actions[rel] = installed_content
            operations.append({"action": "write" if not dest.exists() else "update", "path": rel})

    for rel, old_record in sorted(old_files.items()):
        if rel in desired:
            continue
        dest = _target_path(root, rel)
        _assert_safe_path(root, dest)
        if not dest.exists():
            continue
        if dest.is_file() and not dest.is_symlink() and _sha256(dest.read_bytes()) == old_record.get("sha256"):
            delete_rels.add(rel)
            operations.append({"action": "remove-obsolete", "path": rel})
        else:
            conflicts.append({"path": rel, "reason": "obsolete managed file was modified; preserved"})

    new_section: dict[str, Any] | None = None
    if block:
        section_state, section_error = _plan_section(
            root, section_dest, block, previous.get("managed_sections", []) if previous else []
        )
        if section_error:
            conflicts.append({"path": section_dest, "reason": section_error})
        else:
            updated_bytes, originally_existed = section_state
            new_section = {"path": section_dest, "block": block, "originally_existed": originally_existed}
            section_path = _target_path(root, section_dest)
            if not section_path.exists() or section_path.read_bytes() != updated_bytes:
                write_actions[section_dest] = updated_bytes
                operations.append({"action": "write-managed-section", "path": section_dest})

    json_section: dict[str, Any] | None = None
    hooks_config_rel = _relative(root, _hooks_config_path(root, scope, codex_root))
    old_json_sections = previous.get("managed_json_sections", []) if previous else []
    old_hook_section = next((item for item in old_json_sections if item.get("path") == hooks_config_rel), None)
    if not no_hooks:
        try:
            hook_config = json.loads(hook_source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstallerError(f"Cannot read hook configuration {hook_source}: {exc}") from exc
        if not isinstance(hook_config, dict) or not isinstance(hook_config.get("hooks"), dict):
            raise InstallerError("Hook configuration must contain a hooks object")
        configured_entries = _configure_hook_entries(
            hook_config["hooks"], _hooks_dest(root, scope, codex_root) / "hve_rules.py"
        )
        hook_state, hook_error = _plan_json_hooks(
            root, hooks_config_rel, configured_entries, old_hook_section,
            _hooks_dest(root, scope, codex_root) / "hve_rules.py",
        )
        if hook_error:
            conflicts.append({"path": hooks_config_rel, "reason": hook_error})
        else:
            hook_bytes, json_section = hook_state
            hooks_path = _target_path(root, hooks_config_rel)
            if hook_bytes is not None and (not hooks_path.exists() or hooks_path.read_bytes() != hook_bytes):
                write_actions[hooks_config_rel] = hook_bytes
                operations.append({"action": "merge-hook-config", "path": hooks_config_rel})
    elif old_hook_section:
        hooks_path = _target_path(root, hooks_config_rel)
        if hooks_path.exists():
            hook_bytes, safe = _remove_json_hooks(hooks_path, old_hook_section)
            if not safe:
                conflicts.append({"path": hooks_config_rel, "reason": "previously managed hook entries were modified or are ambiguous"})
            elif hook_bytes is None:
                delete_rels.add(hooks_config_rel)
                operations.append({"action": "remove-hook-config", "path": hooks_config_rel})
            elif hook_bytes != hooks_path.read_bytes():
                write_actions[hooks_config_rel] = hook_bytes
                operations.append({"action": "remove-managed-hook-entries", "path": hooks_config_rel})

    config_rel = _relative(root, _codex_config_path(root, scope, codex_root))
    old_toml_sections = previous.get("managed_toml_sections", []) if previous else []
    old_plugin_section = next((item for item in old_toml_sections if item.get("path") == config_rel), None)
    toml_section: dict[str, Any] | None = None
    if not no_plugin:
        config_state, config_error = _plan_plugin_toml(root, config_rel, old_plugin_section)
        if config_error:
            conflicts.append({"path": config_rel, "reason": config_error})
        elif config_state:
            config_bytes, toml_section = config_state
            config_path = _target_path(root, config_rel)
            if config_bytes is not None and (not config_path.exists() or config_path.read_bytes() != config_bytes):
                write_actions[config_rel] = config_bytes
                operations.append({"action": "enable-plugin", "path": config_rel})
    elif old_plugin_section:
        config_path = _target_path(root, config_rel)
        if config_path.exists():
            config_bytes, safe = _remove_plugin_toml(config_path, old_plugin_section)
            if not safe:
                conflicts.append({"path": config_rel, "reason": "previously managed plugin enable setting was modified"})
            elif config_bytes is None:
                delete_rels.add(config_rel)
                operations.append({"action": "remove-plugin-config", "path": config_rel})
            elif config_bytes != config_path.read_bytes():
                write_actions[config_rel] = config_bytes
                operations.append({"action": "remove-plugin-enable-setting", "path": config_rel})

    marketplace_rel = _relative(root, _marketplace_path(root, scope))
    old_marketplaces = previous.get("managed_marketplace_sections", []) if previous else []
    old_marketplace = next((item for item in old_marketplaces if item.get("path") == marketplace_rel), None)
    marketplace_section: dict[str, Any] | None = None
    if not no_plugin:
        plugin_rel = _relative(root, _plugin_dest(root, scope, codex_root))
        marketplace_state, marketplace_error = _plan_marketplace(
            root, marketplace_rel, scope, old_marketplace, "./" + plugin_rel,
        )
        if marketplace_error:
            conflicts.append({"path": marketplace_rel, "reason": marketplace_error})
        elif marketplace_state:
            marketplace_bytes, marketplace_section = marketplace_state
            marketplace_path = _target_path(root, marketplace_rel)
            if marketplace_bytes is not None and (not marketplace_path.exists() or marketplace_path.read_bytes() != marketplace_bytes):
                write_actions[marketplace_rel] = marketplace_bytes
                operations.append({"action": "register-marketplace-plugin", "path": marketplace_rel})
    elif old_marketplace:
        marketplace_path = _target_path(root, marketplace_rel)
        if marketplace_path.exists():
            marketplace_bytes, safe = _remove_marketplace_entry(marketplace_path, old_marketplace)
            if not safe:
                conflicts.append({"path": marketplace_rel, "reason": "previously managed marketplace entry was modified"})
            elif marketplace_bytes is None:
                delete_rels.add(marketplace_rel)
                operations.append({"action": "remove-marketplace-config", "path": marketplace_rel})
            elif marketplace_bytes != marketplace_path.read_bytes():
                write_actions[marketplace_rel] = marketplace_bytes
                operations.append({"action": "remove-marketplace-entry", "path": marketplace_rel})

    if conflicts:
        return _result(
            "conflict", scope=scope, target=str(root), operations=[], conflicts=conflicts,
            warnings=warnings, source_version=source_version,
        )

    managed_records: dict[str, dict[str, Any]] = {}
    for rel, (content, source_rel) in sorted(desired.items()):
        installed = installed_contents.get(rel, content)
        record: dict[str, Any] = {
            "sha256": _sha256(installed),
            "source": source_rel,
            "locally_merged": installed != content,
        }
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            record["base64"] = base64.b64encode(content).decode("ascii")
        managed_records[rel] = record
    sections = [new_section] if new_section else []
    manifest = {
        "schema_version": _MANIFEST_VERSION,
        "installer": "hve-codex-porter",
        "scope": scope,
        "target": str(root),
        "source_version": source_version,
        "managed_files": managed_records,
        "managed_sections": sections,
        "managed_json_sections": [json_section] if json_section else [],
        "managed_toml_sections": [toml_section] if toml_section else [],
        "managed_marketplace_sections": [marketplace_section] if marketplace_section else [],
        "components": components,
        "warnings": _unique(warnings),
    }
    manifest_rel = _relative(root, manifest_path)
    new_manifest_bytes = _json_bytes(manifest)
    manifest_changed = not manifest_path.exists() or manifest_path.read_bytes() != new_manifest_bytes
    if manifest_changed:
        write_actions[manifest_rel] = new_manifest_bytes
        operations.append({"action": "write-ownership-manifest", "path": manifest_rel})
    actions: list[tuple[Path, bytes | None]] = [
        (_target_path(root, rel), data) for rel, data in write_actions.items()
    ]
    actions.extend((_target_path(root, rel), None) for rel in delete_rels)

    if dry_run:
        return _result(
            "dry-run", scope=scope, target=str(root), operations=sorted(operations, key=lambda op: (op["path"], op["action"])),
            conflicts=[], warnings=_unique(warnings), source_version=source_version,
        )

    if actions:
        _commit_actions(root, meta_root, actions)
    return _result(
        "installed" if operation == "install" else "updated", scope=scope, target=str(root),
        operations=sorted(operations, key=lambda op: (op["path"], op["action"])),
        conflicts=[], warnings=_unique(warnings), source_version=source_version,
        components=components,
    )


def _layout(
    target: Path,
    scope: str,
    codex_home: Path | None,
    user_home: Path | None,
) -> tuple[Path, Path, Path, Path]:
    if scope not in {"project", "user"}:
        raise ValueError("scope must be 'project' or 'user'")
    if scope == "project":
        root = Path(target).expanduser().absolute()
        codex_root = root / ".codex"
        agents_file = root / "AGENTS.md"
    else:
        root = Path(user_home).expanduser().absolute() if user_home is not None else Path.home().absolute()
        default_codex_home = root / ".codex"
        if codex_home is not None:
            codex_root = Path(codex_home).expanduser().absolute()
            default_home_codex = (Path.home() / ".codex").absolute()
            if user_home is None and codex_root != default_home_codex:
                raise InstallerError(
                    "A custom --codex-home requires an explicit --user-home so user-level "
                    "marketplace discovery and standalone skills have a known safe root."
                )
        else:
            codex_root = default_codex_home
        if not _is_relative_to(codex_root, root):
            raise InstallerError("For user scope, --codex-home must be located within --user-home.")
        agents_file = codex_root / "AGENTS.md"
    meta_root = codex_root / "hve-core-codex"
    manifest = meta_root / "ownership.json"
    return root, meta_root, manifest, agents_file


def _codex_root(root: Path, scope: str, codex_home: Path | None) -> Path:
    if scope == "project":
        return root / ".codex"
    return Path(codex_home).expanduser().absolute() if codex_home is not None else root / ".codex"


def _agents_dest(root: Path, scope: str, codex_root: Path | None = None) -> Path:
    codex = root / ".codex" if scope == "project" else codex_root or root / ".codex"
    return codex / "agents"


def _rules_dest(root: Path, scope: str, codex_root: Path | None = None) -> Path:
    codex = root / ".codex" if scope == "project" else codex_root or root / ".codex"
    return codex / "rules"


def _hooks_dest(root: Path, scope: str, codex_root: Path | None = None) -> Path:
    codex = root / ".codex" if scope == "project" else codex_root or root / ".codex"
    return codex / "hooks"


def _hooks_config_path(root: Path, scope: str, codex_root: Path | None = None) -> Path:
    codex = root / ".codex" if scope == "project" else codex_root or root / ".codex"
    return codex / "hooks.json"


def _codex_config_path(root: Path, scope: str, codex_root: Path | None = None) -> Path:
    codex = root / ".codex" if scope == "project" else codex_root or root / ".codex"
    return codex / "config.toml"


def _marketplace_path(root: Path, scope: str) -> Path:
    return root / ".agents" / "plugins" / "marketplace.json"


def _plugin_dest(root: Path, scope: str, codex_root: Path | None = None) -> Path:
    if scope == "project":
        return root / ".agents" / "plugins" / "hve-core-codex"
    return (codex_root or root / ".codex") / "plugins" / "hve-core-codex"


def _skills_dest(root: Path, scope: str) -> Path:
    return root / ".agents" / "skills"


def _collect_overlay(
    overlay: Path,
    root: Path,
    meta_root: Path,
    desired: dict[str, tuple[bytes, str]],
    no_hooks: bool,
    scope: str,
    codex_root: Path | None = None,
) -> None:
    for dirname in ("rules", "hooks"):
        if dirname == "hooks" and no_hooks:
            continue
        source_dir = overlay / dirname
        if not source_dir.exists():
            continue
        if source_dir.is_symlink():
            raise InstallerError(f"Distribution directory must not be a symlink: {source_dir}")
        target_dir = _rules_dest(root, scope, codex_root) if dirname == "rules" else _hooks_dest(root, scope, codex_root)
        for file in _source_files(source_dir, overlay):
            if dirname == "hooks" and file.name == "hooks.json":
                continue
            if dirname == "hooks" and file.name == "hve_rules.py":
                continue
            rel_source = file.relative_to(overlay).as_posix()
            rel_dest = _join_rel(_relative(root, target_dir), file.relative_to(source_dir).as_posix())
            desired[rel_dest] = (file.read_bytes(), f"codex-overlay/{rel_source}")


def _collect_standalone_skills(
    plugin_root: Path,
    root: Path,
    scope: str,
    desired: dict[str, tuple[bytes, str]],
    codex_root: Path | None = None,
) -> None:
    skills_dir = plugin_root / "skills"
    if not skills_dir.is_dir() or skills_dir.is_symlink():
        raise InstallerError("Plugin has no skills directory for --no-plugin fallback")
    for skill in sorted(skills_dir.iterdir(), key=lambda p: p.name.casefold()):
        if skill.is_symlink():
            raise InstallerError(f"Distribution contains a symlink, which is not installed: {skill}")
        if not skill.is_dir():
            continue
        for file in _source_files(skill, plugin_root):
            inside_skill = file.relative_to(skills_dir).as_posix()
            rel_dest = _join_rel(_relative(root, _skills_dest(root, scope)), inside_skill)
            desired[rel_dest] = (file.read_bytes(), f"plugins/hve-core-codex/{file.relative_to(plugin_root).as_posix()}")
    # Skill resources can point to plugin-level asset trees. Preserve those
    # paths under the .agents root so relative references keep their meaning.
    for item in sorted(plugin_root.iterdir(), key=lambda p: p.name.casefold()):
        if item.name in {"skills", "plugin.json"} or item.is_symlink():
            continue
        if item.is_dir():
            for file in _source_files(item, plugin_root):
                rel_dest = _join_rel(_relative(root, root / ".agents"), file.relative_to(plugin_root).as_posix())
                desired[rel_dest] = (file.read_bytes(), f"plugins/hve-core-codex/{file.relative_to(plugin_root).as_posix()}")


def _collect_plugin_resources(
    plugin_root: Path,
    root: Path,
    meta_root: Path,
    desired: dict[str, tuple[bytes, str]],
) -> None:
    """Copy non-skill plugin resources for agents and rules in no-plugin mode."""
    resource_root = meta_root / "plugin-resources"
    for item in sorted(plugin_root.iterdir(), key=lambda p: p.name.casefold()):
        if item.name.casefold() in {"skills", "plugin.json"}:
            continue
        if item.is_symlink():
            raise InstallerError(f"Distribution contains a symlink, which is not installed: {item}")
        if item.is_file():
            files = [item]
        elif item.is_dir():
            files = list(_source_files(item, plugin_root))
        else:
            continue
        for file in files:
            inside = file.relative_to(plugin_root).as_posix()
            rel_dest = _join_rel(_relative(root, resource_root), inside)
            desired[rel_dest] = (file.read_bytes(), f"plugins/hve-core-codex/{inside}")


def _materialize_reference_tokens(
    content: bytes,
    source: str,
    plugin_root: Path,
    rules_root: Path,
) -> bytes:
    if not source.startswith(("codex-overlay/agents/", "codex-overlay/rules/")) and source != "codex-overlay/AGENTS.hve-core.md":
        return content
    if b"@@HVE_PLUGIN_ROOT@@" not in content and b"@@HVE_RULES_ROOT@@" not in content:
        return content
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # Binary resources are passed through untouched. A literal marker in a
        # binary file is not treated as a path substitution request.
        return content
    text = text.replace("@@HVE_PLUGIN_ROOT@@", plugin_root.absolute().as_posix())
    text = text.replace("@@HVE_RULES_ROOT@@", rules_root.absolute().as_posix())
    return text.encode("utf-8")


def _source_files(directory: Path, source_root: Path) -> Iterable[Path]:
    base = source_root.resolve()
    for path in sorted(directory.rglob("*"), key=lambda p: p.as_posix().casefold()):
        if path.is_symlink():
            raise InstallerError(f"Distribution contains a symlink, which is not installed: {path}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not _is_relative_to(resolved, base):
            raise InstallerError(f"Distribution path escapes its root: {path}")
        rel = path.relative_to(source_root).as_posix()
        _validate_relative(rel)
        yield path


def _plan_section(
    root: Path,
    rel: str,
    new_block: str,
    old_sections: list[dict[str, Any]],
) -> tuple[tuple[bytes, bool] | None, str | None]:
    path = _target_path(root, rel)
    _assert_safe_path(root, path)
    previous = next((item for item in old_sections if item.get("path") == rel), None)
    existed = path.exists()
    if not existed:
        return ((new_block + "\n").encode("utf-8"), False), None
    if path.is_symlink() or not path.is_file():
        return None, "AGENTS.md destination is not a regular file"
    raw = path.read_bytes()
    if previous is None:
        if _section_count(raw) != 0:
            return None, "an HVE managed section exists without a matching ownership record"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, "AGENTS.md is not UTF-8; cannot safely merge the managed section"
        if text and not text.endswith("\n"):
            text += "\n"
        if text and not text.endswith("\n\n"):
            text += "\n"
        return ((text + new_block + "\n").encode("utf-8"), True), None
    old_block = previous.get("block", "").encode("utf-8")
    if not _has_exact_owned_section(raw, old_block):
        return None, "previously managed AGENTS.md section was modified or is ambiguous"
    updated, removed, safe = _remove_owned_section(raw, old_block)
    if not safe or not removed:
        return None, "previously managed AGENTS.md section was modified or is ambiguous"
    try:
        text = updated.decode("utf-8")
    except UnicodeDecodeError:
        return None, "AGENTS.md is not UTF-8; cannot safely update the managed section"
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    return ((text + new_block + "\n").encode("utf-8"), bool(previous.get("originally_existed", True))), None


def _format_section(body: str) -> str:
    clean = body.strip("\r\n")
    return f"{_BEGIN}\n{clean}\n{_END}"


def _remove_owned_section(raw: bytes, expected_block: bytes) -> tuple[bytes, bool, bool]:
    if _section_count(raw) != 1 or not _has_exact_owned_section(raw, expected_block):
        return raw, False, False
    try:
        text = raw.decode("utf-8")
        block = expected_block.decode("utf-8")
    except UnicodeDecodeError:
        return raw, False, False
    start = text.index(block)
    end = start + len(block)
    # Remove only the installer-owned block and its adjacent newline. Keep all
    # text that was present outside the exact marked region.
    if end < len(text) and text[end] == "\r":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    before = text[:start]
    after = text[end:]
    if before.endswith("\n\n") and after.startswith("\n"):
        before = before[:-1]
    result = before + after
    return result.encode("utf-8"), True, True


def _has_exact_owned_section(raw: bytes, expected_block: bytes) -> bool:
    if not expected_block or _section_count(raw) != 1:
        return False
    try:
        text = raw.decode("utf-8")
        block = expected_block.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return text.count(block) == 1


def _section_count(raw: bytes) -> int:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return -1
    begins = text.count(_BEGIN)
    ends = text.count(_END)
    if begins == 0 and ends == 0:
        return 0
    if begins == 1 and ends == 1:
        return 1
    return max(begins, ends, 2)


def _configure_hook_entries(hooks: dict[str, Any], script_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Replace the portable script token with this installation's interpreter."""
    script = script_path.absolute()
    if os.name == "nt":
        command_prefix = subprocess.list2cmdline([sys.executable, str(script)])
    else:
        command_prefix = f"{shlex.quote(str(Path(sys.executable).absolute()))} {shlex.quote(str(script))}"
    configured: dict[str, list[dict[str, Any]]] = {}
    for event, event_entries in hooks.items():
        if not isinstance(event, str) or not event:
            raise InstallerError("Hook event names must be non-empty strings")
        if not isinstance(event_entries, list):
            raise InstallerError(f"Hook event {event!r} must contain an array")
        entries: list[dict[str, Any]] = []
        for value in event_entries:
            if not isinstance(value, dict) or not isinstance(value.get("hooks"), list):
                raise InstallerError(f"Hook event {event!r} contains a malformed hook entry")
            entry = json.loads(json.dumps(value))
            for hook in entry["hooks"]:
                if not isinstance(hook, dict) or hook.get("type") != "command":
                    continue
                command = hook.get("command")
                if not isinstance(command, str) or "HVE_HOOK_SCRIPT" not in command:
                    raise InstallerError(
                        f"Hook event {event!r} must use the HVE_HOOK_SCRIPT command placeholder"
                    )
                match = re.match(r"^\s*python\s+HVE_HOOK_SCRIPT\b(.*)$", command, re.IGNORECASE)
                suffix = match.group(1) if match else command.replace("HVE_HOOK_SCRIPT", "", 1)
                hook["command"] = command_prefix + suffix
            entries.append(entry)
        configured[event] = entries
    return configured


def _plan_json_hooks(
    root: Path,
    rel: str,
    new_entries: dict[str, list[dict[str, Any]]],
    previous: dict[str, Any] | None,
    script_path: Path,
) -> tuple[tuple[bytes, dict[str, Any]] | None, str | None]:
    path = _target_path(root, rel)
    _assert_safe_path(root, path)
    existed = path.exists()
    if existed and (path.is_symlink() or not path.is_file()):
        return None, "Codex hooks config is not a regular file"
    original: dict[str, Any] = {}
    if existed:
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"Codex hooks config is invalid JSON: {exc}"
        if not isinstance(original, dict):
            return None, "Codex hooks config root must be a JSON object"

    base = json.loads(json.dumps(original))
    if previous:
        base_bytes, safe = _remove_json_hooks_from_value(base, previous)
        if not safe:
            return None, "previously managed hook entries were modified or are ambiguous"
        if base_bytes is None:
            base = {}

    hooks_value = base.get("hooks", {})
    if not isinstance(hooks_value, dict):
        return None, "Codex hooks config has a non-object 'hooks' value"
    hooks_value = json.loads(json.dumps(hooks_value))
    original_hook_events = dict(previous.get("original_events", {})) if previous else {}
    for event in new_entries:
        original_hook_events.setdefault(event, event in hooks_value)
    for event, entries in new_entries.items():
        existing_entries = hooks_value.get(event, [])
        if not isinstance(existing_entries, list):
            return None, f"Codex hooks config event {event!r} is not an array"
        for entry in entries:
            if entry in existing_entries:
                return None, f"an identical unowned hook entry already exists for {event}"
            existing_entries.append(entry)
        hooks_value[event] = existing_entries
    base["hooks"] = hooks_value
    section = {
        "path": rel,
        "entries": new_entries,
        "originally_existed": previous.get("originally_existed", existed) if previous else existed,
        "original_hooks_key": previous.get("original_hooks_key", "hooks" in original) if previous else "hooks" in original,
        "original_events": original_hook_events,
        "script_path": str(script_path.absolute()),
    }
    encoded = _json_bytes(base)
    return (encoded, section), None


def _remove_json_hooks(path: Path, section: dict[str, Any]) -> tuple[bytes | None, bool]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, False
    if not isinstance(value, dict):
        return None, False
    updated, safe = _remove_json_hooks_from_value(value, section)
    if not safe:
        return None, False
    if updated is None:
        return None, True
    return _json_bytes(updated), True


def _remove_json_hooks_from_value(value: dict[str, Any], section: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    current = json.loads(json.dumps(value))
    hooks = current.get("hooks", {})
    if not isinstance(hooks, dict):
        return None, False
    entries_by_event = section.get("entries", {})
    if not isinstance(entries_by_event, dict):
        return None, False
    for event, managed_entries in entries_by_event.items():
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            return None, False
        if not isinstance(managed_entries, list):
            return None, False
        for managed in managed_entries:
            count = entries.count(managed)
            if count > 1:
                return None, False
            if count == 1:
                entries.remove(managed)
            elif _contains_modified_owned_hook(entries, managed, section.get("script_path", "")):
                return None, False
        if not entries and not section.get("original_events", {}).get(event, False):
            hooks.pop(event, None)
        else:
            hooks[event] = entries
    if not hooks and not section.get("original_hooks_key", False):
        current.pop("hooks", None)
    else:
        current["hooks"] = hooks
    if not current and not section.get("originally_existed", True):
        return None, True
    return current, True


def _contains_modified_owned_hook(entries: list[Any], expected: dict[str, Any], script_path: str) -> bool:
    if not script_path:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and script_path in str(hook.get("command", "")):
                return True
    return False


def _has_json_hooks(path: Path, section: dict[str, Any]) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or not isinstance(value.get("hooks"), dict):
        return False
    for event, entries in section.get("entries", {}).items():
        current = value["hooks"].get(event, [])
        if not isinstance(current, list) or any(current.count(entry) != 1 for entry in entries):
            return False
    return True


def _plan_plugin_toml(
    root: Path,
    rel: str,
    previous: dict[str, Any] | None,
) -> tuple[tuple[bytes | None, dict[str, Any] | None] | None, str | None]:
    try:
        import tomlkit
    except ImportError as exc:  # pragma: no cover - project runtime pins tomlkit
        raise InstallerError("tomlkit is required to safely merge Codex plugin settings") from exc
    path = _target_path(root, rel)
    _assert_safe_path(root, path)
    existed = path.exists()
    if existed and (path.is_symlink() or not path.is_file()):
        return None, "Codex config.toml is not a regular file"
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8") if existed else "")
    except (OSError, UnicodeDecodeError, Exception) as exc:
        return None, f"Codex config.toml cannot be parsed safely: {exc}"

    plugins = document.get("plugins")
    had_plugins = "plugins" in document
    if plugins is not None and not hasattr(plugins, "get"):
        return None, "Codex config.toml 'plugins' value is not a table"
    if plugins is None:
        plugins = tomlkit.table()
    entry = plugins.get(_PLUGIN_KEY)
    if entry is not None:
        if not hasattr(entry, "get"):
            return None, f"Codex config has a non-table setting for {_PLUGIN_KEY}"
        enabled = entry.get("enabled")
        if enabled is not True:
            if previous:
                return None, f"managed plugin setting {_PLUGIN_KEY} was changed or disabled"
            return None, f"plugin {_PLUGIN_KEY} already has a disabled or non-enabled config; preserved"
        if previous:
            return (None, previous), None
        # The user already enabled this plugin. Do not claim ownership.
        return (None, None), None

    if previous:
        return None, f"previously managed plugin setting {_PLUGIN_KEY} is missing"

    entry = tomlkit.table()
    entry["enabled"] = True
    plugins[_PLUGIN_KEY] = entry
    document["plugins"] = plugins
    section = {
        "path": rel,
        "key": _PLUGIN_KEY,
        "originally_existed": existed,
        "original_plugins_key": had_plugins,
    }
    return (tomlkit.dumps(document).encode("utf-8"), section), None


def _remove_plugin_toml(path: Path, section: dict[str, Any]) -> tuple[bytes | None, bool]:
    try:
        import tomlkit
    except ImportError:  # pragma: no cover - project runtime pins tomlkit
        return None, False
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return None, False
    plugins = document.get("plugins")
    if plugins is None:
        return tomlkit.dumps(document).encode("utf-8"), True
    if not hasattr(plugins, "get"):
        return None, False
    key = section.get("key", _PLUGIN_KEY)
    entry = plugins.get(key)
    if entry is None:
        return tomlkit.dumps(document).encode("utf-8"), True
    if not hasattr(entry, "get") or entry.get("enabled") is not True:
        return None, False
    del entry["enabled"]
    if len(entry) == 0:
        del plugins[key]
    if len(plugins) == 0 and not section.get("original_plugins_key", False):
        del document["plugins"]
    if len(document) == 0 and not section.get("originally_existed", True):
        return None, True
    return tomlkit.dumps(document).encode("utf-8"), True


def _has_plugin_toml(path: Path, section: dict[str, Any]) -> bool:
    try:
        import tomlkit
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    plugins = document.get("plugins")
    if not hasattr(plugins, "get"):
        return False
    entry = plugins.get(section.get("key", _PLUGIN_KEY))
    return hasattr(entry, "get") and entry.get("enabled") is True


def _plan_marketplace(
    root: Path,
    rel: str,
    scope: str,
    previous: dict[str, Any] | None,
    plugin_source_path: str | None = None,
) -> tuple[tuple[bytes | None, dict[str, Any] | None] | None, str | None]:
    path = _target_path(root, rel)
    _assert_safe_path(root, path)
    existed = path.exists()
    if existed and (path.is_symlink() or not path.is_file()):
        return None, "marketplace.json is not a regular file"
    if existed:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"marketplace.json is invalid JSON: {exc}"
        if not isinstance(value, dict):
            return None, "marketplace.json root must be an object"
    else:
        value = {
            "name": "hve-core-codex-local",
            "interface": {"displayName": "HVE Core for Codex"},
            "plugins": [],
        }

    if value.get("name") != "hve-core-codex-local":
        return None, "marketplace.json uses a different marketplace name; preserved"
    plugins = value.get("plugins")
    if not isinstance(plugins, list):
        return None, "marketplace.json must contain a plugins array"
    path_value = plugin_source_path or "./.agents/plugins/hve-core-codex"
    expected = {
        "name": "hve-core-codex",
        "source": {"source": "local", "path": path_value},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    old_entry = previous.get("entry") if previous else None
    if previous:
        current_matches = [i for i, entry in enumerate(plugins) if entry == old_entry]
        if len(current_matches) != 1:
            same_name = any(isinstance(entry, dict) and entry.get("name") == "hve-core-codex" for entry in plugins)
            return None, "previously managed marketplace entry was removed or modified" if same_name or not current_matches else "marketplace entry is ambiguous"
        plugins.pop(current_matches[0])

    named_entries = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == "hve-core-codex"]
    if named_entries:
        if len(named_entries) == 1 and named_entries[0] == expected:
            if previous:
                # Re-add an owned entry so its identity remains recorded.
                plugins.append(expected)
                section = {"path": rel, "entry": expected, "originally_existed": previous.get("originally_existed", existed)}
                return (_json_bytes(value), section), None
            # An identical user entry is already registered; do not claim it.
            return (None, None), None
        return None, "marketplace already contains a different hve-core-codex entry; preserved"

    plugins.append(expected)
    value["plugins"] = plugins
    section = {"path": rel, "entry": expected, "originally_existed": previous.get("originally_existed", existed) if previous else existed}
    return (_json_bytes(value), section), None


def _remove_marketplace_entry(path: Path, section: dict[str, Any]) -> tuple[bytes | None, bool]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, False
    if not isinstance(value, dict) or not isinstance(value.get("plugins"), list):
        return None, False
    plugins = value["plugins"]
    expected = section.get("entry")
    matches = [i for i, entry in enumerate(plugins) if entry == expected]
    if len(matches) > 1:
        return None, False
    if not matches:
        if any(isinstance(entry, dict) and entry.get("name") == "hve-core-codex" for entry in plugins):
            return None, False
        return _json_bytes(value), True
    plugins.pop(matches[0])
    value["plugins"] = plugins
    if not section.get("originally_existed", True) and not plugins:
        default_interface = {"displayName": "HVE Core for Codex"}
        if value.get("name") == "hve-core-codex-local" and value.get("interface") == default_interface and set(value) <= {"name", "interface", "plugins"}:
            return None, True
    return _json_bytes(value), True


def _has_marketplace_entry(path: Path, section: dict[str, Any]) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or not isinstance(value.get("plugins"), list):
        return False
    return value["plugins"].count(section.get("entry")) == 1


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise InstallerError(f"Ownership manifest path is unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallerError(f"Cannot read ownership manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != _MANIFEST_VERSION:
        raise InstallerError(f"Unsupported or malformed ownership manifest: {path}")
    if not isinstance(value.get("managed_files"), dict) or not isinstance(value.get("managed_sections", []), list):
        raise InstallerError(f"Malformed ownership manifest: {path}")
    return value


def _commit_actions(root: Path, meta_root: Path, actions: list[tuple[Path, bytes | None]]) -> None:
    root_resolved = root.resolve()
    prepared: list[tuple[Path, bytes | None, bytes | None, int | None]] = []
    for path, data in actions:
        _assert_safe_path(root, path)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise InstallerError(f"Refusing to replace a non-regular file: {path}")
        old_data = path.read_bytes() if path.exists() else None
        old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        prepared.append((path, data, old_data, old_mode))

    changed = [(p, d, old, mode) for p, d, old, mode in prepared if old != d]
    if not changed:
        return
    while True:
        backup_dir = _new_backup_dir(meta_root)
        try:
            backup_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            continue
    if os.name != "nt":
        os.chmod(backup_dir, 0o700)
    for path, _, old_data, _ in changed:
        if old_data is None:
            continue
        rel = path.relative_to(root_resolved).as_posix()
        backup = backup_dir.joinpath(*PurePosixPath(rel).parts)
        _assert_safe_path(root, backup)
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            parent = backup.parent
            while _is_relative_to(parent, backup_dir):
                os.chmod(parent, 0o700)
                if parent == backup_dir:
                    break
                parent = parent.parent
        shutil.copy2(path, backup)
        if os.name != "nt":
            os.chmod(backup, 0o600)

    completed: list[tuple[Path, bytes | None, int | None]] = []
    created_dirs: list[Path] = []
    try:
        for path, data, old_data, old_mode in changed:
            _assert_safe_path(root, path)
            missing: list[Path] = []
            parent = path.parent
            while not parent.exists() and _is_relative_to(parent.absolute(), root.absolute()):
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, data, old_mode)
            completed.append((path, old_data, old_mode))
    except Exception:
        for path, old_data, old_mode in reversed(completed):
            try:
                if old_data is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, old_data, old_mode)
            except OSError:
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _new_backup_dir(meta_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    candidate = meta_root / "backups" / stamp
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = meta_root / "backups" / f"{stamp}-{suffix}"
    return candidate


def _atomic_write(path: Path, data: bytes, mode: int | None) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        _replace_with_retry(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _replace_with_retry(source: str, destination: Path) -> None:
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in {32, 33} or attempt == 5:
                raise
            time.sleep(0.05)


def _validate_root(root: Path, may_create: bool) -> None:
    _reject_reparse_components(root)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise InstallerError(f"Installation root must be a real directory: {root}")
    if not root.exists() and not may_create:
        # Missing project roots are usually a caller mistake. User homes may be
        # created by install, but doctor/uninstall remain read-only.
        raise InstallerError(f"Installation root does not exist: {root}")


def _assert_safe_path(root: Path, path: Path) -> None:
    root_abs = root.absolute()
    dest_abs = path.absolute()
    if not _is_relative_to(dest_abs, root_abs):
        raise InstallerError(f"Refusing path outside installation scope: {path}")
    _reject_reparse_components(dest_abs, stop=root_abs)
    # If an existing parent is redirected, the lexical containment check is
    # insufficient. Ensure the real destination remains below the real root.
    root_resolved = root.resolve(strict=False)
    dest_resolved = dest_abs.resolve(strict=False)
    if not _is_relative_to(dest_resolved, root_resolved):
        raise InstallerError(f"Refusing path that resolves outside installation scope: {path}")


def _reject_reparse_components(path: Path, stop: Path | None = None) -> None:
    absolute = path.absolute()
    parts = list(absolute.parents)[::-1] + [absolute]
    stop_abs = stop.absolute() if stop is not None else None
    for component in parts:
        if stop_abs is not None and component == stop_abs:
            break
        if not component.exists() and not component.is_symlink():
            continue
        try:
            mode = component.lstat().st_mode
            attrs = getattr(component.lstat(), "st_file_attributes", 0)
        except OSError:
            continue
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(mode) or attrs & reparse:
            if component != absolute and is_canonical_var_alias(component):
                continue
            raise InstallerError(f"Refusing path with a symlink or reparse-point component: {component}")


def _target_path(root: Path, rel: str) -> Path:
    _validate_relative(rel)
    return root.joinpath(*PurePosixPath(rel).parts)


def _relative(root: Path, path: Path) -> str:
    try:
        result = path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise InstallerError(f"Installation path escapes scope: {path}") from exc
    _validate_relative(result)
    return result


def _join_rel(base: str, child: str) -> str:
    _validate_relative(base)
    _validate_relative(child)
    joined = (PurePosixPath(base) / PurePosixPath(child)).as_posix()
    _validate_relative(joined)
    return joined


def _validate_relative(value: str) -> None:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or any(part in {"", ".", ".."} for part in p.parts):
        raise InstallerError(f"Unsafe relative path: {value!r}")
    if re.match(r"^[A-Za-z]:", value) or "\\" in value or "\x00" in value:
        raise InstallerError(f"Unsafe or non-portable path: {value!r}")
    for part in p.parts:
        if part.endswith((".", " ")) or ":" in part or part.split(".", 1)[0].upper() in _DEVICE_NAMES:
            raise InstallerError(f"Unsafe Windows path component: {part!r}")


def _prune_empty_owned_dirs(root: Path, rels: Iterable[str]) -> None:
    dirs: set[Path] = set()
    for rel in rels:
        path = _target_path(root, rel).parent
        while path != root and _is_relative_to(path.absolute(), root.absolute()):
            dirs.add(path)
            path = path.parent
    for path in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            _assert_safe_path(root, path)
            path.rmdir()
        except (OSError, InstallerError):
            continue


def _load_plugin_meta(plugin_root: Path) -> dict[str, Any]:
    manifest = plugin_root / "plugin.json"
    if manifest.is_symlink():
        raise InstallerError(f"Distribution manifest must not be a symlink: {manifest}")
    if not manifest.is_file():
        return {}
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_baseline(record: dict[str, Any]) -> bytes | None:
    encoded = record.get("base64")
    if not isinstance(encoded, str):
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error):
        return None


def _three_way_merge(base: bytes, local: bytes, upstream: bytes) -> bytes | None:
    """Merge non-overlapping line edits; return None for conflicts or binary data."""
    try:
        base_lines = base.decode("utf-8").splitlines(keepends=True)
        local_lines = local.decode("utf-8").splitlines(keepends=True)
        upstream_lines = upstream.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None

    def edits(changed: list[str]) -> list[tuple[int, int, list[str]]]:
        matcher = difflib.SequenceMatcher(a=base_lines, b=changed, autojunk=False)
        return [
            (i1, i2, changed[j1:j2])
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
            if tag != "equal"
        ]

    local_edits = edits(local_lines)
    upstream_edits = edits(upstream_lines)
    combined = list(local_edits)
    for upstream_edit in upstream_edits:
        duplicate = False
        for local_edit in local_edits:
            if upstream_edit == local_edit:
                duplicate = True
                break
            a_start, a_end, _ = local_edit
            b_start, b_end, _ = upstream_edit
            if a_start == a_end and b_start == b_end:
                overlaps = a_start == b_start
            elif a_start == a_end:
                overlaps = b_start <= a_start <= b_end
            elif b_start == b_end:
                overlaps = a_start <= b_start <= a_end
            else:
                overlaps = max(a_start, b_start) < min(a_end, b_end)
            if overlaps:
                return None
        if not duplicate:
            combined.append(upstream_edit)

    combined.sort(key=lambda item: (item[0], item[1]))
    result: list[str] = []
    cursor = 0
    for start, end, replacement in combined:
        if start < cursor:
            return None
        result.extend(base_lines[cursor:start])
        result.extend(replacement)
        cursor = end
    result.extend(base_lines[cursor:])
    return "".join(result).encode("utf-8")


def _is_codex_agent_profile(rel: str) -> bool:
    """Match only direct project agent profiles, leaving all other files on text merge."""
    path = PurePosixPath(rel)
    return (
        len(path.parts) == 3
        and path.parts[0].casefold() == ".codex"
        and path.parts[1].casefold() == "agents"
        and path.suffix.casefold() == ".toml"
    )


def _three_way_merge_agent_profile(base: bytes, local: bytes, upstream: bytes) -> bytes | None:
    """Merge a managed Codex agent profile by TOML key, failing closed on conflicts."""
    import copy

    try:
        import tomlkit

        base_doc = tomlkit.parse(base.decode("utf-8"))
        local_doc = tomlkit.parse(local.decode("utf-8"))
        upstream_doc = tomlkit.parse(upstream.decode("utf-8"))
    except (ImportError, UnicodeDecodeError, ValueError):
        return None

    merged_doc = copy.deepcopy(local_doc)
    missing = object()

    def semantic(value: Any) -> Any:
        if value is missing:
            return missing
        unwrap = getattr(value, "unwrap", None)
        return unwrap() if callable(unwrap) else value

    def semantically_equal(left: Any, right: Any) -> bool:
        left = semantic(left)
        right = semantic(right)
        if type(left) is not type(right):
            return False
        if isinstance(left, Mapping):
            return left.keys() == right.keys() and all(
                semantically_equal(left[key], right[key]) for key in left
            )
        if isinstance(left, (list, tuple)):
            return len(left) == len(right) and all(
                semantically_equal(left_item, right_item)
                for left_item, right_item in zip(left, right)
            )
        return left == right

    def same(left_present: bool, left: Any, right_present: bool, right: Any) -> bool:
        if left_present != right_present:
            return False
        if not left_present:
            return True
        return semantically_equal(left, right)

    def is_table(value: Any) -> bool:
        return isinstance(value, Mapping)

    def merge_table(
        base_table: Mapping[str, Any],
        local_table: Mapping[str, Any],
        upstream_table: Mapping[str, Any],
        output_table: Any,
    ) -> bool:
        keys = dict.fromkeys((*base_table.keys(), *local_table.keys(), *upstream_table.keys()))
        for key in keys:
            base_present = key in base_table
            local_present = key in local_table
            upstream_present = key in upstream_table
            base_value = base_table[key] if base_present else missing
            local_value = local_table[key] if local_present else missing
            upstream_value = upstream_table[key] if upstream_present else missing

            # Tables can merge recursively, including two independently added
            # tables. A table changed into or out of a scalar remains a conflict.
            if (
                local_present
                and upstream_present
                and is_table(local_value)
                and is_table(upstream_value)
                and (not base_present or is_table(base_value))
            ):
                output_value = output_table[key]
                if not is_table(output_value):
                    return False
                if not merge_table(
                    base_value if base_present else {},
                    local_value,
                    upstream_value,
                    output_value,
                ):
                    return False
                continue

            if same(local_present, local_value, base_present, base_value):
                selected_present, selected_value = upstream_present, upstream_value
            elif same(upstream_present, upstream_value, base_present, base_value):
                selected_present, selected_value = local_present, local_value
            elif same(local_present, local_value, upstream_present, upstream_value):
                selected_present, selected_value = local_present, local_value
            else:
                return False

            if selected_present:
                if not (local_present and same(True, local_value, True, selected_value)):
                    output_table[key] = copy.deepcopy(selected_value)
            elif key in output_table:
                del output_table[key]
        return True

    if not merge_table(base_doc, local_doc, upstream_doc, merged_doc):
        return None
    try:
        return tomlkit.dumps(merged_doc).encode("utf-8")
    except (TypeError, ValueError):
        return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _result(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}
