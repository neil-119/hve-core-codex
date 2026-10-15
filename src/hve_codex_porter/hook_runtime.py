"""Advisory Codex hook adapter for the HVE scoped-rule registry.

This module can run as a package module or as a copied standalone script. It
only reads the registry and writes a small deduplication file in the local
Codex data directory. Optional hash-only event receipts are disabled unless
``HVE_CODEX_HOOK_AUDIT=1`` and are stored separately with bounded history. A
receipt records that this hook handled an input event; it does not prove that
Codex delivered the returned context to a model. The module never runs a
command from the registry and never grants or denies a permission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


MAX_CONTEXT_BYTES = 8 * 1024
MAX_STDIN_BYTES = 1024 * 1024
_MAX_STATE_BYTES = 64 * 1024
_MAX_SCOPES = 64
_MAX_HASHES_PER_SCOPE = 256
_HOOK_AUDIT_ENV = "HVE_CODEX_HOOK_AUDIT"
_HOOK_RECEIPTS_FILENAME = "hook-event-receipts.json"
_MAX_HOOK_RECEIPTS = 256
_MAX_HOOK_RECEIPT_BYTES = _MAX_STATE_BYTES
_MAX_HOOK_RULE_DIGESTS = 128
_SUPPORTED_HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "SubagentStart", "PreToolUse"}
_HOOK_INVOCATION_FIELDS = {
    "SessionStart": None,
    "UserPromptSubmit": "turn_id",
    "SubagentStart": "agent_id",
    "PreToolUse": "tool_use_id",
}
_HOOK_RECEIPT_OUTCOMES = {"context_emitted", "no_match", "dedup"}

try:  # Package import, including ``python -m hve_codex_porter.hook_runtime``.
    from .rules import load_registry as _load_registry
    from .rules import match_rules as _match_rules
except ImportError:  # Direct execution beside rules.py.
    try:
        from rules import load_registry as _load_registry  # type: ignore[no-redef]
        from rules import match_rules as _match_rules  # type: ignore[no-redef]
    except ImportError:  # A copied, single-file script: use the small fallback below.
        _load_registry = None
        _match_rules = None


_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:[A-Za-z]:[\\/]|[\\/]{1,2})?"
    r"(?:\.{1,2}[\\/])?"
    r"[A-Za-z0-9_@+.-][A-Za-z0-9_@+.~-]*"
    r"(?:[\\/][A-Za-z0-9_@+.-][A-Za-z0-9_@+.~-]*)*"
    r"(?:\.[A-Za-z0-9_-]+)?"
    r"(?![A-Za-z0-9_])"
)
_PATCH_FILE_RE = re.compile(r"^\*\*\*\s+(?:Update|Add|Delete) File:\s*(.+?)\s*$", re.IGNORECASE)
_PATCH_MOVE_RE = re.compile(r"^\*\*\*\s+(?:Move|Copy) to:\s*(.+?)\s*$", re.IGNORECASE)
_PATCH_RENAME_COPY_RE = re.compile(r"^(?:rename|copy)\s+(?:from|to)\s+(.+?)\s*$", re.IGNORECASE)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _repo_root_for_registry(root: Path) -> Path:
    resolved = Path(root).expanduser().resolve()
    if resolved.name.casefold() == "rules" and (resolved / "index.json").is_file():
        return resolved.parent
    if resolved.name.casefold() == "codex-overlay" and (resolved / "rules" / "index.json").is_file():
        return resolved.parent
    if resolved.name.casefold() == ".codex" and (resolved / "rules" / "index.json").is_file():
        return resolved.parent
    return resolved


def _working_repository_root(cwd: Path) -> Path:
    """Find a useful workspace root when the registry is user-scoped."""
    candidates = (cwd, *cwd.parents)
    for candidate in candidates:
        if any((candidate / marker).exists() for marker in (".git", ".hg", ".svn")):
            return candidate
    project_markers = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "AGENTS.md")
    for candidate in candidates:
        if any((candidate / marker).is_file() for marker in project_markers):
            return candidate
    return cwd


def _event_repo_root(registry_root: Path, cwd: str | Path | None) -> Path:
    if not cwd:
        return _repo_root_for_registry(registry_root)
    try:
        working_directory = Path(cwd).expanduser().resolve()
        resolved_registry = Path(registry_root).expanduser().resolve()
    except (OSError, RuntimeError):
        return _repo_root_for_registry(registry_root)
    inferred_root = _repo_root_for_registry(resolved_registry)
    user_codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    try:
        user_codex_home = user_codex_home.resolve()
        registry_is_user_scoped = resolved_registry == user_codex_home or user_codex_home in resolved_registry.parents
    except (OSError, RuntimeError):
        registry_is_user_scoped = False
    if registry_is_user_scoped:
        return _working_repository_root(working_directory)
    try:
        working_directory.relative_to(inferred_root)
        return inferred_root
    except ValueError:
        # User-scoped registries can live outside the working repository.
        return _working_repository_root(working_directory)


def find_registry_root(cwd: str | Path | None = None, script_path: str | Path | None = None) -> Path | None:
    """Find a registry near the working directory or copied hook script."""
    starts: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME")
    for value in (cwd, script_path, codex_home, Path.home()):
        if value is None:
            continue
        try:
            path = Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        starts.append(path if path.is_dir() else path.parent)
    seen: set[Path] = set()
    for start in starts:
        for parent in (start, *start.parents):
            if parent in seen:
                continue
            seen.add(parent)
            candidates = (
                parent,  # repository root with rules/index.json
                parent / "rules",  # registry directory itself
                parent / "codex-overlay",  # repository root with generated overlay
                parent / "codex-overlay" / "rules",
                parent / ".codex" / "rules",
                parent / ".codex" / "hve-core" / "rules",
            )
            for candidate in candidates:
                if (candidate / "index.json").is_file():
                    return candidate.parent if candidate.name.casefold() == "rules" else candidate
            if cwd is not None and start == starts[0] and any(
                (parent / marker).exists() for marker in (".git", ".hg", ".svn")
            ):
                # A different repository above this one must not mask the
                # shared registry reached through the installed hook/home.
                break
    return None


def _normalize_target(value: str, repo_root: Path) -> str | None:
    """Return a safe repo-relative target, or None for an outside/unsafe path."""
    candidate = value.strip().strip("\"'").strip()
    if not candidate or "\x00" in candidate or "://" in candidate:
        return None
    candidate = candidate.replace("\\", "/")
    windows = PureWindowsPath(candidate)
    if Path(candidate).is_absolute() or windows.is_absolute() or windows.drive:
        try:
            absolute = Path(value.strip().strip("\"'")).expanduser().resolve(strict=False)
            resolved_root = Path(repo_root).expanduser().resolve(strict=False)
            try:
                relative = absolute.relative_to(resolved_root)
            except ValueError:
                # Some filesystems expose the same directory through multiple
                # names (for example, Windows 8.3 paths). Compare existing
                # ancestors by file identity while keeping the path resolved
                # so symlinks that escape the repository remain rejected.
                for ancestor in (absolute, *absolute.parents):
                    try:
                        if os.path.samefile(ancestor, resolved_root):
                            relative = absolute.relative_to(ancestor)
                            break
                    except OSError:
                        continue
                else:
                    return None
        except (OSError, RuntimeError, ValueError):
            return None
        candidate = relative.as_posix()
    elif windows.root:
        # A rooted Windows path without a drive does not identify a location
        # we can safely relate to the repository.
        return None
    candidate = candidate.removeprefix("./")
    parts = [part for part in candidate.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _looks_like_path(value: str) -> bool:
    value = value.strip().strip("\"'")
    if not value or "://" in value:
        return False
    return "/" in value or "\\" in value or bool(re.search(r"\.[A-Za-z0-9_-]{1,12}$", value))


def _unique_paths(values: Iterable[str], repo_root: Path) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not _looks_like_path(value):
            continue
        normalized = _normalize_target(value, repo_root)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def extract_prompt_paths(prompt: str, repo_root: Path) -> list[str]:
    """Extract conservative path hints from a submitted prompt."""
    if not isinstance(prompt, str) or not prompt:
        return []
    values: list[str] = []
    # First inspect inline-code spans, which often contain exact target paths.
    for span in re.findall(r"`([^`\n]+)`", prompt):
        for token in span.split():
            token = token.strip("\"'()[]{}<>,;:")
            if _looks_like_path(token):
                values.append(token)
    for match in _PATH_TOKEN_RE.finditer(prompt):
        token = match.group(0).strip("\"'()[]{}<>,;:")
        prefix = prompt[max(0, match.start() - 8) : match.start()]
        if "://" in prefix or not _looks_like_path(token):
            continue
        values.append(token)
    return _unique_paths(values, repo_root)


def extract_apply_patch_paths(command: str, repo_root: Path) -> list[str]:
    """Extract patch file targets without attempting to apply the patch."""
    if not isinstance(command, str):
        return []
    values: list[str] = []
    for line in command.splitlines():
        stripped = line.strip()
        for regex in (_PATCH_FILE_RE, _PATCH_MOVE_RE, _PATCH_RENAME_COPY_RE):
            found = regex.match(stripped)
            if found:
                values.append(found.group(1).strip().strip("\"'"))
                break
        if stripped.startswith(("+++ ", "--- ")):
            token = stripped[4:].split("\t", 1)[0].strip()
            if token != "/dev/null":
                if token.startswith(("a/", "b/")):
                    token = token[2:]
                values.append(token)
    return _unique_paths(values, repo_root)


def _is_path_field(key: str) -> bool:
    return (
        key in {
            "path", "paths", "file", "files", "filename", "filenames",
            "target", "targets", "filepath", "filepaths",
        }
        or key.endswith(("_path", "_paths", "_file", "_files"))
    )


def _nested_path_values(value: Any, key: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_name = str(child_key).casefold()
            yield from _nested_path_values(child, child_name)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _nested_path_values(child, key)
    elif isinstance(value, str) and _is_path_field(key):
        yield value


def extract_shell_paths(command: str, repo_root: Path) -> list[str]:
    """Find path-like shell tokens; this is deliberately best-effort only."""
    if not isinstance(command, str):
        return []
    values: list[str] = []
    for match in _PATH_TOKEN_RE.finditer(command):
        prefix = command[: match.start()].rstrip().rstrip("\"'")
        # Do not treat an expanded variable or home-directory expression as a
        # literal repo-relative path. The caller separately marks writes as
        # uncertain so callers can still inspect the complete shell target.
        if (
            prefix.endswith(("$", "~", "${"))
            or prefix.casefold().endswith("$env:")
            or re.search(r"\$(?:env:)?[A-Za-z_][A-Za-z0-9_]*[\\/]*$", prefix, re.IGNORECASE)
        ):
            continue
        values.append(match.group(0).strip("\"'()[]{}<>,;:"))
    return _unique_paths(values, repo_root)


def extract_tool_paths(tool_name: str, tool_input: Any, repo_root: Path) -> tuple[list[str], bool]:
    """Return target hints and whether a shell write target may be concealed."""
    tool_name = tool_name or ""
    if tool_name == "apply_patch":
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else tool_input
        return extract_apply_patch_paths(command, repo_root), False
    if tool_name.casefold() in {"bash", "shell", "powershell", "cmd", "exec_command"}:
        command = tool_input
        if isinstance(tool_input, dict):
            command = tool_input.get("command")
            if not isinstance(command, str) or not command.strip():
                command = tool_input.get("cmd", "")
        paths = extract_shell_paths(command, repo_root)
        write_like = bool(
            re.search(
                r"(?:>>?|\b(?:touch|rm|mv|cp|tee|install|apply_patch|Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|git\s+apply|sed\s+-i)\b)",
                str(command),
                re.IGNORECASE,
            )
        )
        return paths, write_like
    return _unique_paths(_nested_path_values(tool_input), repo_root), False


def _fallback_load_registry(root: Path) -> list[dict[str, Any]]:
    """Minimal stdlib registry reader used only when this file was copied alone."""
    base = Path(root).expanduser()
    rules_dir = base / "rules" if (base / "rules" / "index.json").is_file() else base
    resolved_base = base.resolve()
    resolved_rules = rules_dir.resolve()
    if rules_dir != base:
        try:
            resolved_rules.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError("Rules directory escapes the supplied root") from exc
    index_path = rules_dir / "index.json"
    try:
        index_path.resolve().relative_to(resolved_rules)
    except ValueError as exc:
        raise ValueError("Rules index escapes the rules directory") from exc
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if (
        not isinstance(index, dict)
        or isinstance(index.get("version"), bool)
        or index.get("version") != 1
        or not isinstance(index.get("rules"), list)
    ):
        raise ValueError("Invalid HVE rules registry")
    loaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in index["rules"]:
        if not isinstance(record, dict) or not all(
            key in record for key in ("id", "source", "path", "apply_to", "precedence", "imports")
        ):
            raise ValueError("Invalid HVE rule record")
        rule_id = record["id"]
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("Invalid HVE rule record fields")
        rule_id = rule_id.strip()
        if rule_id in seen:
            raise ValueError(f"Duplicate rule id: {rule_id}")
        seen.add(rule_id)
        if not isinstance(record["source"], str) or not record["source"].strip():
            raise ValueError("Invalid HVE rule record fields")
        if not isinstance(record["apply_to"], list) or any(
            not isinstance(pattern, str) or not pattern.strip() for pattern in record["apply_to"]
        ):
            raise ValueError("Invalid HVE rule record fields")
        if isinstance(record["precedence"], bool) or not isinstance(record["precedence"], int):
            raise ValueError("Invalid HVE rule record fields")
        if "auto_attach" in record and not isinstance(record["auto_attach"], bool):
            raise ValueError("Invalid HVE rule record fields")
        if not isinstance(record["imports"], list) or any(
            not isinstance(dependency, str) or not dependency.strip() for dependency in record["imports"]
        ):
            raise ValueError("Invalid HVE rule record fields")
        relative = _fallback_safe_relative(record["path"])
        parts = relative.split("/")
        content_path = (rules_dir / Path(*parts)).resolve()
        try:
            content_path.relative_to(resolved_rules)
        except ValueError as exc:
            raise ValueError("Rule content escapes the rules directory") from exc
        if not content_path.is_file():
            raise FileNotFoundError(f"Rule content file does not exist: {content_path}")
        item = dict(record)
        item.update({"id": rule_id, "path": relative, "content": content_path.read_text(encoding="utf-8")})
        loaded.append(item)
    aliases: dict[str, str] = {}
    for item in loaded:
        for alias in (item["id"], item["source"], item["path"]):
            if alias in aliases and aliases[alias] != item["id"]:
                raise ValueError("Ambiguous HVE rule import reference")
            aliases[alias] = item["id"]
    for item in loaded:
        if any(dependency not in aliases for dependency in item.get("imports", [])):
            raise ValueError("Unknown HVE rule import reference")
    return loaded


def _fallback_safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Rule path must be a non-empty relative path")
    raw = value.strip()
    windows = PureWindowsPath(raw)
    if windows.is_absolute() or windows.drive or raw.startswith(("/", "\\")):
        raise ValueError("Unsafe absolute path in rule path")
    parts = raw.replace("\\", "/").split("/")
    if any(part in ("..", "") for part in parts):
        raise ValueError("Unsafe path component in rule path")
    parts = [part for part in parts if part != "."]
    if not parts:
        raise ValueError("Rule path must name a file")
    return "/".join(parts)


def _fallback_expand_braces(pattern: str) -> list[str]:
    opening = pattern.find("{")
    if opening < 0:
        return [pattern]
    depth = 0
    closing = -1
    for position in range(opening, len(pattern)):
        if pattern[position] == "{":
            depth += 1
        elif pattern[position] == "}":
            depth -= 1
            if depth == 0:
                closing = position
                break
    if closing < 0:
        return [pattern]
    body = pattern[opening + 1 : closing]
    options: list[str] = []
    depth = 0
    start = 0
    for position, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            options.append(body[start:position])
            start = position + 1
    options.append(body[start:])
    if len(options) == 1:
        return [pattern]
    result: list[str] = []
    for option in options:
        result.extend(_fallback_expand_braces(pattern[:opening] + option + pattern[closing + 1 :]))
    return result


def _fallback_glob_regex(pattern: str) -> re.Pattern[str]:
    output = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if (
                index + 1 < len(pattern) and pattern[index + 1] == "*"
                and (index == 0 or pattern[index - 1] == "/")
                and (index + 2 == len(pattern) or pattern[index + 2] == "/")
            ):
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    output.append("(?:.*/)?")
                    index += 1
                    continue
                output.append(".*")
                continue
            output.append("[^/]*")
        elif char == "?":
            output.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end < 0:
                output.append(r"\[")
            else:
                body = pattern[index + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                elif body.startswith("^"):
                    body = "\\" + body
                output.append("[" + body.replace("\\", r"\\") + "]")
                index = end
        else:
            output.append(re.escape(char))
        index += 1
    output.append("$")
    return re.compile("".join(output))


def _fallback_rule_matches(rule: dict[str, Any], path: str) -> bool:
    if rule.get("auto_attach", True) is False:
        return False
    patterns: list[str] = []
    for value in rule.get("apply_to", []):
        depth = 0
        start = 0
        for index, char in enumerate(value):
            if char == "{":
                depth += 1
            elif char == "}" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                if value[start:index].strip():
                    patterns.append(value[start:index].strip())
                start = index + 1
        if value[start:].strip():
            patterns.append(value[start:].strip())
    positive = any(not pattern.startswith("!") for pattern in patterns)
    included = not positive
    for pattern in patterns:
        excluded = pattern.startswith("!")
        glob = (pattern[1:] if excluded else pattern).replace("\\", "/").removeprefix("./")
        if any(_fallback_glob_regex(item).fullmatch(path) for item in _fallback_expand_braces(glob)):
            included = not excluded
    return included


def _fallback_match_rules(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    # Keep the copied-script path dependency-free while retaining the same
    # recursive, multi-pattern, negation, and brace behavior as rules.py.
    rules = _fallback_load_registry(root)
    normalized = []
    for path in paths:
        value = path.strip().replace("\\", "/")
        windows = PureWindowsPath(value)
        if windows.is_absolute() or windows.drive or value.startswith("/"):
            raise ValueError("Unsafe repo-relative HVE path")
        parts = [part for part in value.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("Unsafe repo-relative HVE path")
        normalized.append("/".join(parts))
    by_id = {item["id"]: item for item in rules}
    aliases = {
        alias: item["id"]
        for item in rules
        for alias in (item["id"], item.get("source", ""), item["path"])
        if alias
    }
    selected: set[str] = set()
    for rule in rules:
        if any(_fallback_rule_matches(rule, path) for path in normalized):
            selected.add(rule["id"])
    stack = list(selected)
    while stack:
        for dependency in by_id[stack.pop()].get("imports", []):
            dependency_id = aliases[dependency]
            if dependency_id not in selected:
                selected.add(dependency_id)
                stack.append(dependency_id)
    return sorted((by_id[key] for key in selected), key=lambda item: (item.get("precedence", 0), item["id"]))


def _state_file() -> Path:
    override = os.environ.get("HVE_CODEX_HOOK_STATE_DIR")
    if override:
        directory = Path(override).expanduser()
    else:
        plugin_data = os.environ.get("PLUGIN_DATA")
        codex_home = os.environ.get("CODEX_HOME")
        if plugin_data:
            directory = Path(plugin_data).expanduser() / "hve-core"
        else:
            codex_directory = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
            directory = codex_directory / "hve-core"
    return directory / "rule-hook-state.json"


def _hook_receipts_file() -> Path:
    """Keep opt-in event receipts beside, but separate from, deduplication state."""
    return _state_file().with_name(_HOOK_RECEIPTS_FILENAME)


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _sanitize_hook_receipt(value: Any) -> dict[str, Any] | None:
    """Retain only the bounded, non-content fields allowed in an audit receipt."""
    if not isinstance(value, dict):
        return None
    event = value.get("event_name")
    outcome = value.get("outcome")
    timestamp = value.get("timestamp_utc")
    if (
        not isinstance(event, str)
        or event not in _SUPPORTED_HOOK_EVENTS
        or not isinstance(outcome, str)
        or outcome not in _HOOK_RECEIPT_OUTCOMES
    ):
        return None
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        return None
    try:
        datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    session_digest = value.get("session_id_sha256")
    invocation_digest = value.get("invocation_id_sha256")
    context_digest = value.get("context_sha256")
    script_digest = value.get("script_sha256")
    if session_digest is not None and not _valid_sha256(session_digest):
        return None
    if invocation_digest is not None and not _valid_sha256(invocation_digest):
        return None
    if context_digest is not None and not _valid_sha256(context_digest):
        return None
    if script_digest is not None and not _valid_sha256(script_digest):
        return None
    raw_rule_digests = value.get("rule_content_sha256", [])
    if not isinstance(raw_rule_digests, list):
        return None
    rule_digests: list[str] = []
    for digest in raw_rule_digests[:_MAX_HOOK_RULE_DIGESTS]:
        if not _valid_sha256(digest):
            return None
        if digest not in rule_digests:
            rule_digests.append(digest)
    receipt = {
        "event_name": event,
        "timestamp_utc": timestamp,
        "session_id_sha256": session_digest,
        "invocation_id_sha256": invocation_digest,
        "outcome": outcome,
        "rule_content_sha256": rule_digests,
    }
    if context_digest is not None:
        receipt["context_sha256"] = context_digest
    if script_digest is not None:
        receipt["script_sha256"] = script_digest
    return receipt


def _read_hook_receipts(path: Path) -> list[dict[str, Any]]:
    """Read only a bounded regular JSON file; malformed history starts fresh."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_HOOK_RECEIPT_BYTES:
            return []
        with path.open("rb") as stream:
            raw = stream.read(_MAX_HOOK_RECEIPT_BYTES + 1)
        if len(raw) > _MAX_HOOK_RECEIPT_BYTES:
            return []
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or isinstance(payload.get("version"), bool)
            or not isinstance(payload.get("receipts"), list)
        ):
            return []
        receipts = [receipt for value in payload["receipts"] if (receipt := _sanitize_hook_receipt(value)) is not None]
        return receipts[-_MAX_HOOK_RECEIPTS:]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return []


def _write_hook_receipts(path: Path, receipts: list[dict[str, Any]]) -> None:
    """Atomically replace the bounded receipt file in its existing state directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    bounded = receipts[-_MAX_HOOK_RECEIPTS:]
    encoded = json.dumps({"version": 1, "receipts": bounded}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    while bounded and len(encoded) > _MAX_HOOK_RECEIPT_BYTES:
        bounded = bounded[1:]
        encoded = json.dumps({"version": 1, "receipts": bounded}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_HOOK_RECEIPT_BYTES:
        raise OSError("hook receipt exceeds the configured state size limit")
    fd, temporary = tempfile.mkstemp(prefix="hve-hook-audit-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _script_file_sha256(script_path: str | Path | None) -> str | None:
    try:
        path = Path(script_path) if script_path is not None else Path(__file__)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _record_hook_receipt(
    payload: dict[str, Any],
    event: str,
    outcome: str,
    *,
    rules: Iterable[dict[str, Any]] = (),
    context: str | None = None,
    script_path: str | Path | None = None,
) -> None:
    """Best-effort opt-in receipt; failures never affect returned hook context."""
    if os.environ.get(_HOOK_AUDIT_ENV) != "1":
        return
    try:
        if event not in _SUPPORTED_HOOK_EVENTS or outcome not in _HOOK_RECEIPT_OUTCOMES:
            return
        session_id = payload.get("session_id")
        invocation_field = _HOOK_INVOCATION_FIELDS.get(event)
        invocation_id = payload.get(invocation_field) if invocation_field else None
        rule_digests: list[str] = []
        for rule in rules:
            content = rule.get("content") if isinstance(rule, dict) else None
            if isinstance(content, str):
                digest = _sha256(content)
                if digest not in rule_digests:
                    rule_digests.append(digest)
                if len(rule_digests) >= _MAX_HOOK_RULE_DIGESTS:
                    break
        receipt: dict[str, Any] = {
            "event_name": event,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id_sha256": _sha256(session_id) if isinstance(session_id, str) and session_id else None,
            "invocation_id_sha256": _sha256(invocation_id) if isinstance(invocation_id, str) and invocation_id else None,
            "outcome": outcome,
            "rule_content_sha256": rule_digests,
        }
        if context is not None:
            receipt["context_sha256"] = _sha256(context)
        script_digest = _script_file_sha256(script_path)
        if script_digest:
            receipt["script_sha256"] = script_digest
        path = _hook_receipts_file()
        receipts = _read_hook_receipts(path)
        receipts.append(receipt)
        _write_hook_receipts(path, receipts)
    except Exception:
        # Audit is advisory only. Registry context must continue to work when
        # the local receipt directory is unavailable or its disk is full.
        return


def _read_state(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_STATE_BYTES:
            return {"version": 1, "scopes": {}}
        state = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(state, dict)
            and not isinstance(state.get("version"), bool)
            and state.get("version") == 1
            and isinstance(state.get("scopes"), dict)
        ):
            scopes: dict[str, Any] = {}
            for key, value in state["scopes"].items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                updated = value.get("updated", 0)
                hashes = value.get("hashes", [])
                scopes[key] = {
                    "updated": updated if isinstance(updated, int) and not isinstance(updated, bool) else 0,
                    "hashes": [digest for digest in hashes if isinstance(digest, str)]
                    if isinstance(hashes, list)
                    else [],
                }
            return {"version": 1, "scopes": scopes}
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {"version": 1, "scopes": {}}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    scopes = state.get("scopes", {})
    if len(scopes) > _MAX_SCOPES:
        sorted_scopes = sorted(scopes.items(), key=lambda pair: pair[1].get("updated", 0), reverse=True)
        state["scopes"] = dict(sorted_scopes[:_MAX_SCOPES])
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    encoded = payload.encode("utf-8")
    if len(encoded) > _MAX_STATE_BYTES:
        state["scopes"] = {}
        encoded = json.dumps(state, separators=(",", ":")).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix="hve-rules-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _scope_key(payload: dict[str, Any]) -> str | None:
    """Identify one hook invocation without sharing guidance across events.

    SessionStart has no stable per-invocation id and runs again after compaction,
    so it deliberately bypasses persistent deduplication. The other supported
    events expose unique ids in their documented input shapes.
    """
    session = str(payload.get("session_id") or "session-without-id")
    event = str(payload.get("hook_event_name") or "")
    if event == "SessionStart":
        return None
    identity_field = {
        "SubagentStart": "agent_id",
        "UserPromptSubmit": "turn_id",
        "PreToolUse": "tool_use_id",
    }.get(event)
    identity = payload.get(identity_field) if identity_field else None
    if not isinstance(identity, str) or not identity:
        return None
    return _sha256(session + "\0" + event + "\0" + identity)


def _bounded_context(items: list[tuple[str, str, str]], prefix: str, used_hashes: set[str]) -> tuple[str, list[str]]:
    output = prefix
    added: list[str] = []
    if len(output.encode("utf-8")) > MAX_CONTEXT_BYTES:
        output = _truncate_utf8(output, MAX_CONTEXT_BYTES)
        return output, added
    for rule_id, content, digest in items:
        if digest in used_hashes:
            continue
        body = content.strip()
        section = f"\n\n[{rule_id}]\n{body}" if body else f"\n\n[{rule_id}]"
        current_size = len(output.encode("utf-8"))
        available = MAX_CONTEXT_BYTES - current_size
        if available <= 0:
            break
        if len(section.encode("utf-8")) > available:
            section = _truncate_utf8(section, available)
        output += section
        added.append(digest)
        if len(output.encode("utf-8")) >= MAX_CONTEXT_BYTES:
            break
    return output, added


def _truncate_utf8(value: str, max_bytes: int) -> str:
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _rule_items(rules: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [
        (str(rule.get("id", "rule")), str(rule.get("content", "")), _sha256(str(rule.get("id", "")) + "\0" + str(rule.get("content", ""))))
        for rule in rules
    ]


def _context_output(event: str, context: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def handle_event(payload: dict[str, Any], *, script_path: str | Path | None = None) -> dict[str, Any] | None:
    """Return Codex-supported advisory context for one hook event."""
    if not isinstance(payload, dict):
        return None
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or event not in _SUPPORTED_HOOK_EVENTS:
        return None
    root = find_registry_root(payload.get("cwd"), script_path)
    if root is None:
        _record_hook_receipt(payload, event, "no_match", script_path=script_path)
        return None
    try:
        if _load_registry:
            _load_registry(root)
        else:
            _fallback_load_registry(root)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        _record_hook_receipt(payload, event, "no_match", script_path=script_path)
        return None

    repo_root = _event_repo_root(root, payload.get("cwd"))
    selected_rules: list[dict[str, Any]] = []
    paths: list[str] = []
    advisory = ""
    if event == "SessionStart":
        selected_rules = [{
            "id": "hve-rule-index",
            "content": (
                "HVE scoped instructions are selected by repo-relative file path. "
                "Before editing a known target, use hve-codex rules explain <path> "
                "or hve-codex rules match <paths...>. Hook guidance is advisory."
            ),
        }]
    elif event == "SubagentStart":
        selected_rules = [{
            "id": "hve-rule-index",
            "content": (
                "HVE scoped instructions are indexed by repo-relative file path. "
                "Check hve-codex rules explain <path> for known targets before editing; "
                "hook guidance is advisory."
            ),
        }]
    elif event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        paths = extract_prompt_paths(prompt, repo_root) if isinstance(prompt, str) else []
        if paths:
            try:
                selected_rules = _match_rules(root, paths) if _match_rules else _fallback_match_rules(root, paths)
            except (OSError, ValueError, json.JSONDecodeError):
                selected_rules = []
    else:
        tool_name = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input")
        paths, uncertain_shell_write = extract_tool_paths(tool_name, tool_input, repo_root)
        if paths:
            try:
                selected_rules = _match_rules(root, paths) if _match_rules else _fallback_match_rules(root, paths)
            except (OSError, ValueError, json.JSONDecodeError):
                selected_rules = []
        if uncertain_shell_write:
            if paths:
                advisory = (
                    "Shell target extraction is best-effort and may miss variable, generated, or indirect targets. "
                    "Resolve the complete target paths before relying on scoped rules; this hook is advisory."
                )
            else:
                advisory = (
                    "A shell command may write files, but its target paths could not be inferred. "
                    "Resolve the targets and consult the scoped rules; this hook is advisory."
                )
            selected_rules = [*selected_rules, {"id": "hve-shell-path-advisory", "content": advisory}]

    if event in {"SessionStart", "SubagentStart"}:
        # Always-on repository instructions belong in initial host context.
        # This extensionless sentinel selects global patterns without selecting
        # Python, Markdown, or other file-specific instructions.
        try:
            global_paths = ["__hve_global_context__"]
            global_rules = _match_rules(root, global_paths) if _match_rules else _fallback_match_rules(root, global_paths)
            selected_rules = [*selected_rules, *global_rules]
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    if not selected_rules:
        _record_hook_receipt(payload, event, "no_match", script_path=script_path)
        return None
    items = _rule_items(selected_rules)
    if event == "UserPromptSubmit":
        prefix = "HVE scoped rules for mentioned repo-relative paths: " + ", ".join(paths) + "." if paths else "HVE scoped rule index."
    elif event == "PreToolUse":
        if paths:
            prefix = "Advisory scoped rules for pending repo-relative target(s): " + ", ".join(paths) + "."
        else:
            prefix = "Advisory scoped rule guidance for this tool call."
    else:
        prefix = "HVE Core scoped-rule guidance."

    scope = _scope_key(payload)
    state_path = _state_file() if scope else None
    state = _read_state(state_path) if state_path else {"version": 1, "scopes": {}}
    existing = state["scopes"].get(scope, {}) if scope else {}
    hashes = existing.get("hashes", []) if isinstance(existing, dict) else []
    used_hashes = set(hashes) if isinstance(hashes, list) else set()
    context, added_hashes = _bounded_context(items, prefix, used_hashes)
    if not added_hashes:
        outcome = "dedup" if items and all(digest in used_hashes for _rule_id, _content, digest in items) else "no_match"
        _record_hook_receipt(payload, event, outcome, rules=selected_rules, script_path=script_path)
        return None
    if scope and state_path:
        state["scopes"][scope] = {
            "updated": int(time.time()),
            "hashes": list(dict.fromkeys([*used_hashes, *added_hashes]))[-_MAX_HASHES_PER_SCOPE:],
        }
        try:
            _write_state(state_path, state)
        except OSError:
            # Context remains useful even when this local optimization cannot persist.
            pass
    result = _context_output(event, context)
    _record_hook_receipt(payload, event, "context_emitted", rules=selected_rules, context=context, script_path=script_path)
    return result


def _registry_under_root(value: str | Path) -> Path | None:
    """Find a project registry without falling back outside an explicit root."""
    try:
        requested = Path(value).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if not requested.is_dir():
        return None
    for candidate in (
        requested,
        requested / "rules",
        requested / "codex-overlay",
        requested / "codex-overlay" / "rules",
        requested / ".codex",
        requested / ".codex" / "rules",
        requested / ".codex" / "hve-core",
        requested / ".codex" / "hve-core" / "rules",
    ):
        try:
            resolved = candidate.resolve()
            index = resolved / "index.json"
            if not index.is_file():
                continue
            if candidate != requested:
                resolved.relative_to(requested)
            return resolved.parent if resolved.name.casefold() == "rules" else resolved
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _rules_match_cli(argv: list[str]) -> int:
    """Print read-only rule context for paths without simulating a hook event."""
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="Read scoped HVE rules for repo-relative paths (does not run a hook event).",
    )
    parser.add_argument("--rules-match", nargs="+", metavar="PATH", required=True)
    parser.add_argument("--root", help="project root or its rules directory; defaults to the current project")
    args = parser.parse_args(argv)

    if args.root:
        registry_root = _registry_under_root(args.root)
        if registry_root is None:
            print(f"hve_rules: no rules/index.json found under --root {args.root!r}", file=sys.stderr)
            return 2
        repo_root = _repo_root_for_registry(registry_root)
    else:
        registry_root = find_registry_root(Path.cwd(), __file__)
        if registry_root is None:
            print("hve_rules: no HVE rules registry found from the current project", file=sys.stderr)
            return 2
        repo_root = _event_repo_root(registry_root, Path.cwd())

    targets: list[str] = []
    for value in args.rules_match:
        normalized = _normalize_target(value, repo_root)
        if normalized is None:
            print(f"hve_rules: unsafe or out-of-project target path: {value!r}", file=sys.stderr)
            return 2
        if normalized not in targets:
            targets.append(normalized)

    try:
        resolver = _match_rules or _fallback_match_rules
        results = []
        for target in targets:
            rules = resolver(registry_root, [target])
            results.append({
                "path": target,
                "matched": bool(rules),
                "rule_ids": [rule["id"] for rule in rules],
                "rules": rules,
            })
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"hve_rules: could not resolve scoped rules: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(json.dumps({"targets": results}, ensure_ascii=True, indent=2))
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the explicit read-only CLI or read one native hook payload from stdin."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        return _rules_match_cli(arguments)
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            return 0
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, OSError):
        return 0
    result = handle_event(payload, script_path=__file__)
    if result is not None:
        sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
