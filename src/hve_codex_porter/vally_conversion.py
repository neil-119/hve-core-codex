"""Deterministically adapt Vally evaluation specs to the Codex executor.

The conversion preserves the spec tree and changes only executor declarations
and environment artifact references. Environment sources are resolved from the
Vally spec directory and must exist in the converter's source-to-output
manifest; this module never guesses artifact paths or executes a suite.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from pathlib import PurePosixPath
from typing import Any

from .common import PortError


_UPSTREAM_EXECUTOR = "copilot-sdk"
_TARGET_EXECUTOR = "codex"
_PORTABLE_CODEX_EXECUTOR_OPTIONS = {"binary", "sandbox", "reasoningEffort"}
_PLUGIN_RESOURCE_SOURCE = "plugins/hve-core-codex/resources"
_PLUGIN_RESOURCE_DESTINATIONS = (
    ".agents/resources",
    ".codex/hve-core-codex/plugin-resources/resources",
)
_SKIPPED_SUBTREES = {
    "assertions", "fixtures", "graders", "runs", "scoring", "tags", "threshold", "thresholds",
}


def _spec_directory_parts(spec_directory: str) -> list[str]:
    if not isinstance(spec_directory, str) or not spec_directory:
        raise PortError("Vally conversion requires the spec's upstream-root-relative directory.")
    if "\\" in spec_directory or ":" in spec_directory or "\x00" in spec_directory:
        raise PortError(f"Unsafe Vally spec directory: {spec_directory!r}")
    if spec_directory.startswith("/"):
        raise PortError(f"Vally spec directory must be upstream-root-relative: {spec_directory!r}")
    base_parts: list[str] = []
    for part in spec_directory.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise PortError(f"Vally spec directory must be upstream-root-relative: {spec_directory!r}")
        base_parts.append(part)
    return base_parts


def _relative_source_path(reference: str, spec_directory: str, *, field: str) -> str:
    """Resolve one Vally path lexically to an upstream-root-relative identity."""
    if not isinstance(reference, str) or not reference:
        raise PortError(f"Vally {field} has an empty or invalid reference.")
    if "\\" in reference or ":" in reference or "\x00" in reference:
        raise PortError(f"Unsafe Vally {field} reference: {reference!r}")

    base_parts = _spec_directory_parts(spec_directory)

    if reference.startswith("/"):
        raise PortError(f"Vally {field} reference must be relative: {reference!r}")
    parts = base_parts
    for part in reference.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise PortError(f"Vally {field} reference escapes the upstream root: {reference!r}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise PortError(f"Vally {field} reference resolves to the upstream root: {reference!r}")
    return "/".join(parts)


def _manifest_resource(
    source_identity: str,
    artifact_manifest: Mapping[str, Any],
    *,
    original_reference: str,
    field: str,
) -> str:
    """Return the unique converted resource path recorded by the manifest."""
    entry = artifact_manifest.get(source_identity)
    if not isinstance(entry, Mapping):
        raise PortError(
            f"Unmapped Vally {field} reference {original_reference!r}: "
            f"source identity {source_identity!r} is absent from the artifact manifest."
        )
    resources = entry.get("resources")
    if not isinstance(resources, list) or len(resources) != 1 or not isinstance(resources[0], str):
        raise PortError(
            f"Unmapped Vally {field} reference {original_reference!r}: "
            f"manifest entry {source_identity!r} must have exactly one resource artifact."
        )
    artifact = resources[0]
    artifact_parts = artifact.split("/")
    if (
        not artifact
        or artifact.startswith("/")
        or "\\" in artifact
        or ":" in artifact
        or "\x00" in artifact
        or any(part in {"", ".", ".."} for part in artifact_parts)
    ):
        raise PortError(
            f"Invalid converted resource path for Vally {field} reference "
            f"{original_reference!r}: {artifact!r}."
        )
    return artifact


def _native_agent_profile(
    source_identity: str,
    artifact_manifest: Mapping[str, Any],
) -> str | None:
    """Return the unique native Codex role profile emitted for a source agent."""
    if not source_identity.casefold().endswith(".agent.md"):
        return None
    entry = artifact_manifest.get(source_identity)
    outputs = entry.get("outputs") if isinstance(entry, Mapping) else None
    if not isinstance(outputs, list):
        return None

    profiles: list[str] = []
    for output in outputs:
        if not isinstance(output, str):
            continue
        if not output.startswith("codex-overlay/agents/") or not output.endswith(".toml"):
            continue
        parts = output.split("/")
        if (
            len(parts) != 3
            or parts[:2] != ["codex-overlay", "agents"]
            or not parts[2]
            or parts[2] in {".", ".."}
            or "\\" in output
            or ":" in output
            or "\x00" in output
        ):
            raise PortError(
                f"Invalid native Codex role profile output for Vally agent source "
                f"{source_identity!r}: {output!r}."
            )
        if output not in profiles:
            profiles.append(output)
    if len(profiles) > 1:
        raise PortError(
            f"Ambiguous native Codex role profiles for Vally agent source "
            f"{source_identity!r}: {profiles!r}."
        )
    return profiles[0] if profiles else None


def _convert_environment_files(
    files: Any,
    artifact_manifest: Mapping[str, Any],
    spec_directory: str,
    *,
    location: str,
    stage_agent_profiles: bool = False,
) -> None:
    if files is None:
        return
    if not isinstance(files, list):
        raise PortError(f"Vally {location}.files must be a list of source/destination mappings.")
    profiles: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, MutableMapping):
            raise PortError(f"Vally {location}.files[{index}] must be a mapping.")
        # Validate destinations even when no generated file will be staged.
        # Keep the authored spelling in the spec; the normalized key is only
        # used for safe, platform-independent collision checks.
        _normalized_destination(item.get("dest"))
        reference = item.get("src")
        if not isinstance(reference, str):
            raise PortError(f"Vally {location}.files[{index}].src must be a source path.")
        identity = _relative_source_path(reference, spec_directory, field="file")
        profile = (
            _native_agent_profile(identity, artifact_manifest)
            if stage_agent_profiles
            else None
        )
        item["src"] = _manifest_resource(
            identity,
            artifact_manifest,
            original_reference=reference,
            field="file",
        )
        if profile is not None:
            profiles.add(profile)

    # The Markdown resource remains at its authored destination. A companion
    # file puts the already-generated native profile where Codex discovers
    # project roles when this runtime environment is staged.
    profile_destinations: dict[str, str] = {}
    for profile in sorted(profiles, key=lambda value: (value.casefold(), value)):
        destination = f".codex/agents/{PurePosixPath(profile).name}"
        destination_key = _normalized_destination(destination)
        assert destination_key is not None
        prior_profile = profile_destinations.get(destination_key)
        if prior_profile is not None and prior_profile != profile:
            raise PortError(
                f"Vally {location}.files maps multiple native Codex role profiles "
                f"to the same destination {destination!r}: {prior_profile!r} and {profile!r}."
            )
        profile_destinations[destination_key] = profile

        already_staged = False
        for item in files:
            existing_destination = _normalized_destination(item.get("dest"))
            if existing_destination is None or not _destinations_overlap(
                existing_destination, destination_key
            ):
                continue
            if item.get("src") == profile and item.get("dest") == destination:
                already_staged = True
                continue
            raise PortError(
                f"Vally {location}.files destination {item.get('dest')!r} conflicts with "
                f"native Codex role profile staging for {profile!r} at {destination!r}."
            )
        if not already_staged:
            files.append({"src": profile, "dest": destination})


def _skill_resource_directory(resource: str, *, original_reference: str) -> tuple[str, str]:
    """Return the converted skill directory and its Codex auto-discovery destination."""
    path = PurePosixPath(resource)
    if path.name.casefold() != "skill.md" or len(path.parts) < 2:
        raise PortError(
            f"Converted Vally skill {original_reference!r} must map to a manifest resource "
            f"ending in <skill-name>/SKILL.md, got {resource!r}."
        )
    skill_name = path.parent.name
    if skill_name in {"", ".", ".."}:
        raise PortError(f"Converted Vally skill has an invalid skill directory: {resource!r}.")
    return path.parent.as_posix(), f".agents/skills/{skill_name}"


def _normalized_destination(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    if "\x00" in value or ":" in normalized or normalized.startswith("/"):
        raise PortError(f"Unsafe Vally destination path: {value!r}")

    parts: list[str] = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise PortError(f"Unsafe Vally destination path escapes the workspace: {value!r}")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts).casefold()


def _overlapping_destination_matches_skill(
    item: Mapping[str, Any],
    skill_source: str,
    skill_destination: str,
) -> bool:
    """Check whether an overlapping file mapping is the identical skill tree."""
    destination = _normalized_destination(item.get("dest"))
    source = _normalized_destination(item.get("src"))
    staged = _normalized_destination(skill_destination)
    source_root = _normalized_destination(skill_source)
    if destination is None or source is None or staged is None or source_root is None:
        return False
    if destination == staged:
        return source == source_root
    if destination.startswith(f"{staged}/"):
        suffix = destination[len(staged) + 1:]
        return source == f"{source_root}/{suffix}"
    return False


def _destinations_overlap(left: str, right: str) -> bool:
    """Treat equal paths and either path being an ancestor as an overlap."""
    return (
        left == right
        or left == ""
        or right == ""
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
    )


def _stage_plugin_resource_directories(files: list[Any], *, location: str) -> None:
    """Stage the package resource tree at the plugin's runtime reference roots."""
    if not any(
        isinstance(item, Mapping)
        and isinstance(item.get("src"), str)
        and (
            item["src"] == _PLUGIN_RESOURCE_SOURCE
            or item["src"].startswith(f"{_PLUGIN_RESOURCE_SOURCE}/")
        )
        for item in files
    ):
        return

    for destination in _PLUGIN_RESOURCE_DESTINATIONS:
        destination_key = _normalized_destination(destination)
        source_root = _PLUGIN_RESOURCE_SOURCE
        assert destination_key is not None
        colliding = [
            item
            for item in files
            if isinstance(item, Mapping)
            and (item_destination := _normalized_destination(item.get("dest"))) is not None
            and _destinations_overlap(item_destination, destination_key)
        ]
        if colliding:
            if any(
                not _overlapping_destination_matches_skill(item, source_root, destination)
                for item in colliding
            ):
                raise PortError(
                    f"Vally {location}.files plugin resource destination {destination!r} "
                    "conflicts with an existing environment.files entry."
                )
            if any(
                _normalized_destination(item.get("dest")) == destination_key
                for item in colliding
            ):
                continue
        files.append({"src": source_root, "dest": destination})


def _convert_environment(
    environment: MutableMapping[str, Any],
    artifact_manifest: Mapping[str, Any],
    spec_directory: str,
    *,
    location: str,
) -> None:
    skills = environment.get("skills")
    converted_skills: list[tuple[str, str]] = []
    if skills is not None:
        if not isinstance(skills, list):
            raise PortError(f"Vally {location}.skills must be a list of source paths.")
        for reference in skills:
            if not isinstance(reference, str):
                raise PortError(f"Vally {location}.skills contains a non-string reference.")
            identity = _relative_source_path(reference, spec_directory, field="skill")
            if identity.rsplit("/", 1)[-1].casefold() != "skill.md":
                identity = f"{identity.rstrip('/')}/SKILL.md"
            resource = _manifest_resource(
                identity,
                artifact_manifest,
                original_reference=reference,
                field="skill",
            )
            converted_skills.append((reference, resource))
        environment["skills"] = [
            _skill_resource_directory(resource, original_reference=reference)[0]
            for reference, resource in converted_skills
        ]

    files = environment.get("files")
    _convert_environment_files(
        files,
        artifact_manifest,
        spec_directory,
        location=location,
        stage_agent_profiles=True,
    )

    if files:
        _stage_plugin_resource_directories(files, location=location)

    # Vally keeps its own skill list for prompt-time skill loading. Codex also
    # needs the complete converted skill directories in its native discovery tree.
    # These additions are part of the converted spec, so the evaluator stages
    # them before it takes the workspace baseline.
    if converted_skills:
        if files is None:
            files = []
            environment["files"] = files
        staged_destinations: dict[str, str] = {}
        for reference, resource in converted_skills:
            source_directory, destination = _skill_resource_directory(
                resource,
                original_reference=reference,
            )
            prior_source = staged_destinations.get(destination)
            if prior_source is not None and prior_source != source_directory:
                raise PortError(
                    f"Vally skills map to the same Codex destination {destination!r} "
                    f"from different directories {prior_source!r} and {source_directory!r}."
                )
            staged_destinations[destination] = source_directory

        for destination, source_directory in staged_destinations.items():
            destination_key = _normalized_destination(destination)
            colliding = []
            for item in files:
                item_destination = _normalized_destination(item.get("dest"))
                if item_destination is None or destination_key is None:
                    continue
                if _destinations_overlap(item_destination, destination_key):
                    colliding.append(item)
            if colliding:
                if any(
                    not _overlapping_destination_matches_skill(item, source_directory, destination)
                    for item in colliding
                ):
                    raise PortError(
                        f"Vally skill staging destination {destination!r} conflicts with "
                        "an existing environment.files entry."
                    )
                # An identical authored directory mapping already stages the
                # complete tree. Matching nested file mappings remain intact.
                if any(_normalized_destination(item.get("dest")) == destination_key for item in colliding):
                    continue
            files.append({"src": source_directory, "dest": destination})


def _rewrite_environment_nodes(
    node: Any,
    artifact_manifest: Mapping[str, Any],
    spec_directory: str,
    *,
    location: str,
    active: set[int],
    converted_environments: set[int],
    converted_grading_environments: set[int],
) -> None:
    if isinstance(node, MutableMapping):
        identity = id(node)
        if identity in active:
            raise PortError(f"Recursive Vally data is not supported at {location}.")
        active.add(identity)
        try:
            for key, value in list(node.items()):
                child_location = f"{location}.{key}" if location else str(key)
                if key in {"environment", "agent_environment"}:
                    if not isinstance(value, MutableMapping):
                        raise PortError(f"Vally {child_location} must be a mapping.")
                    if id(value) not in converted_environments:
                        _convert_environment(value, artifact_manifest, spec_directory, location=child_location)
                        converted_environments.add(id(value))
                    continue
                if key == "grading_environment":
                    if not isinstance(value, MutableMapping):
                        raise PortError(f"Vally {child_location} must be a mapping.")
                    if id(value) not in converted_grading_environments:
                        _convert_environment_files(
                            value.get("files"),
                            artifact_manifest,
                            spec_directory,
                            location=child_location,
                        )
                        converted_grading_environments.add(id(value))
                    continue
                if str(key).casefold() in _SKIPPED_SUBTREES:
                    continue
                _rewrite_environment_nodes(
                    value,
                    artifact_manifest,
                    spec_directory,
                    location=child_location,
                    active=active,
                    converted_environments=converted_environments,
                    converted_grading_environments=converted_grading_environments,
                )
        finally:
            active.remove(identity)
    elif isinstance(node, list):
        identity = id(node)
        if identity in active:
            raise PortError(f"Recursive Vally data is not supported at {location}.")
        active.add(identity)
        try:
            for index, value in enumerate(node):
                _rewrite_environment_nodes(
                    value,
                    artifact_manifest,
                    spec_directory,
                    location=f"{location}[{index}]",
                    active=active,
                    converted_environments=converted_environments,
                    converted_grading_environments=converted_grading_environments,
                )
        finally:
            active.remove(identity)


def _convert_executor(value: Any, *, location: str) -> Any:
    if value is None:
        return value
    if isinstance(value, str):
        if value == _TARGET_EXECUTOR:
            return value
        if value == _UPSTREAM_EXECUTOR:
            return _TARGET_EXECUTOR
        raise PortError(f"Unsupported Vally executor at {location}: {value!r}.")
    if isinstance(value, MutableMapping):
        name = value.get("name")
        if name != _UPSTREAM_EXECUTOR and name != _TARGET_EXECUTOR:
            raise PortError(f"Unsupported Vally executor at {location}.name: {name!r}.")
        if "config" in value:
            config = value["config"]
            if not isinstance(config, Mapping):
                raise PortError(
                    f"Vally {location}.config must be a mapping for the Codex executor."
                )
            unsupported = sorted(set(config) - _PORTABLE_CODEX_EXECUTOR_OPTIONS, key=str)
            if unsupported:
                raise PortError(
                    f"Vally {location}.config contains options not portable to Codex: {unsupported!r}; "
                    "Copilot provider configuration cannot be discarded during executor conversion."
                )
            if "binary" in config and (not isinstance(config["binary"], str) or not config["binary"]):
                raise PortError(f"Vally {location}.config.binary must be a nonempty executable path.")
            if "sandbox" in config and config["sandbox"] not in {"read-only", "workspace-write"}:
                raise PortError(f"Vally {location}.config.sandbox is not supported by the Codex executor.")
            if "reasoningEffort" in config and config["reasoningEffort"] not in {
                "low", "medium", "high", "xhigh", "max",
            }:
                raise PortError(
                    f"Vally {location}.config.reasoningEffort is not supported by the Codex executor."
                )
        if name == _UPSTREAM_EXECUTOR:
            value["name"] = _TARGET_EXECUTOR
        return value
    raise PortError(f"Unsupported Vally executor at {location}: {value!r}.")


def _rewrite_stimulus_executors(node: Any, *, location: str, active: set[int]) -> None:
    if isinstance(node, MutableMapping):
        identity = id(node)
        if identity in active:
            raise PortError(f"Recursive Vally stimulus data is not supported at {location}.")
        active.add(identity)
        try:
            if "executor" in node:
                node["executor"] = _convert_executor(node["executor"], location=f"{location}.executor")
            if "supported_executors" in node:
                supported = node["supported_executors"]
                if not isinstance(supported, list) or any(not isinstance(name, str) for name in supported):
                    raise PortError(f"Vally {location}.supported_executors must be a list of strings.")
                node["supported_executors"] = [
                    _TARGET_EXECUTOR if name == _UPSTREAM_EXECUTOR else name
                    for name in supported
                ]
            for key, value in list(node.items()):
                if str(key).casefold() in _SKIPPED_SUBTREES or key in {
                    "environment", "agent_environment", "grading_environment", "executor",
                }:
                    continue
                _rewrite_stimulus_executors(
                    value,
                    location=f"{location}.{key}",
                    active=active,
                )
        finally:
            active.remove(identity)
    elif isinstance(node, list):
        identity = id(node)
        if identity in active:
            raise PortError(f"Recursive Vally stimulus data is not supported at {location}.")
        active.add(identity)
        try:
            for index, value in enumerate(node):
                _rewrite_stimulus_executors(value, location=f"{location}[{index}]", active=active)
        finally:
            active.remove(identity)


def convert_vally_spec(
    spec: Mapping[str, Any],
    artifact_manifest: Mapping[str, Any],
    *,
    spec_directory: str,
) -> dict[str, Any]:
    """Return a Codex-adapted deep copy of a Vally evaluation spec.

    ``artifact_manifest`` is the source-to-output manifest produced by the
    porter conversion, keyed by upstream-root-relative source identities. Its
    ``resources`` entries map authored environment files and skill procedures;
    an agent source may additionally emit a native role companion from its
    ``outputs`` entry. ``spec_directory`` is the upstream-root-relative
    directory from which Vally resolves the spec's environment paths (for
    example ``evals/agent-behavior``).

    Stimuli, graders, scoring thresholds, run counts, fixtures, tags, and other
    fields are retained by deep copy. Only executor declarations and
    environment ``files[*].src`` / ``skills[*]`` references are transformed;
    runtime agent files may receive a native-role companion mapping. Known
    repository-contract suites also receive generated repository context when
    a stimulus omits its own environment; empty nominal baselines stay isolated.
    """
    if not isinstance(spec, Mapping):
        raise PortError("Vally spec must be a mapping.")
    if not isinstance(artifact_manifest, Mapping):
        raise PortError("Vally conversion requires the source-to-output artifact manifest.")
    if not isinstance(spec_directory, str) or not spec_directory:
        raise PortError("Vally conversion requires the spec's upstream-root-relative directory.")
    _spec_directory_parts(spec_directory)

    converted = copy.deepcopy(dict(spec))
    if "executor" in converted:
        converted["executor"] = _convert_executor(converted["executor"], location="executor")
    defaults = converted.get("defaults")
    if defaults is not None:
        if not isinstance(defaults, MutableMapping):
            raise PortError("Vally defaults must be a mapping when present.")
        if "executor" in defaults:
            defaults["executor"] = _convert_executor(defaults["executor"], location="defaults.executor")
    if "stimuli" in converted:
        _rewrite_stimulus_executors(converted["stimuli"], location="stimuli", active=set())
    _rewrite_environment_nodes(
        converted,
        artifact_manifest,
        spec_directory,
        location="",
        active=set(),
        converted_environments=set(),
        converted_grading_environments=set(),
    )
    # Repository-contract suites omit environments because their host normally
    # provides the HVE repository. Recreate that context from generated resources,
    # never from ambient user files, and keep authored prompts/graders untouched.
    has_repository_resources = any(
        str(resource).startswith("plugins/hve-core-codex/resources/.github/")
        for record in artifact_manifest.values() if isinstance(record, Mapping)
        for resource in record.get("resources", [])
    )
    repository_skill_files = []
    for identity, record in sorted(artifact_manifest.items()):
        if str(identity).startswith(".github/skills/") and str(identity).endswith("/SKILL.md") and isinstance(record, Mapping):
            for resource in record.get("resources", []):
                if str(resource).startswith("plugins/hve-core-codex/skills/") and str(resource).endswith("/SKILL.md"):
                    repository_skill_files.append({"src": str(PurePosixPath(resource).parent), "dest": str(PurePosixPath(identity).parent)})
                    break
    repository_support_files = []
    for directory in ("scripts", "schemas"):
        prefix = f"plugins/hve-core-codex/resources/{directory}/"
        if any(str(resource).startswith(prefix) for record in artifact_manifest.values() if isinstance(record, Mapping) for resource in record.get("resources", [])):
            repository_support_files.append({"src": prefix.rstrip("/"), "dest": directory})
    repository_contract = str(converted.get("name", "")).startswith("agent-conformance-") or converted.get("name") in {
        "agent-behavior", "behavior-conformance-instructions", "behavior-conformance-skills",
    }
    if repository_contract and has_repository_resources:
        for stimulus in converted.get("stimuli", []):
            if not stimulus.get("environment") and not stimulus.get("agent_environment"):
                stimulus["environment"] = {"files": [
                    {"src": "plugins/hve-core-codex/resources/.github", "dest": ".github"},
                    {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
                    {"src": "plugins/hve-core-codex/resources", "dest": ".codex/hve-core-codex/plugin-resources/resources"},
                ] + repository_skill_files + repository_support_files}
    return converted


__all__ = ["convert_vally_spec"]
