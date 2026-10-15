"""Shared deterministic serialization and filesystem boundaries."""
from __future__ import annotations
import hashlib
import json
import re
import sys
import unicodedata
from datetime import date, datetime, time as datetime_time
from typing import Any
from pathlib import Path, PurePosixPath
from ruamel.yaml import YAML

class PortError(ValueError):
    """An actionable conversion or installation failure."""


def is_canonical_var_alias(path: Path) -> bool:
    """Allow only macOS's canonical /var -> /private/var path alias.

    Callers still reject symlinks at their selected root and below it. This
    exception applies only to the system path prefix used by macOS temp dirs.
    """
    if sys.platform != "darwin" or path != Path("/var"):
        return False
    try:
        return path.is_symlink() and path.resolve(strict=True) == Path("/private/var")
    except OSError:
        return False

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    def serialize(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise PortError(f"Unsupported serialization type: {type(item).__name__}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=serialize) + "\n", encoding="utf-8", newline="\n")

def safe_path(root: Path, relative: str) -> Path:
    if "\\" in relative or ":" in relative or "\x00" in relative:
        raise PortError(f"Unsafe path: {relative!r}")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise PortError(f"Unsafe path: {relative!r}")
    reserved = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)), *("lpt" + str(i) for i in range(1, 10))}
    if any(p.rstrip(" .") != p or p.split(".")[0].casefold() in reserved for p in rel.parts):
        raise PortError(f"Non-portable Windows path: {relative!r}")
    target = root.joinpath(*rel.parts)
    for part in [target, *target.parents]:
        if part == root.parent:
            break
        if part.is_symlink():
            raise PortError(f"Symlink is not permitted: {part}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise PortError(f"Path escapes root: {relative}")
    return target

def frontmatter(text: str, source: str = "input") -> tuple[dict, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise PortError(f"Unclosed frontmatter: {source}")
    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        meta = yaml.load("".join(lines[1:end])) or {}
    except Exception as exc:
        raise PortError(f"Malformed YAML in {source}: {exc}") from exc
    if not isinstance(meta, dict):
        raise PortError(f"Frontmatter must be a mapping: {source}")
    active = set()
    count = 0
    def check(value, depth=0):
        nonlocal count
        count += 1
        if depth > 32 or count > 10000:
            raise PortError(f"Frontmatter is too complex: {source}")
        if isinstance(value, (dict, list, tuple)):
            identity = id(value)
            if identity in active:
                raise PortError(f"Recursive YAML alias in {source}")
            active.add(identity)
            for item in (value.items() if isinstance(value, dict) else value):
                check(item, depth + 1)
            active.remove(identity)
    check(meta)
    return meta, "".join(lines[end + 1:])

def slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return result or "artifact"

def text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def json_safe(value: Any, _active: set[int] | None = None) -> Any:
    if _active is None:
        _active = set()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        identity = id(value)
        if identity in _active:
            return {"unsupported_value_type": "recursive-yaml-alias"}
        _active.add(identity)
        result = {str(key): json_safe(child, _active) for key, child in value.items()}
        _active.remove(identity)
        return result
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _active:
            return {"unsupported_value_type": "recursive-yaml-alias"}
        _active.add(identity)
        result = [json_safe(child, _active) for child in value]
        _active.remove(identity)
        return result
    if isinstance(value, (set, frozenset)):
        return sorted((json_safe(child, _active) for child in value), key=lambda item: str(item))
    if isinstance(value, bytes):
        return {"unsupported_value_type": "bytes", "sha256": hashlib.sha256(value).hexdigest()}
    return {"unsupported_value_type": type(value).__name__, "repr": repr(value)[:500]}
