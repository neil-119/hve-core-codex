"""Load and resolve the path-scoped rules in a Codex overlay registry.

The resolver intentionally uses only the standard library. Registry globs use
repo-relative POSIX paths, ``*``/``?``/character classes, ``**`` for recursive
matching, and simple brace alternatives. A leading ``!`` excludes a match; the
last matching pattern wins, which permits an explicit later re-include.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable


_REQUIRED_FIELDS = ("id", "source", "path", "apply_to", "precedence", "imports")


def _registry_dir(root: Path) -> Path:
    """Resolve either an overlay root or the rules directory itself."""
    raw_base = Path(root).expanduser()
    base = raw_base.resolve()
    candidates = (raw_base, raw_base / "rules") if raw_base.name.casefold() != "rules" else (raw_base,)
    for candidate in candidates:
        index_path = candidate / "index.json"
        if not index_path.is_file():
            continue
        resolved_candidate = candidate.resolve()
        # A repository root may not redirect its rules directory elsewhere.
        # A caller that passes a registry directory directly owns that root.
        if candidate != raw_base:
            try:
                resolved_candidate.relative_to(base)
            except ValueError as exc:
                raise ValueError(f"Rules directory escapes the supplied root: {candidate}") from exc
        try:
            index_path.resolve().relative_to(resolved_candidate)
        except ValueError as exc:
            raise ValueError(f"Rules index escapes the rules directory: {index_path}") from exc
        return resolved_candidate
    raise FileNotFoundError(f"No rules/index.json or index.json found under {base}")


def _safe_relative(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    raw = value.strip()
    # Treat both separator styles as separators, even on POSIX, so Windows
    # drive, UNC, and traversal forms cannot become harmless-looking names.
    win = PureWindowsPath(raw)
    if win.is_absolute() or win.drive or raw.startswith(("/", "\\")):
        raise ValueError(f"Unsafe absolute path in {label}: {value!r}")
    normalized = raw.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in ("..", "") for part in parts):
        raise ValueError(f"Unsafe path component in {label}: {value!r}")
    if any(part == "." for part in parts):
        parts = [part for part in parts if part != "."]
    if not parts:
        raise ValueError(f"{label} must name a file")
    return "/".join(parts)


def _content_path(rules_dir: Path, relative: str) -> Path:
    safe = _safe_relative(relative, label="rule path")
    candidate = (rules_dir / Path(*safe.split("/"))).resolve()
    try:
        candidate.relative_to(rules_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Rule content escapes the rules directory: {relative!r}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Rule content file does not exist: {candidate}")
    return candidate


def load_registry(root: Path) -> list[dict[str, Any]]:
    """Load and validate ``index.json`` and its UTF-8 rule content files.

    ``root`` may be a repository/overlay root containing ``rules/index.json``
    or the rules directory containing ``index.json``. Each returned record
    preserves its registry fields and includes a ``content`` string.
    """
    rules_dir = _registry_dir(Path(root))
    index_path = rules_dir / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid rules registry JSON at {index_path}: {exc}") from exc
    if (
        not isinstance(index, dict)
        or isinstance(index.get("version"), bool)
        or index.get("version") != 1
    ):
        raise ValueError(f"Unsupported rules registry version in {index_path}; expected version 1")
    entries = index.get("rules")
    if not isinstance(entries, list):
        raise ValueError(f"Rules registry must contain a rules array: {index_path}")

    loaded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, item in enumerate(entries):
        label = f"rules[{position}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object")
        missing = [field for field in _REQUIRED_FIELDS if field not in item]
        if missing:
            raise ValueError(f"{label} is missing required field(s): {', '.join(missing)}")
        rule_id = item["id"]
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError(f"{label}.id must be a non-empty string")
        rule_id = rule_id.strip()
        if rule_id in seen:
            raise ValueError(f"Duplicate rule id: {rule_id}")
        seen.add(rule_id)
        if not isinstance(item["source"], str) or not item["source"].strip():
            raise ValueError(f"{label}.source must be a non-empty string")
        if not isinstance(item["apply_to"], list) or any(not isinstance(p, str) or not p.strip() for p in item["apply_to"]):
            raise ValueError(f"{label}.apply_to must be a list of non-empty strings")
        if "auto_attach" in item and not isinstance(item["auto_attach"], bool):
            raise ValueError(f"{label}.auto_attach must be a boolean")
        precedence = item["precedence"]
        if isinstance(precedence, bool) or not isinstance(precedence, int):
            raise ValueError(f"{label}.precedence must be an integer")
        if not isinstance(item["imports"], list) or any(not isinstance(dep, str) or not dep.strip() for dep in item["imports"]):
            raise ValueError(f"{label}.imports must be a list of non-empty strings")
        safe_content_path = _safe_relative(item["path"], label=f"{label}.path")
        try:
            content = _content_path(rules_dir, safe_content_path).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Rule content is not UTF-8: {safe_content_path}") from exc
        record = dict(item)
        record.update({"id": rule_id, "path": safe_content_path, "content": content})
        loaded.append(record)

    aliases: dict[str, str] = {}
    for rule in loaded:
        for alias in (rule["id"], rule["source"], rule["path"]):
            previous = aliases.get(alias)
            if previous is not None and previous != rule["id"]:
                raise ValueError(f"Ambiguous rule import reference {alias!r}")
            aliases[alias] = rule["id"]
    for rule in loaded:
        for dependency in rule["imports"]:
            if dependency not in aliases:
                raise ValueError(f"Rule {rule['id']!r} imports unknown rule reference {dependency!r}")
    return loaded


def _normalize_repo_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty repo-relative path")
    raw = path.strip().replace("\\", "/")
    win = PureWindowsPath(raw)
    if win.is_absolute() or win.drive or raw.startswith("/"):
        raise ValueError(f"Expected a repo-relative path, got {path!r}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Unsafe repo-relative path: {path!r}")
    return "/".join(parts)


def _expand_braces(pattern: str) -> list[str]:
    """Expand simple nested ``{a,b}`` alternatives without a dependency."""
    opening = -1
    depth = 0
    closing = -1
    for index, char in enumerate(pattern):
        if char == "{":
            if depth == 0:
                opening = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                closing = index
                break
    if opening < 0 or closing < 0:
        return [pattern]
    body = pattern[opening + 1 : closing]
    options: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            options.append(body[start:index])
            start = index + 1
    options.append(body[start:])
    if len(options) == 1:
        return [pattern]
    expanded: list[str] = []
    for option in options:
        expanded.extend(_expand_braces(pattern[:opening] + option + pattern[closing + 1 :]))
    return expanded


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate the supported glob syntax to a case-sensitive regex."""
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if (
                i + 1 < len(pattern) and pattern[i + 1] == "*"
                and (i == 0 or pattern[i - 1] == "/")
                and (i + 2 == len(pattern) or pattern[i + 2] == "/")
            ):
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(r"\[")
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                elif body.startswith("^"):
                    body = "\\" + body
                out.append("[" + body.replace("\\", r"\\") + "]")
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _pattern_matches(path: str, pattern: str) -> bool:
    normalized = pattern.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return False
    return any(_glob_regex(candidate).match(path) is not None for candidate in _expand_braces(normalized))


def _patterns_for(rule: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for value in rule.get("apply_to", []):
        # A few source manifests encode multiple globs in one comma-separated
        # value. Split only outside braces so alternatives remain intact.
        depth = 0
        start = 0
        for index, char in enumerate(value):
            if char == "{":
                depth += 1
            elif char == "}" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                part = value[start:index].strip()
                if part:
                    patterns.append(part)
                start = index + 1
        part = value[start:].strip()
        if part:
            patterns.append(part)
    return patterns


def _rule_matches_path(rule: dict[str, Any], path: str) -> bool:
    if rule.get("auto_attach", True) is False:
        return False
    patterns = _patterns_for(rule)
    positives = [pattern for pattern in patterns if not pattern.startswith("!")]
    included = not positives
    for pattern in patterns:
        negate = pattern.startswith("!")
        body = pattern[1:] if negate else pattern
        if _pattern_matches(path, body):
            included = not negate
    return included


def _effective_rules(rules: list[dict[str, Any]], matching_ids: Iterable[str]) -> tuple[list[dict[str, Any]], set[str]]:
    by_id = {rule["id"]: rule for rule in rules}
    aliases: dict[str, str] = {}
    for rule in rules:
        for alias in (rule["id"], rule["source"], rule["path"]):
            aliases[alias] = rule["id"]
    direct = set(matching_ids)
    included = set(direct)
    stack = list(direct)
    while stack:
        rule_id = stack.pop()
        for dependency in by_id[rule_id]["imports"]:
            dependency_id = aliases[dependency]
            if dependency_id not in included:
                included.add(dependency_id)
                stack.append(dependency_id)
    ordered = sorted((by_id[rule_id] for rule_id in included), key=lambda rule: (rule["precedence"], rule["id"]))
    imported = included - direct
    return ordered, imported


def match_rules(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    """Return rules matching any repo-relative path plus imported dependencies.

    Results are ordered from lower to higher precedence, then by rule id. This
    makes later records the final word when consumers apply the instructions.
    """
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise ValueError("paths must be a list of repo-relative strings")
    normalized_paths = [_normalize_repo_path(path) for path in paths]
    rules = load_registry(Path(root))
    matching = {
        rule["id"]
        for rule in rules
        if any(_rule_matches_path(rule, path) for path in normalized_paths)
    }
    effective, _ = _effective_rules(rules, matching)
    return effective


def explain_rules(root: Path, path: str) -> dict[str, Any]:
    """Explain direct matches and imported rules for one repo-relative path."""
    normalized_path = _normalize_repo_path(path)
    rules = load_registry(Path(root))
    direct_ids = {
        rule["id"] for rule in rules if _rule_matches_path(rule, normalized_path)
    }
    effective, imported_ids = _effective_rules(rules, direct_ids)
    direct_rules = [rule for rule in effective if rule["id"] in direct_ids]
    imported_rules = [rule for rule in effective if rule["id"] in imported_ids]
    return {
        "path": normalized_path,
        "matched": bool(direct_rules),
        "matched_rules": direct_rules,
        "imported_rules": imported_rules,
        "rules": effective,
    }


__all__ = ["load_registry", "match_rules", "explain_rules"]
