"""Prepare exact, offline npm fixture dependencies for Vally bundles.

The upstream package-lock is the authority for package versions and SRI. This
module never invokes npm or the network: it reads content-addressed npm cache
blobs, verifies the lock integrity, compares the installed package trees with
the published tarballs, and emits a self-contained receipt and proof tarballs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any

from .common import PortError, digest, dump_json, safe_path


RECEIPT_NAME = "fixture-dependencies.json"
_SCHEMA_VERSION = 1
_COMPARE_SCHEMA_VERSION = 2
_VALLY_CLI_LOCK_PATH = "node_modules/@microsoft/vally-cli"
_VALLY_CLI_VERSION = "0.17.0"
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_TARBALL_BYTES = 128 * 1024 * 1024
_MAX_PACKAGE_FILE_BYTES = 192 * 1024 * 1024
_MAX_PACKAGE_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_MAX_PACKAGE_FILES = 20_000
_MAX_PACKAGES = 500
_MAX_TAR_MEMBERS = 25_000
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ALGORITHMS = {"sha512": hashlib.sha512, "sha384": hashlib.sha384, "sha256": hashlib.sha256}


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _reject_reparse_chain(path: Path, label: str) -> None:
    absolute = Path(os.path.abspath(path.expanduser()))
    chain = [*reversed(absolute.parents), absolute]
    for item in chain:
        if _reparse(item):
            raise PortError(f"{label} contains a symlink or reparse point: {item}")


def _input_directory(path: Path, label: str) -> Path:
    expanded = Path(path).expanduser()
    _reject_reparse_chain(expanded, label)
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise PortError(f"{label} is missing or inaccessible.") from exc
    if _reparse(resolved) or not resolved.is_dir():
        raise PortError(f"{label} must be a regular directory.")
    return resolved


def _run_git(upstream: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(upstream), *arguments],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PortError("Could not read the pinned upstream Git objects.") from exc
    if completed.returncode != 0:
        raise PortError("Could not read the pinned upstream Git objects.")
    return completed.stdout


def _pinned_upstream(upstream: Path, expected_sha: str) -> tuple[str, bytes, bytes]:
    if not isinstance(expected_sha, str) or not _SHA_RE.fullmatch(expected_sha.lower()):
        raise PortError("Expected upstream SHA must be an exact 40-character Git commit.")
    expected = expected_sha.lower()
    head = _run_git(upstream, "rev-parse", "HEAD").decode("ascii", errors="strict").strip().lower()
    if head != expected:
        raise PortError("Upstream Git HEAD does not match the expected commit.")
    manifest_bytes: dict[str, bytes] = {}
    for name in ("package.json", "package-lock.json"):
        canonical = _run_git(upstream, "cat-file", "blob", f"{expected}:{name}")
        path = safe_path(upstream, name)
        if _reparse(path) or not path.is_file():
            raise PortError(f"Pinned upstream {name} is missing or unsafe.")
        current = path.read_bytes()
        if current != canonical:
            raise PortError(f"Upstream {name} differs from its pinned Git blob.")
        manifest_bytes[name] = canonical
    if max(map(len, manifest_bytes.values())) > _MAX_JSON_BYTES:
        raise PortError("Pinned upstream package manifests exceed the size limit.")
    return expected, manifest_bytes["package.json"], manifest_bytes["package-lock.json"]


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortError(f"Could not parse {label}.") from exc
    if not isinstance(value, dict):
        raise PortError(f"{label} must contain a JSON object.")
    return value


def _package_lock(package_bytes: bytes, lock_bytes: bytes) -> dict[str, Any]:
    package = _json_object(package_bytes, "pinned upstream package.json")
    lock = _json_object(lock_bytes, "pinned upstream package-lock.json")
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict):
        raise PortError("Fixture dependency preparation requires a version 3 npm package-lock.")
    root_entry = lock["packages"].get("")
    if not isinstance(root_entry, dict):
        raise PortError("Pinned package-lock has no root package entry.")
    if package.get("name") != root_entry.get("name") or package.get("version") != root_entry.get("version"):
        raise PortError("Pinned package.json identity disagrees with package-lock.")
    return lock


def _vally_cli_compare_root(package_bytes: bytes, lock: dict[str, Any]) -> str:
    """Return the pinned Vally CLI root, or fail closed if its pin changed."""
    package = _json_object(package_bytes, "pinned upstream package.json")
    root_entry = lock["packages"].get("")
    package_declared = package.get("devDependencies")
    lock_declared = root_entry.get("devDependencies") if isinstance(root_entry, dict) else None
    if (
        not isinstance(package_declared, dict)
        or package_declared.get("@microsoft/vally-cli") != _VALLY_CLI_VERSION
        or not isinstance(lock_declared, dict)
        or lock_declared.get("@microsoft/vally-cli") != _VALLY_CLI_VERSION
    ):
        raise PortError(
            f"Compare reuse requires @microsoft/vally-cli exactly pinned to {_VALLY_CLI_VERSION}."
        )
    entry = lock["packages"].get(_VALLY_CLI_LOCK_PATH)
    if not isinstance(entry, dict) or entry.get("link") is True or entry.get("version") != _VALLY_CLI_VERSION:
        raise PortError(
            f"Compare reuse requires the package-lock entry for @microsoft/vally-cli {_VALLY_CLI_VERSION}."
        )
    return _VALLY_CLI_LOCK_PATH


def _package_root_from_reference(reference: str) -> str | None:
    parts = PurePosixPath(reference).parts
    if not parts or parts[0] != "node_modules":
        return None
    if len(parts) < 2:
        raise PortError(f"Vally fixture dependency reference has no package name: {reference!r}.")
    count = 1
    while count < len(parts):
        if parts[count] == "node_modules":
            if count + 1 >= len(parts):
                raise PortError(f"Vally fixture dependency reference has an invalid package name: {reference!r}.")
            count += 1
        if parts[count].startswith("@"):
            if count + 1 >= len(parts) or parts[count + 1] in {"", "node_modules"}:
                raise PortError(f"Vally fixture dependency reference has an invalid package name: {reference!r}.")
            count += 2
        else:
            count += 1
        if count >= len(parts) or parts[count] != "node_modules":
            break
    root = PurePosixPath(*parts[:count]).as_posix()
    if root in {"node_modules/.bin", "node_modules/.cache"}:
        raise PortError(f"Vally fixture dependency reference is not a package: {reference!r}.")
    return root


def package_roots_from_references(references: list[str]) -> list[str]:
    """Return lockfile package roots referenced by eval environment files."""
    roots = {_package_root_from_reference(item) for item in references}
    roots.discard(None)
    return sorted(roots, key=lambda item: (item.casefold(), item))


def _dependency_lock_path(package_path: str, name: str, packages: dict[str, Any]) -> str | None:
    name_parts = name.split("/")
    if (
        not name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or name.startswith("/")
        or any(not item or item in {".", ".."} for item in name_parts)
        or (name_parts[0].startswith("@") and len(name_parts) < 2)
    ):
        raise PortError(f"Invalid package dependency name in package-lock: {name!r}.")
    container = PurePosixPath(package_path)
    while True:
        candidate = (container / "node_modules" / PurePosixPath(*name_parts)).as_posix()
        if candidate in packages:
            return candidate
        if not container.parts:
            break
        container = container.parent
    return None


def _dependency_closure(
    lock: dict[str, Any],
    roots: list[str],
    *,
    installed_root: Path | None = None,
    selected_optional_roots: set[str] | None = None,
    include_optional_roots: set[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    if installed_root is not None and include_optional_roots is not None:
        raise PortError("Optional dependency selection cannot use both an install tree and a receipt.")
    packages = lock["packages"]
    queue: deque[str] = deque(roots)
    seen: set[str] = set()
    discovered_optional_roots: set[str] = set()
    result: list[tuple[str, dict[str, Any]]] = []
    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        if len(seen) > _MAX_PACKAGES:
            raise PortError(f"Locked fixture dependency closure exceeds {_MAX_PACKAGES} packages.")
        entry = packages.get(path)
        if not isinstance(entry, dict) or entry.get("link") is True:
            raise PortError(f"Fixture package is missing from package-lock or is a link: {path!r}.")
        result.append((path, entry))
        optional = entry.get("optionalDependencies", {})
        dependencies = entry.get("dependencies", {})
        peers = entry.get("peerDependencies", {})
        peer_meta = entry.get("peerDependenciesMeta", {})
        if any(not isinstance(value, dict) for value in (optional, dependencies, peers, peer_meta)):
            raise PortError(f"Invalid dependency metadata in package-lock for {path!r}.")
        for name in sorted(set(dependencies) | set(optional), key=lambda item: (item.casefold(), item)):
            if not isinstance(name, str):
                raise PortError(f"Invalid dependency name in package-lock for {path!r}.")
            resolved = _dependency_lock_path(path, name, packages)
            is_optional = name in optional and name not in dependencies
            if resolved is None:
                if is_optional:
                    continue
                raise PortError(f"Locked dependency {name!r} is missing from package-lock below {path!r}.")
            if is_optional:
                if installed_root is not None:
                    optional_path = safe_path(installed_root, resolved)
                    _reject_reparse_chain(optional_path, "Installed optional dependency")
                    if not optional_path.exists():
                        continue
                    if not optional_path.is_dir():
                        raise PortError(f"Installed optional dependency is not a package directory: {resolved}.")
                    if selected_optional_roots is not None:
                        selected_optional_roots.add(resolved)
                elif include_optional_roots is not None:
                    if resolved not in include_optional_roots:
                        continue
                    discovered_optional_roots.add(resolved)
            queue.append(resolved)
        if not isinstance(peers, dict) or not isinstance(peer_meta, dict):
            raise PortError(f"Invalid peer dependency metadata in package-lock for {path!r}.")
        for name in sorted(peers, key=lambda item: (item.casefold(), item)):
            if not isinstance(name, str):
                raise PortError(f"Invalid peer dependency name in package-lock for {path!r}.")
            resolved = _dependency_lock_path(path, name, packages)
            is_optional = isinstance(peer_meta.get(name), dict) and peer_meta[name].get("optional") is True
            if resolved is None:
                if is_optional:
                    continue
                raise PortError(f"Locked peer dependency {name!r} is missing from package-lock below {path!r}.")
            if is_optional:
                if installed_root is not None:
                    optional_path = safe_path(installed_root, resolved)
                    _reject_reparse_chain(optional_path, "Installed optional peer dependency")
                    if not optional_path.exists():
                        continue
                    if not optional_path.is_dir():
                        raise PortError(f"Installed optional peer dependency is not a package directory: {resolved}.")
                    if selected_optional_roots is not None:
                        selected_optional_roots.add(resolved)
                elif include_optional_roots is not None:
                    if resolved not in include_optional_roots:
                        continue
                    discovered_optional_roots.add(resolved)
            queue.append(resolved)
    if include_optional_roots is not None and discovered_optional_roots != include_optional_roots:
        raise PortError("Prepared dependency receipt selects optional package roots outside the lockfile closure.")
    return sorted(result, key=lambda item: (len(PurePosixPath(item[0]).parts), item[0].casefold(), item[0]))


def _sri_tokens(integrity: Any) -> list[tuple[str, str, bytes]]:
    if not isinstance(integrity, str) or not integrity:
        raise PortError("Locked fixture package has no npm SRI integrity value.")
    tokens: list[tuple[str, str, bytes]] = []
    for item in integrity.split():
        match = re.fullmatch(r"(sha512|sha384|sha256)-([A-Za-z0-9+/]+={0,2})(?:\?.*)?", item)
        if not match:
            continue
        algorithm, encoded = match.groups()
        try:
            expected = base64.b64decode(encoded, validate=True)
        except ValueError:
            continue
        if len(expected) != _ALGORITHMS[algorithm]().digest_size:
            continue
        tokens.append((algorithm, encoded, expected))
    if not tokens:
        raise PortError("Locked fixture package has no supported SHA-2 SRI integrity token.")
    tokens.sort(key=lambda item: (-(item[0] == "sha512") * 3 - (item[0] == "sha384") * 2, item[0], item[1]))
    return tokens


def _cache_content_path(cache: Path, algorithm: str, digest_bytes: bytes) -> Path:
    root = cache / "_cacache" if (cache / "_cacache").is_dir() else cache
    if root.name != "_cacache" or not (root / "content-v2").is_dir():
        raise PortError("npm cache must contain a CACache content-v2 directory.")
    hexadecimal = digest_bytes.hex()
    return root / "content-v2" / algorithm / hexadecimal[:2] / hexadecimal[2:4] / hexadecimal[4:]


def _verified_cache_blob(cache: Path, integrity: str) -> tuple[bytes, str, str]:
    for algorithm, encoded, expected in _sri_tokens(integrity):
        candidate = _cache_content_path(cache, algorithm, expected)
        _reject_reparse_chain(candidate, "npm cache content")
        if not candidate.is_file():
            continue
        size = candidate.stat().st_size
        if size > _MAX_TARBALL_BYTES:
            raise PortError("Locked fixture package tarball exceeds the size limit.")
        raw = candidate.read_bytes()
        if _ALGORITHMS[algorithm](raw).digest() != expected:
            raise PortError("npm cache content does not match the package-lock SRI.")
        return raw, f"{algorithm}-{encoded}", digest(raw)
    raise PortError("A lockfile-pinned fixture package tarball is absent from the offline npm cache.")


def _archive_members(raw: bytes, label: str) -> list[tuple[str, bytes]]:
    if len(raw) > _MAX_TARBALL_BYTES:
        raise PortError(f"Fixture package tarball exceeds the size limit: {label}.")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise PortError(f"Fixture package SRI blob is not a readable npm tarball: {label}.") from exc
    result: list[tuple[str, bytes]] = []
    folded: dict[str, str] = {}
    seen_types: dict[str, str] = {}
    seen_files: dict[str, bytes] = {}
    directory_paths: set[str] = set()
    archive_root: str | None = None
    total = 0
    try:
        member_count = 0
        for member in archive:
            member_count += 1
            if member_count > _MAX_TAR_MEMBERS:
                raise PortError(f"Fixture package tarball has too many entries: {label}.")
            name = member.name
            if "\\" in name or "\x00" in name or name.startswith("/") or ":" in name:
                raise PortError(f"Fixture package tarball contains an unsafe path: {label}.")
            raw_parts = name.split("/")
            if (
                not raw_parts
                or not raw_parts[0]
                or raw_parts[0] in {".", ".."}
                or any(part == ".." for part in raw_parts)
                or any(not part and index != len(raw_parts) - 1 for index, part in enumerate(raw_parts))
            ):
                raise PortError(f"Fixture package tarball path escapes its package root: {label}.")
            if archive_root is None:
                archive_root = raw_parts[0]
            elif raw_parts[0] != archive_root:
                raise PortError(f"Fixture package tarball has multiple package roots: {label}.")
            parts = [part for part in raw_parts if part not in {"", "."}]
            relative = PurePosixPath(*parts[1:]).as_posix()
            duplicate_path = False
            if relative and relative != ".":
                folded_path = relative.casefold()
                prior_name = folded.get(folded_path)
                if prior_name is not None and prior_name != relative:
                    raise PortError(f"Fixture package tarball contains duplicate or colliding paths: {label}.")
                prior_type = seen_types.get(relative)
                member_type = "directory" if member.isdir() else "file" if member.isfile() else "special"
                if prior_type is not None:
                    if prior_type != member_type or member_type == "special":
                        raise PortError(f"Fixture package tarball contains duplicate or colliding paths: {label}.")
                    duplicate_path = True
                else:
                    folded[folded_path] = relative
                    seen_types[relative] = member_type
            if member.isdir():
                if relative and relative != ".":
                    directory_paths.add(relative)
                continue
            if not member.isfile():
                raise PortError(f"Fixture package tarball contains a link or special file: {label}.")
            if not relative or relative == ".":
                raise PortError(f"Fixture package tarball contains a root file: {label}.")
            safe_path(Path("C:/fixture-tarball"), relative)
            if member.size < 0 or member.size > _MAX_PACKAGE_FILE_BYTES or (total := total + member.size) > _MAX_PACKAGE_BYTES:
                raise PortError(f"Fixture package tarball expands beyond the size limit: {label}.")
            stream = archive.extractfile(member)
            if stream is None:
                raise PortError(f"Fixture package tarball entry could not be read: {label}.")
            data = stream.read(member.size + 1)
            if len(data) != member.size:
                raise PortError(f"Fixture package tarball entry size does not match: {label}.")
            if duplicate_path:
                if seen_files.get(relative) != data:
                    raise PortError(f"Fixture package tarball contains conflicting duplicate paths: {label}.")
                continue
            seen_files[relative] = data
            result.append((relative, data))
    finally:
        archive.close()
    if not result:
        raise PortError(f"Fixture package tarball is empty: {label}.")
    file_paths = {path for path, _ in result}
    if any(not any(file_path.startswith(f"{directory}/") for file_path in file_paths) for directory in directory_paths):
        raise PortError(f"Fixture package tarball contains an empty directory: {label}.")
    return result


def _write_package_files(root: Path, package_path: str, members: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for relative, raw in members:
        output_relative = f"{package_path}/{relative}"
        target = safe_path(root, output_relative)
        if target.exists() or target.is_symlink():
            raise PortError(f"Locked fixture package output collision at {output_relative!r}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        files.append({"path": output_relative, "size": len(raw), "sha256": digest(raw)})
    return files


def _tree_files(root: Path, label: str) -> dict[str, tuple[int, str]]:
    if _reparse(root) or not root.is_dir():
        raise PortError(f"Installed fixture package is missing or unsafe: {label}.")
    result: dict[str, tuple[int, str]] = {}
    folded: set[str] = set()
    directories: set[str] = set()
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        here = Path(current)
        for name in list(dirs):
            candidate = here / name
            if _reparse(candidate) or not candidate.is_dir():
                raise PortError(f"Installed fixture package contains a symlink or reparse point: {label}.")
            directories.add(candidate.relative_to(root).as_posix())
        dirs.sort(key=lambda item: (item.casefold(), item))
        for name in sorted(names, key=lambda item: (item.casefold(), item)):
            candidate = here / name
            if _reparse(candidate) or not candidate.is_file():
                raise PortError(f"Installed fixture package contains a symlink or special file: {label}.")
            relative = candidate.relative_to(root).as_posix()
            safe_path(Path("C:/fixture-package"), relative)
            key = relative.casefold()
            if key in folded:
                raise PortError(f"Installed fixture package has case-colliding files: {label}.")
            folded.add(key)
            size = candidate.stat().st_size
            if size > _MAX_PACKAGE_FILE_BYTES:
                raise PortError(f"Installed fixture package file exceeds the size limit: {label}.")
            result[relative] = (size, digest(candidate.read_bytes()))
            if len(result) > _MAX_PACKAGE_FILES:
                raise PortError(f"Installed fixture package has too many files: {label}.")
    if any(not any(path.startswith(f"{directory}/") for path in result) for directory in directories):
        raise PortError(f"Installed fixture package contains an empty directory: {label}.")
    return result


def _expected_tree(members: list[tuple[str, bytes]]) -> dict[str, tuple[int, str]]:
    return {path: (len(raw), digest(raw)) for path, raw in members}


def _package_identity(package_path: str, entry: dict[str, Any], members: list[tuple[str, bytes]]) -> tuple[str, str]:
    package_json = next((raw for path, raw in members if path == "package.json"), None)
    if package_json is None:
        raise PortError(f"Pinned package tarball has no package.json: {package_path}.")
    parsed = _json_object(package_json, f"package.json in {package_path}")
    name, version = parsed.get("name"), parsed.get("version")
    if not isinstance(name, str) or not isinstance(version, str) or version != entry.get("version"):
        raise PortError(f"Pinned package tarball identity disagrees with package-lock: {package_path}.")
    parts = PurePosixPath(package_path).parts
    node_modules_index = max(index for index, part in enumerate(parts) if part == "node_modules")
    package_name_parts = parts[node_modules_index + 1:]
    package_name_parts = package_name_parts[:2] if package_name_parts and package_name_parts[0].startswith("@") else package_name_parts[:1]
    expected_name = "/".join(package_name_parts)
    if name != expected_name:
        raise PortError(f"Pinned package tarball name disagrees with package-lock: {package_path}.")
    return name, version


def _package_meta(lock_path: str, entry: dict[str, Any], members: list[tuple[str, bytes]]) -> tuple[str, str]:
    if not isinstance(entry.get("resolved"), str) or not entry["resolved"].startswith(("https://", "http://")):
        raise PortError(f"Pinned fixture package has no registry tarball URL: {lock_path}.")
    name, version = _package_identity(lock_path, entry, members)
    return name, version


def _nested_package_roots(package_path: str, closure_paths: list[str]) -> list[str]:
    prefix = f"{package_path}/node_modules/"
    return [path for path in closure_paths if path.startswith(prefix)]


def _compare_installed_tree(
    installed_root: Path,
    package_path: str,
    members: list[tuple[str, bytes]],
    closure_paths: list[str],
) -> None:
    installed = safe_path(installed_root, package_path)
    _reject_reparse_chain(installed, f"Installed fixture package {package_path}")
    actual = _tree_files(installed, package_path)
    for nested in _nested_package_roots(package_path, closure_paths):
        prefix = nested[len(package_path) + 1:]
        actual = {path: value for path, value in actual.items() if path != prefix and not path.startswith(f"{prefix}/")}
    expected = _expected_tree(members)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:3]
        extra = sorted(set(actual) - set(expected))[:3]
        changed = sorted(path for path in set(actual) & set(expected) if actual[path] != expected[path])[:3]
        raise PortError(
            f"Installed fixture package differs from its lock-SRI tarball ({package_path}; "
            f"missing={missing}, extra={extra}, changed={changed})."
        )


def _copy_proof_tarball(staging: Path, raw: bytes, selected_integrity: str) -> dict[str, Any]:
    algorithm, encoded = selected_integrity.split("-", 1)
    digest_bytes = base64.b64decode(encoded, validate=True)
    hexadecimal = digest_bytes.hex()
    relative = f".proof/tarballs/{algorithm}-{hexadecimal}.tgz"
    target = safe_path(staging, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != raw:
            raise PortError("Two fixture dependencies map to conflicting SRI tarballs.")
    else:
        target.write_bytes(raw)
    return {"path": relative, "size": len(raw), "sha256": digest(raw), "integrity": selected_integrity}


def _load_preparation_inputs(
    upstream_dir: Path,
    expected_upstream_sha: str,
    installed_root: Path,
    npm_cache: Path,
) -> tuple[Path, Path, Path, str, bytes, bytes, dict[str, Any]]:
    upstream = _input_directory(upstream_dir, "Upstream source")
    installed = _input_directory(installed_root, "Installed npm dependency root")
    cache = _input_directory(npm_cache, "Offline npm cache")
    if installed == upstream or installed.is_relative_to(upstream) or upstream.is_relative_to(installed):
        raise PortError("Installed dependencies and upstream source must use separate roots.")
    if cache == upstream or cache.is_relative_to(upstream) or cache == installed or cache.is_relative_to(installed):
        raise PortError("Offline npm cache must be separate from upstream and installed dependencies.")
    pinned, package_bytes, lock_bytes = _pinned_upstream(upstream, expected_upstream_sha)
    lock = _package_lock(package_bytes, lock_bytes)
    for name, expected in (("package.json", package_bytes), ("package-lock.json", lock_bytes)):
        path = safe_path(installed, name)
        if _reparse(path) or not path.is_file() or path.read_bytes() != expected:
            raise PortError(f"Installed dependency root {name} does not match the pinned upstream bytes.")
    return upstream, installed, cache, pinned, package_bytes, lock_bytes, lock


def prepare_locked_fixture_dependencies(
    upstream_dir: Path,
    expected_upstream_sha: str,
    installed_root: Path,
    npm_cache: Path,
    output: Path,
    *,
    include_vally_cli: bool = False,
) -> dict[str, Any]:
    """Create a fresh verified dependency root from lock-SRI npm cache blobs.

    ``include_vally_cli`` is an explicit compare-only opt-in; ordinary fixture
    preparation retains its existing package roots and output receipt schema.
    """
    upstream, installed, cache, pinned, package_bytes, lock_bytes, lock = _load_preparation_inputs(
        upstream_dir, expected_upstream_sha, installed_root, npm_cache
    )
    roots = _declared_fixture_package_roots(upstream)
    extra_roots = [_vally_cli_compare_root(package_bytes, lock)] if include_vally_cli else []
    roots.extend(extra_roots)
    roots = sorted(set(roots), key=lambda item: (item.casefold(), item))
    if not roots:
        raise PortError("No node_modules package references were found in upstream Vally fixture environments.")
    selected_optional_roots: set[str] = set()
    if include_vally_cli:
        closure = _dependency_closure(
            lock,
            roots,
            installed_root=installed,
            selected_optional_roots=selected_optional_roots,
        )
    else:
        closure = _dependency_closure(lock, roots)
    closure_paths = [path for path, _ in closure]
    destination_input = Path(output).expanduser()
    _reject_reparse_chain(destination_input, "Fixture dependency output")
    destination = destination_input.resolve()
    if destination.exists() or destination.is_symlink():
        raise PortError(f"Fixture dependency output must be fresh: {destination}")
    if destination == upstream or destination.is_relative_to(upstream) or destination == installed or destination.is_relative_to(installed):
        raise PortError("Fixture dependency output must be outside upstream and installed package roots.")
    if destination == cache or destination.is_relative_to(cache):
        raise PortError("Fixture dependency output must be outside the npm cache.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.fixture-deps-", dir=destination.parent))
    verify_root = staging / ".verification"
    package_records: list[dict[str, Any]] = []
    tarball_records: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    try:
        verify_root.mkdir()
        for index, (lock_path, entry) in enumerate(closure):
            raw_tarball, selected_integrity, tarball_sha = _verified_cache_blob(cache, entry.get("integrity"))
            if len(raw_tarball) > _MAX_TARBALL_BYTES:
                raise PortError(f"Fixture package tarball exceeds the size limit: {lock_path}.")
            members = _archive_members(raw_tarball, lock_path)
            name, version = _package_meta(lock_path, entry, members)
            total_bytes += sum(len(data) for _, data in members)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise PortError("Locked fixture dependency closure exceeds the total size limit.")
            verification_package = verify_root / str(index)
            verification_package.mkdir(parents=True)
            _write_package_files(verify_root, str(index), members)
            _compare_installed_tree(installed, lock_path, members, closure_paths)
            if not _tree_files(verification_package, lock_path) == _expected_tree(members):
                raise PortError(f"Could not materialize the verified package tarball: {lock_path}.")
            output_files = _write_package_files(staging, lock_path, members)
            proof = _copy_proof_tarball(staging, raw_tarball, selected_integrity)
            prior_proof = tarball_records.get(selected_integrity)
            if prior_proof is not None and prior_proof != proof:
                raise PortError("Conflicting fixture dependency proof tarballs share an SRI value.")
            tarball_records[selected_integrity] = proof
            package_records.append({
                "name": name,
                "version": version,
                "lock_path": lock_path,
                "resolved_sha256": digest(entry["resolved"].encode("utf-8")),
                "lock_integrity": entry["integrity"],
                "tarball": proof,
                "files": output_files,
            })
        shutil.rmtree(verify_root)
        receipt = {
            "schema_version": _COMPARE_SCHEMA_VERSION if extra_roots else _SCHEMA_VERSION,
            "source": {
                "upstream_sha": pinned,
                "package_json_sha256": digest(package_bytes),
                "package_lock_sha256": digest(lock_bytes),
            },
            "verification": "npm-lock-sri-tarball-byte-match",
            **({
                "extra_roots": extra_roots,
                "selected_optional_roots": sorted(selected_optional_roots, key=lambda item: (item.casefold(), item)),
            } if extra_roots else {}),
            "packages": package_records,
            "summary": {
                "package_count": len(package_records),
                "file_count": sum(len(item["files"]) for item in package_records),
                "proof_tarball_count": len(tarball_records),
                "scripts_executed": False,
                "network_accessed": False,
            },
        }
        dump_json(staging / RECEIPT_NAME, receipt)
        if destination.exists() or destination.is_symlink():
            raise PortError(f"Fixture dependency output appeared during preparation: {destination}")
        os.replace(staging, destination)
        return {
            "success": True,
            "output_dir": destination.as_posix(),
            "receipt": RECEIPT_NAME,
            "receipt_sha256": digest((destination / RECEIPT_NAME).read_bytes()),
            "source": receipt["source"],
            "summary": receipt["summary"],
        }
    except Exception:
        if staging.exists() and not _reparse(staging):
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _declared_fixture_package_roots(upstream: Path) -> list[str]:
    # Imported lazily to keep the lockfile verifier independent from YAML code
    # when it is used only for explicit prepared-root validation.
    from .vally_bundle import _environment_references, _read_yaml, _walk_eval_files

    findings: list[dict[str, str]] = []
    roots: set[str] = set()
    for path in _walk_eval_files(upstream, findings):
        if not _is_eval_spec_name(path.name):
            continue
        relative = path.relative_to(upstream).as_posix()
        spec = _read_yaml(path.read_bytes(), relative)
        if not isinstance(spec, dict):
            continue
        directory = PurePosixPath(relative).parent.as_posix()
        for _, identity in _environment_references(spec, directory):
            root = _package_root_from_reference(identity)
            if root is not None:
                roots.add(root)
    return sorted(roots, key=lambda item: (item.casefold(), item))


def _is_eval_spec_name(name: str) -> bool:
    folded = name.casefold()
    return folded in {"eval.yaml", "eval.yml"} or folded.endswith((".eval.yaml", ".eval.yml"))


def verify_prepared_fixture_dependencies(
    upstream_dir: Path,
    prepared_root: Path,
    *,
    expected_upstream_sha: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Re-verify a prepared root against the exact upstream lock and SRI proofs."""
    upstream = _input_directory(upstream_dir, "Upstream source")
    prepared = _input_directory(prepared_root, "Prepared fixture dependency root")
    if prepared == upstream or prepared.is_relative_to(upstream) or upstream.is_relative_to(prepared):
        raise PortError("Prepared fixture dependencies and upstream source must use separate roots.")
    head = _run_git(upstream, "rev-parse", "HEAD").decode("ascii", errors="strict").strip().lower()
    if expected_upstream_sha is not None and head != expected_upstream_sha.lower():
        raise PortError("Upstream Git HEAD does not match the prepared dependency receipt.")
    pinned, package_bytes, lock_bytes = _pinned_upstream(upstream, head)
    lock = _package_lock(package_bytes, lock_bytes)
    receipt_path = safe_path(prepared, RECEIPT_NAME)
    if _reparse(receipt_path) or not receipt_path.is_file() or receipt_path.stat().st_size > _MAX_JSON_BYTES:
        raise PortError("Prepared fixture dependency receipt is missing, unsafe, or too large.")
    receipt = _json_object(receipt_path.read_bytes(), "prepared fixture dependency receipt")
    schema_version = receipt.get("schema_version")
    expected_receipt_fields = {"schema_version", "source", "verification", "packages", "summary"}
    if schema_version == _COMPARE_SCHEMA_VERSION:
        expected_receipt_fields.update({"extra_roots", "selected_optional_roots"})
    elif schema_version != _SCHEMA_VERSION:
        raise PortError("Prepared fixture dependency receipt has an unsupported schema version.")
    if set(receipt) != expected_receipt_fields:
        raise PortError("Prepared fixture dependency receipt contains unknown or missing fields.")
    source = receipt.get("source")
    if (
        schema_version not in {_SCHEMA_VERSION, _COMPARE_SCHEMA_VERSION}
        or receipt.get("verification") != "npm-lock-sri-tarball-byte-match"
        or not isinstance(source, dict)
        or source.get("upstream_sha") != pinned
        or source.get("package_json_sha256") != digest(package_bytes)
        or source.get("package_lock_sha256") != digest(lock_bytes)
        or set(source) != {"upstream_sha", "package_json_sha256", "package_lock_sha256"}
    ):
        raise PortError("Prepared fixture dependency receipt does not match the pinned upstream manifests.")
    extra_roots = receipt.get("extra_roots", [])
    if schema_version == _SCHEMA_VERSION:
        if extra_roots:
            raise PortError("Legacy fixture dependency receipts cannot request extra package roots.")
        extra_roots = []
        selected_optional_roots: set[str] | None = None
    else:
        selected_optional = receipt.get("selected_optional_roots")
        if (
            not isinstance(extra_roots, list)
            or extra_roots != [_VALLY_CLI_LOCK_PATH]
            or _vally_cli_compare_root(package_bytes, lock) != _VALLY_CLI_LOCK_PATH
            or not isinstance(selected_optional, list)
            or any(not isinstance(item, str) for item in selected_optional)
            or selected_optional != sorted(set(selected_optional), key=lambda item: (item.casefold(), item))
        ):
            raise PortError("Compare dependency receipt does not describe the pinned Vally CLI closure exactly.")
        selected_optional_roots = set(selected_optional)
    roots = [*_declared_fixture_package_roots(upstream), *extra_roots]
    roots = sorted(set(roots), key=lambda item: (item.casefold(), item))
    closure = _dependency_closure(lock, roots, include_optional_roots=selected_optional_roots)
    closure_paths = [path for path, _ in closure]
    records = receipt.get("packages")
    if not isinstance(records, list) or len(records) != len(closure):
        raise PortError("Prepared fixture dependency receipt has an incomplete package closure.")
    summary = receipt.get("summary")
    if (
        not isinstance(summary, dict)
        or set(summary) != {"package_count", "file_count", "proof_tarball_count", "scripts_executed", "network_accessed"}
        or summary.get("package_count") != len(closure)
        or summary.get("scripts_executed") is not False
        or summary.get("network_accessed") is not False
    ):
        raise PortError("Prepared fixture dependency summary is malformed or incomplete.")
    by_path = {item.get("lock_path"): item for item in records if isinstance(item, dict)}
    if len(by_path) != len(records) or set(by_path) != set(closure_paths):
        raise PortError("Prepared fixture dependency package records do not match the lockfile closure.")
    total_bytes = 0
    used_proofs: set[str] = set()
    for lock_path, entry in closure:
        record = by_path[lock_path]
        if set(record) != {"name", "version", "lock_path", "resolved_sha256", "lock_integrity", "tarball", "files"}:
            raise PortError(f"Prepared fixture dependency record has unknown fields: {lock_path}.")
        raw_tarball_record = record.get("tarball")
        if not isinstance(raw_tarball_record, dict):
            raise PortError(f"Prepared fixture dependency has no SRI proof: {lock_path}.")
        if set(raw_tarball_record) != {"path", "size", "sha256", "integrity"}:
            raise PortError(f"Prepared fixture dependency SRI proof has unknown fields: {lock_path}.")
        integrity = entry.get("integrity")
        selected = raw_tarball_record.get("integrity")
        tokens = _sri_tokens(integrity)
        selected_token = next((item for item in tokens if f"{item[0]}-{item[1]}" == selected), None)
        if selected_token is None:
            raise PortError(f"Prepared fixture dependency SRI is not declared by package-lock: {lock_path}.")
        proof_path = f".proof/tarballs/{selected_token[0]}-{selected_token[2].hex()}.tgz"
        if raw_tarball_record.get("path") != proof_path:
            raise PortError(f"Prepared fixture dependency SRI proof path is invalid: {lock_path}.")
        proof_file = safe_path(prepared, proof_path)
        if _reparse(proof_file) or not proof_file.is_file() or proof_file.stat().st_size > _MAX_TARBALL_BYTES:
            raise PortError(f"Prepared fixture dependency SRI proof is missing or unsafe: {lock_path}.")
        raw_tarball = proof_file.read_bytes()
        if (
            len(raw_tarball) != raw_tarball_record.get("size")
            or digest(raw_tarball) != raw_tarball_record.get("sha256")
            or _ALGORITHMS[selected_token[0]](raw_tarball).digest() != selected_token[2]
        ):
            raise PortError(f"Prepared fixture dependency SRI proof bytes do not match: {lock_path}.")
        used_proofs.add(proof_path)
        members = _archive_members(raw_tarball, lock_path)
        name, version = _package_meta(lock_path, entry, members)
        if (
            record.get("name") != name
            or record.get("version") != version
            or record.get("lock_integrity") != integrity
            or record.get("resolved_sha256") != digest(entry["resolved"].encode("utf-8"))
        ):
            raise PortError(f"Prepared fixture dependency package metadata differs from package-lock: {lock_path}.")
        expected_files = [
            {"path": f"{lock_path}/{path}", "size": len(raw), "sha256": digest(raw)}
            for path, raw in members
        ]
        actual_files = record.get("files")
        if (
            not isinstance(actual_files, list)
            or any(not isinstance(item, dict) or set(item) != {"path", "size", "sha256"} for item in actual_files)
            or actual_files != expected_files
        ):
            raise PortError(f"Prepared fixture dependency file receipt differs from SRI tarball: {lock_path}.")
        _reject_reparse_chain(safe_path(prepared, lock_path), f"Prepared fixture package {lock_path}")
        _compare_installed_tree(prepared, lock_path, members, closure_paths)
        for path, raw in members:
            total_bytes += len(raw)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise PortError("Prepared fixture dependency root exceeds the total size limit.")
            installed_path = safe_path(prepared, f"{lock_path}/{path}")
            if installed_path.stat().st_size != len(raw) or installed_path.read_bytes() != raw:
                raise PortError(f"Prepared fixture dependency file differs from its SRI tarball: {lock_path}/{path}.")
    proof_root = safe_path(prepared, ".proof/tarballs")
    if _reparse(proof_root) or not proof_root.is_dir():
        raise PortError("Prepared fixture dependency proof directory is missing or unsafe.")
    actual_proofs = _tree_files(proof_root, "SRI proof directory")
    expected_proof_files: dict[str, tuple[int, str]] = {}
    for proof in used_proofs:
        relative = PurePosixPath(proof).name
        path = safe_path(prepared, proof)
        raw = path.read_bytes()
        expected_proof_files[relative] = (len(raw), digest(raw))
    if actual_proofs != expected_proof_files:
        raise PortError("Prepared fixture dependency proof directory contains missing or extra files.")
    if (
        summary.get("file_count") != sum(len(by_path[path].get("files", [])) for path in closure_paths)
        or summary.get("proof_tarball_count") != len(used_proofs)
    ):
        raise PortError("Prepared fixture dependency summary counts do not match its contents.")
    # Reject files outside the verified package closure, receipt, and SRI proof.
    allowed_roots = ["node_modules", ".proof", RECEIPT_NAME]
    for child in prepared.iterdir():
        if child.name not in allowed_roots:
            raise PortError("Prepared fixture dependency root contains an undeclared top-level entry.")
        if _reparse(child):
            raise PortError("Prepared fixture dependency root contains a symlink or reparse point.")
    package_node_modules = safe_path(prepared, "node_modules")
    if _reparse(package_node_modules) or not package_node_modules.is_dir():
        raise PortError("Prepared fixture dependency node_modules tree is missing or unsafe.")
    actual_package_files = _tree_files(package_node_modules, "prepared node_modules")
    expected_package_files: dict[str, tuple[int, str]] = {}
    for lock_path, entry in closure:
        raw_tarball_record = by_path[lock_path]["tarball"]
        raw_tarball = safe_path(prepared, raw_tarball_record["path"]).read_bytes()
        for relative, raw in _archive_members(raw_tarball, lock_path):
            expected_package_files[f"{lock_path.removeprefix('node_modules/')}/{relative}"] = (len(raw), digest(raw))
    if actual_package_files != expected_package_files:
        raise PortError("Prepared fixture dependency node_modules tree contains missing or extra files.")
    return receipt, prepared


def _tarball_copy_record(receipt_package: dict[str, Any]) -> dict[str, Any]:
    return dict(receipt_package["tarball"])


__all__ = [
    "RECEIPT_NAME",
    "package_roots_from_references",
    "prepare_locked_fixture_dependencies",
    "verify_prepared_fixture_dependencies",
]
