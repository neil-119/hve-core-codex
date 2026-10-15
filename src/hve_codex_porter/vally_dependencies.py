"""Supplement Vally runtime environments with known artifact dependencies.

The generated artifact inventory is the only source of dependency edges. This module
does not inspect file contents, infer links, or execute repository scripts.
"""
from __future__ import annotations

from bisect import bisect_left
import posixpath
from collections.abc import Mapping, MutableMapping
from typing import Any

from .common import PortError
from .vally_conversion import (
    _manifest_resource,
    _normalized_destination,
    _relative_source_path,
    _spec_directory_parts,
)


_SKIPPED_SUBTREES = {
    "assertions",
    "fixtures",
    "grading_environment",
    "graders",
    "runs",
    "scoring",
    "tags",
    "threshold",
    "thresholds",
}
_MAX_ARTIFACTS = 50_000
_MAX_DEPENDENCIES = 250_000
_MAX_MANIFEST_ENTRIES = 250_000
_MAX_DIRECTORY_MEMBERS = 10_000
_MAX_DOCUMENT_NODES = 100_000
_MAX_DOCUMENT_DEPTH = 256
_PLUGIN_RESOURCE_PREFIX = "plugins/hve-core-codex/"
_SUPPLEMENTAL_RESOURCE_ROOT = ".codex/hve-core-codex/plugin-resources"


def _canonical_source(reference: str, *, field: str) -> str:
    """Normalize an upstream-root-relative artifact path."""
    return _relative_source_path(reference, ".", field=field)


def _source_relative_to_spec(source_identity: str, spec_directory: str) -> str:
    base = "/".join(_spec_directory_parts(spec_directory)) or "."
    return posixpath.relpath(source_identity, start=base)


def _supplemental_destination(source_identity: str, artifact_manifest: Mapping[str, Any]) -> str:
    resource = _manifest_resource(
        source_identity,
        artifact_manifest,
        original_reference=source_identity,
        field="dependency",
    )
    if resource.startswith(_PLUGIN_RESOURCE_PREFIX):
        relative_resource = resource[len(_PLUGIN_RESOURCE_PREFIX):]
    elif resource.startswith(("plugins/", "codex-overlay/")):
        raise PortError(
            f"Vally dependency {source_identity!r} maps outside the HVE plugin resources: "
            f"{resource!r}."
        )
    else:
        # Small test distributions may use a resource path without the full
        # production package prefix. Keep that path beneath the same asset root.
        relative_resource = resource
    if not relative_resource:
        raise PortError(f"Vally dependency {source_identity!r} maps to an empty plugin resource path.")
    return f"{_SUPPLEMENTAL_RESOURCE_ROOT}/{relative_resource}"


def _build_graph(artifacts: Any) -> dict[str, tuple[str | None, tuple[str, ...]]]:
    if isinstance(artifacts, Mapping):
        artifacts = artifacts.get("artifacts")
    if not isinstance(artifacts, (list, tuple)):
        raise PortError("Vally dependency supplementation requires compatibility-report artifacts.")
    if len(artifacts) > _MAX_ARTIFACTS:
        raise PortError("Vally compatibility artifact graph exceeds the supported size.")

    graph: dict[str, tuple[str | None, tuple[str, ...]]] = {}
    edge_count = 0
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            raise PortError(f"Compatibility artifact {index} must be a mapping.")
        source = artifact.get("source")
        if not isinstance(source, str) or not source:
            # A non-source record cannot participate as an artifact graph node.
            continue
        identity = _canonical_source(source, field="artifact source")
        kind_value = artifact.get("kind")
        kind = kind_value.casefold() if isinstance(kind_value, str) else None
        raw_dependencies = artifact.get("dependencies", [])
        if not isinstance(raw_dependencies, list):
            raise PortError(f"Compatibility artifact {identity!r}.dependencies must be a list.")
        edge_count += len(raw_dependencies)
        if edge_count > _MAX_DEPENDENCIES:
            raise PortError("Vally compatibility dependency graph exceeds the supported size.")
        dependencies: list[str] = []
        for dependency in raw_dependencies:
            if not isinstance(dependency, str) or not dependency:
                raise PortError(
                    f"Compatibility artifact {identity!r} contains an invalid dependency path."
                )
            dependencies.append(_canonical_source(dependency, field="artifact dependency"))
        node = (kind, tuple(sorted(set(dependencies), key=lambda path: (path.casefold(), path))))
        prior = graph.get(identity)
        if prior is not None and prior != node:
            raise PortError(f"Conflicting compatibility graph entries for {identity!r}.")
        graph[identity] = node
    return graph


def _manifest_source_index(artifact_manifest: Mapping[str, Any]) -> tuple[str, ...]:
    if len(artifact_manifest) > _MAX_MANIFEST_ENTRIES:
        raise PortError("Vally artifact manifest exceeds the supported size.")
    sources: list[str] = []
    for source in artifact_manifest:
        if not isinstance(source, str) or not source:
            raise PortError("Vally artifact manifest contains an invalid source identity.")
        canonical = _canonical_source(source, field="manifest source")
        if canonical != source:
            raise PortError(f"Vally artifact manifest source is not canonical: {source!r}.")
        sources.append(source)
    return tuple(sorted(sources))


def _manifest_directory_members(
    directory: str,
    manifest_sources: tuple[str, ...],
    cache: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    cached = cache.get(directory)
    if cached is not None:
        return cached
    prefix = directory.rstrip("/") + "/"
    index = bisect_left(manifest_sources, prefix)
    matches: list[str] = []
    while index < len(manifest_sources) and manifest_sources[index].startswith(prefix):
        matches.append(manifest_sources[index])
        if len(matches) > _MAX_DIRECTORY_MEMBERS:
            raise PortError(
                f"Vally directory dependency {directory!r} has too many manifest members."
            )
        index += 1

    # A source-map entry that has descendants denotes a directory mapping.
    # Stage its exact leaf members instead so every added source remains a file.
    leaves: list[str] = []
    for source in matches:
        child_prefix = source + "/"
        child_index = bisect_left(manifest_sources, child_prefix)
        if child_index < len(manifest_sources) and manifest_sources[child_index].startswith(child_prefix):
            continue
        leaves.append(source)
    result = tuple(leaves)
    cache[directory] = result
    return result


def _runtime_environments(document: Any) -> list[MutableMapping[str, Any]]:
    environments: list[MutableMapping[str, Any]] = []
    seen_environments: set[int] = set()
    active: set[int] = set()
    node_count = 0

    def add_environment(value: Any, location: str) -> None:
        if not isinstance(value, MutableMapping):
            raise PortError(f"Vally {location} must be a mutable mapping.")
        identity = id(value)
        if identity not in seen_environments:
            seen_environments.add(identity)
            environments.append(value)

    def walk(node: Any, location: str, *, root: bool = False, depth: int = 0) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > _MAX_DOCUMENT_NODES:
            raise PortError("Vally document is too large to inspect for runtime environments.")
        if depth > _MAX_DOCUMENT_DEPTH:
            raise PortError("Vally document is too deeply nested to inspect for runtime environments.")
        if isinstance(node, Mapping):
            identity = id(node)
            if identity in active:
                raise PortError(f"Recursive Vally YAML data is not supported at {location}.")
            active.add(identity)
            try:
                for key, value in node.items():
                    key_name = str(key).casefold()
                    child_location = f"{location}.{key}" if location else str(key)
                    if key_name in _SKIPPED_SUBTREES:
                        continue
                    if key_name in {"environment", "agent_environment"}:
                        add_environment(value, child_location)
                        continue
                    if root and key_name == "environments":
                        if not isinstance(value, Mapping):
                            raise PortError("Vally .vally.yaml environments must be a mapping.")
                        for name, environment in value.items():
                            add_environment(environment, f"environments.{name}")
                        continue
                    walk(value, child_location, depth=depth + 1)
            finally:
                active.remove(identity)
        elif isinstance(node, list):
            identity = id(node)
            if identity in active:
                raise PortError(f"Recursive Vally YAML data is not supported at {location}.")
            active.add(identity)
            try:
                for index, value in enumerate(node):
                    walk(value, f"{location}[{index}]", depth=depth + 1)
            finally:
                active.remove(identity)

    walk(document, "", root=True)
    return environments


def _explicit_roots(
    environment: MutableMapping[str, Any],
    graph: Mapping[str, tuple[str | None, tuple[str, ...]]],
    spec_directory: str,
) -> set[str]:
    roots: set[str] = set()

    files = environment.get("files")
    if files is not None:
        if not isinstance(files, list):
            raise PortError("Vally runtime environment.files must be a list.")
        for index, item in enumerate(files):
            if not isinstance(item, Mapping):
                raise PortError(f"Vally runtime environment.files[{index}] must be a mapping.")
            reference = item.get("src")
            if not isinstance(reference, str):
                raise PortError(f"Vally runtime environment.files[{index}].src must be a path.")
            identity = _relative_source_path(reference, spec_directory, field="file")
            node = graph.get(identity)
            if node is not None and node[0] == "agent":
                roots.add(identity)

    skills = environment.get("skills")
    if skills is not None:
        if not isinstance(skills, list):
            raise PortError("Vally runtime environment.skills must be a list.")
        for index, reference in enumerate(skills):
            if not isinstance(reference, str):
                raise PortError(f"Vally runtime environment.skills[{index}] must be a path.")
            if reference.rsplit("/", 1)[-1].casefold() == "skill.md":
                identity = _relative_source_path(reference, spec_directory, field="skill")
            else:
                identity = _relative_source_path(
                    f"{reference.rstrip('/')}/SKILL.md",
                    spec_directory,
                    field="skill",
                )
            node = graph.get(identity)
            if node is not None and node[0] == "skill":
                roots.add(identity)
    return roots


def _reachable_dependencies(
    roots: set[str],
    graph: Mapping[str, tuple[str | None, tuple[str, ...]]],
    artifact_manifest: Mapping[str, Any],
    manifest_sources: tuple[str, ...],
) -> set[str]:
    visited: set[str] = set()
    staged: set[str] = set()
    pending = sorted(roots, key=lambda path: (path.casefold(), path), reverse=True)
    directory_cache: dict[str, tuple[str, ...]] = {}
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if len(visited) > _MAX_ARTIFACTS:
            raise PortError("Vally reachable dependency graph exceeds the supported size.")
        node = graph.get(current)
        if node is None:
            continue
        for dependency in node[1]:
            if dependency != current:
                if dependency in artifact_manifest:
                    staged.add(dependency)
                else:
                    members = _manifest_directory_members(
                        dependency,
                        manifest_sources,
                        directory_cache,
                    )
                    if not members:
                        raise PortError(
                            f"Compatibility dependency {dependency!r} referenced by {current!r} "
                            "is absent from the artifact manifest and has no known manifest members."
                        )
                    staged.update(members)
                    for member in members:
                        if member in graph and member not in visited:
                            pending.append(member)
                if len(staged) > _MAX_DEPENDENCIES:
                    raise PortError("Vally reachable dependency graph exceeds the supported size.")
            if dependency in graph and dependency not in visited:
                pending.append(dependency)
    return staged


def supplement_environment_dependencies(
    document: MutableMapping[str, Any],
    *,
    artifact_manifest: Mapping[str, Any],
    artifacts: Any,
    spec_directory: str,
) -> list[str]:
    """Append transitive HVE support files to authored runtime environments.

    Only explicitly declared agent files and skills seed the graph walk. Added
    ``src`` values are relative to ``spec_directory`` and destinations retain
    original identities under the HVE plugin resource root for non-``.github``
    files.
    """
    if not isinstance(document, MutableMapping):
        raise PortError("Vally dependency supplementation requires a mutable document mapping.")
    if not isinstance(artifact_manifest, Mapping):
        raise PortError("Vally dependency supplementation requires an artifact manifest mapping.")
    # Validate the directory even when no roots are found.
    _spec_directory_parts(spec_directory)
    graph = _build_graph(artifacts)
    environments = _runtime_environments(document)
    manifest_sources: tuple[str, ...] | None = None

    plans: list[tuple[MutableMapping[str, Any], list[tuple[str, str, str]]]] = []
    all_added: set[str] = set()
    for environment in environments:
        roots = _explicit_roots(environment, graph, spec_directory)
        if not roots:
            continue
        if manifest_sources is None:
            manifest_sources = _manifest_source_index(artifact_manifest)
        dependencies = _reachable_dependencies(
            roots,
            graph,
            artifact_manifest,
            manifest_sources,
        )
        if not dependencies:
            continue

        files = environment.get("files")
        if files is None:
            files = []
        if not isinstance(files, list):
            raise PortError("Vally runtime environment.files must be a list.")

        destinations: dict[str, set[str | None]] = {}
        for index, item in enumerate(files):
            if not isinstance(item, Mapping):
                raise PortError(f"Vally runtime environment.files[{index}] must be a mapping.")
            source = item.get("src")
            source_identity: str | None = None
            if isinstance(source, str) and source:
                source_identity = _relative_source_path(source, spec_directory, field="file")
            destination_key = _normalized_destination(item.get("dest"))
            if destination_key is None:
                continue
            destinations.setdefault(destination_key, set()).add(source_identity)

        additions: list[tuple[str, str, str]] = []
        for dependency in sorted(dependencies, key=lambda path: (path.casefold(), path)):
            destination = (
                dependency
                if dependency.startswith(".github/")
                else _supplemental_destination(dependency, artifact_manifest)
            )
            destination_key = _normalized_destination(destination)
            assert destination_key is not None
            prior_sources = destinations.get(destination_key)
            if prior_sources is not None:
                if prior_sources == {dependency}:
                    continue
                raise PortError(
                    f"Vally dependency {dependency!r} collides with an authored runtime "
                    f"destination {destination!r}."
                )
            additions.append(
                (_source_relative_to_spec(dependency, spec_directory), destination, dependency)
            )
            destinations[destination_key] = {dependency}

        if additions:
            plans.append((environment, additions))
            all_added.update(identity for _, _, identity in additions)

    for environment, additions in plans:
        files = environment.get("files")
        if files is None:
            files = []
            environment["files"] = files
        files.extend(
            {"src": source, "dest": destination}
            for source, destination, _identity in additions
        )

    return sorted(all_added, key=lambda path: (path.casefold(), path))


__all__ = ["supplement_environment_dependencies"]
