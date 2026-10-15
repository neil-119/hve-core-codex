from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hve_codex_porter.vally_conversion import convert_vally_spec  # noqa: E402
from hve_codex_porter.conversion import convert  # noqa: E402


YAML_READER = YAML(typ="safe")
LEGACY_CASES = {
    "stable": {
        "source": ROOT / "tests/fixtures/legacy-scenarios/scenarios.json",
        "ids": {f"golden-path-{number:02d}" for number in (1, 2, 3, 4, 5, 6, 8)},
        "existing": {"integration-05", "golden-path-09", "golden-path-10"},
        "total": 10,
    },
    "edge": {
        "source": ROOT / "tests/fixtures/legacy-scenarios/edge-scenarios.json",
        "ids": {f"edge-path-{number:02d}" for number in (1, 2, 3, 4, 5, 6, 8, 9, 10)},
        "existing": {"edge-integration-05", "edge-integration-06"},
        "total": 11,
    },
}
BUILTIN_GRADERS = {
    "prompt",
    "file-exists",
    "file-not-exists",
    "file-contains",
    "file-matches",
    "diff-contains",
    "diff-not-contains",
    "diff-empty",
}
REQUIRED_CUSTOM_SKILLS = {
    "edge-path-01": {
        ".github/skills/rpi/rpi-plan",
        ".github/skills/rpi/rpi-plan-critique",
        ".github/skills/rpi/rpi-implement",
    },
    "edge-path-02": {
        ".github/skills/coding-standards/code-review",
        ".github/skills/shared/pr-reference",
    },
    "edge-path-04": {".github/skills/project-planning/requirements-author"},
    "edge-path-05": {".github/skills/project-planning/backlog-management"},
    "edge-path-06": {
        ".github/skills/data-science-engineering/data-science-engineering-foundation"
    },
    "edge-path-08": {
        ".github/skills/design-thinking/dt-coaching-foundation",
        ".github/skills/design-thinking/dt-methods",
    },
    "edge-path-09": {".github/skills/project-planning/backlog-management"},
    "edge-integration-05": {
        ".github/skills/design-thinking/dt-coaching-foundation",
        ".github/skills/design-thinking/dt-methods",
    },
}
REQUIRED_CUSTOM_FILES = {
    "edge-path-02": {
        ".github/instructions/coding-standards/code-review/diff-computation.instructions.md":
            ".github/instructions/coding-standards/code-review/diff-computation.instructions.md",
    },
    "edge-path-03": {
        ".github/instructions/rai-planning/rai-identity.instructions.md":
            ".github/instructions/rai-planning/rai-identity.instructions.md",
        ".github/instructions/shared/planner-identity-base.instructions.md":
            ".github/instructions/shared/planner-identity-base.instructions.md",
        ".github/instructions/shared/disclaimer-language.instructions.md":
            ".github/instructions/shared/disclaimer-language.instructions.md",
        ".github/instructions/rai-planning/rai-license-posture.instructions.md":
            ".github/instructions/rai-planning/rai-license-posture.instructions.md",
    }
}


def _read_legacy(channel: str) -> dict[str, dict[str, Any]]:
    raw = json.loads(LEGACY_CASES[channel]["source"].read_text(encoding="utf-8"))
    return {item["id"]: item for item in raw["scenarios"] if item["id"] in LEGACY_CASES[channel]["ids"]}


def _spec_entries(channel: str) -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    custom_root = ROOT / "tests/vally-custom" / channel
    entries = []
    for path in sorted((custom_root / "evals").rglob("eval.yaml")):
        spec = YAML_READER.load(path.read_text(encoding="utf-8"))
        for stimulus in spec.get("stimuli", []):
            entries.append((path, spec, stimulus))
    return entries


def _resolve_source(reference: str, spec_directory: str) -> str:
    parts = list(PurePosixPath(spec_directory).parts)
    for part in reference.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise AssertionError(f"source reference escapes custom root: {reference}")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _resolved_prompts(case: dict[str, Any]) -> list[str]:
    entrypoints = {item["id"]: item["source"] for item in case["entrypoints"]}
    primary = entrypoints["primary"]
    resolved = []
    for prompt in case["prompts"]:
        prompt = prompt.replace("$actual-entrypoint", primary)
        prompt = re.sub(
            r"\$entrypoint\[([^\]]+)\]",
            lambda match: entrypoints[match.group(1)],
            prompt,
        )
        if "$entrypoint[" in prompt or "$actual-entrypoint" in prompt:
            raise AssertionError(f"unresolved source entrypoint placeholder in {case['id']}")
        resolved.append(prompt)
    return resolved


def _fixture_text(value: Any) -> str:
    if isinstance(value, dict) and set(value) == {"content"}:
        value = value["content"]
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not isinstance(value, str):
        raise AssertionError(f"legacy fixture content is not text: {type(value).__name__}")
    return value


def _environment_nodes(node: Any, path: tuple[str | int, ...] = ()) -> list[tuple[tuple[str | int, ...], dict[str, Any], str]]:
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"environment", "agent_environment", "grading_environment"} and isinstance(value, dict):
                found.append((path + (key,), value, key))
            else:
                found.extend(_environment_nodes(value, path + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_environment_nodes(value, path + (index,)))
    return found


def _value_at_path(value: Any, path: tuple[str | int, ...]) -> Any:
    for part in path:
        value = value[part]
    return value


def _synthetic_artifact_manifest(spec: dict[str, Any], spec_directory: str) -> dict[str, dict[str, list[str]]]:
    manifest = {}

    def add(reference: str) -> None:
        identity = _resolve_source(reference, spec_directory)
        manifest.setdefault(identity, {"resources": [f"resources/{identity}"]})

    for _, environment, kind in _environment_nodes(spec):
        for item in environment.get("files", []):
            add(item["src"])
        if kind != "grading_environment":
            for reference in environment.get("skills", []):
                identity = _resolve_source(reference, spec_directory)
                if not identity.casefold().endswith("/skill.md"):
                    identity = f"{identity.rstrip('/')}/SKILL.md"
                add(identity)
    return manifest


def _required_source_artifacts(case_id: str) -> set[str]:
    skill_entrypoints = {
        f"{skill}/SKILL.md" for skill in REQUIRED_CUSTOM_SKILLS.get(case_id, set())
    }
    return skill_entrypoints | set(REQUIRED_CUSTOM_FILES.get(case_id, {}))


def _fixture_only_manifest(spec: dict[str, Any], spec_directory: str) -> dict[str, dict[str, list[str]]]:
    manifest = {}
    for _, environment, _ in _environment_nodes(spec):
        for item in environment.get("files", []):
            reference = item["src"].replace("\\", "/")
            if reference.startswith("fixtures/"):
                identity = _resolve_source(reference, spec_directory)
                manifest[identity] = {"resources": [f"resources/{identity}"]}
    return manifest


def _build_synthetic_edge_distribution_manifest(root: Path) -> Path:
    """Build converter output for every upstream source referenced by edge specs."""
    upstream = root / "synthetic-upstream"
    skill_sources: set[str] = set()
    agent_sources: set[str] = set()
    instruction_sources: set[str] = set()
    other_sources: set[str] = set()

    for path, spec, _ in _spec_entries("edge"):
        spec_directory = path.parent.relative_to(ROOT / "tests/vally-custom/edge").as_posix()
        for _, environment, _kind in _environment_nodes(spec):
            for item in environment.get("files", []):
                identity = _resolve_source(item["src"], spec_directory)
                if identity.startswith("fixtures/"):
                    continue
                if identity.casefold().endswith("/skill.md"):
                    skill_sources.add(identity)
                elif identity.casefold().endswith(".agent.md"):
                    agent_sources.add(identity)
                elif identity.casefold().endswith(".instructions.md"):
                    instruction_sources.add(identity)
                else:
                    other_sources.add(identity)
            for reference in environment.get("skills", []):
                identity = _resolve_source(reference, spec_directory)
                if not identity.casefold().endswith("/skill.md"):
                    identity = f"{identity.rstrip('/')}/SKILL.md"
                skill_sources.add(identity)

    (upstream / ".github").mkdir(parents=True)
    for source in sorted(skill_sources):
        skill = upstream / Path(*source.split("/"))
        skill.parent.mkdir(parents=True, exist_ok=True)
        name = skill.parent.name.replace("-", " ").title()
        skill.write_text(
            f"---\nname: {name}\ndescription: Synthetic {name} fixture\n---\nSynthetic test source.\n",
            encoding="utf-8",
        )
    for source in sorted(agent_sources):
        agent = upstream / Path(*source.split("/"))
        agent.parent.mkdir(parents=True, exist_ok=True)
        name = PurePosixPath(source).name.removesuffix(".agent.md").replace("-", " ").title()
        agent.write_text(
            f"---\nname: {name}\ndescription: Synthetic {name} fixture\n---\nSynthetic test source.\n",
            encoding="utf-8",
        )
    for source in sorted(instruction_sources):
        instruction = upstream / Path(*source.split("/"))
        instruction.parent.mkdir(parents=True, exist_ok=True)
        instruction.write_text(
            "---\napplyTo: '**/*'\n---\nSynthetic test source.\n",
            encoding="utf-8",
        )
    for source in sorted(other_sources):
        resource = upstream / Path(*source.split("/"))
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_text("Synthetic test resource.\n", encoding="utf-8")

    manifest = {
        "version": "1.0.0",
        "skills": sorted({str(PurePosixPath(source).parent) for source in skill_sources}),
        "agents": sorted(agent_sources),
        "commands": [],
    }
    (upstream / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    distribution = root / "synthetic-distribution"
    convert(
        upstream,
        distribution,
        {"upstream_ref": "fixture", "upstream_sha": "a" * 40, "channel": "edge"},
        ROOT / "mappings",
    )
    return distribution / "generated/source-to-output.json"


class VallyCustomLegacyPreservationTests(unittest.TestCase):
    def test_custom_cases_require_every_grader_without_averaging_away_failed_checks(self) -> None:
        for channel in LEGACY_CASES:
            for path, spec, stimulus in _spec_entries(channel):
                with self.subTest(path=path, stimulus=stimulus["name"]):
                    self.assertNotIn("scoring", spec)
                    self.assertNotIn("scoring", stimulus)
                    prompts = [grader for grader in stimulus["graders"] if grader["type"] == "prompt"]
                    self.assertTrue(prompts)
                    self.assertTrue(all(grader["config"]["threshold"] == 0.7 for grader in prompts))

    def test_custom_eval_counts_include_each_preserved_channel_specific_case(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            with self.subTest(channel=channel):
                entries = _spec_entries(channel)
                legacy_cases = [stimulus.get("tags", {}).get("legacy_case") for _, _, stimulus in entries]
                self.assertEqual(len(entries), contract["total"])
                self.assertEqual(len(legacy_cases), len(set(legacy_cases)))
                self.assertEqual(
                    set(legacy_cases),
                    set(contract["ids"]) | set(contract["existing"]),
                )
                added = [case for case in legacy_cases if case in contract["ids"]]
                self.assertEqual(len(added), 9 if channel == "edge" else 7)
                self.assertTrue(all(spec["defaults"]["runs"] == 1 for _, spec, _ in entries))

    def test_legacy_prompts_rubrics_and_multiturn_order_are_exact(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            with self.subTest(channel=channel):
                cases = _read_legacy(channel)
                entries = {
                    stimulus["tags"]["legacy_case"]: (path, spec, stimulus)
                    for path, spec, stimulus in _spec_entries(channel)
                }
                for case_id, case in cases.items():
                    path, spec, stimulus = entries[case_id]
                    primary = next(item["source"] for item in case["entrypoints"] if item["id"] == "primary")
                    self.assertEqual(stimulus["turns"], [f"Launch {primary}", *_resolved_prompts(case)])
                    self.assertNotIn("prompt", stimulus, f"{case_id} must remain a multi-turn Vally stimulus")
                    self.assertEqual(stimulus["rubric"], case["rubric"])
                    self.assertEqual(stimulus["tags"]["channel"], channel)
                    self.assertEqual(stimulus["tags"]["legacy_title"], case["title"])
                    self.assertEqual(stimulus["tags"]["category"], "codex-port-preservation")
                    self.assertEqual(spec["defaults"]["runs"], 1)
                    self.assertTrue(path.is_file())

    def test_edge_integration_06_preserves_archived_prompt_rubric_sources_and_fixture_bytes(self) -> None:
        archive_path = ROOT / "tests/fixtures/legacy-scenarios/edge-integration-scenarios.json"
        archived = json.loads(archive_path.read_text(encoding="utf-8"))
        case = next(item for item in archived["scenarios"] if item["id"] == "edge-integration-06")
        path, _, stimulus = next(
            entry
            for entry in _spec_entries("edge")
            if entry[2].get("tags", {}).get("legacy_case") == "edge-integration-06"
        )
        primary = next(item["source"] for item in case["entrypoints"] if item["id"] == "primary")

        self.assertEqual(stimulus["turns"], [f"Launch {primary}", *_resolved_prompts(case)])
        self.assertEqual(stimulus["rubric"], case["rubric"])
        self.assertEqual(stimulus["tags"]["legacy_title"], case["title"])
        self.assertEqual(stimulus["tags"]["channel"], "edge")

        custom_root = ROOT / "tests/vally-custom/edge"
        spec_directory = path.parent.relative_to(custom_root).as_posix()
        environment = stimulus["agent_environment"]
        references = set()
        for item in environment.get("files", []):
            source = item["src"].replace("\\", "/")
            if not source.startswith("fixtures/"):
                references.add(_resolve_source(source, spec_directory))
        for skill in environment.get("skills", []):
            identity = _resolve_source(skill.replace("\\", "/"), spec_directory)
            if not identity.casefold().endswith("/skill.md"):
                identity = f"{identity.rstrip('/')}/SKILL.md"
            references.add(identity)
        self.assertEqual(
            references,
            set(case["source_artifacts"]) | _required_source_artifacts("edge-integration-06"),
        )

        expected = {name: _fixture_text(value) for name, value in case["fixture_files"].items()}
        actual = {}
        for item in environment.get("files", []):
            source = item["src"].replace("\\", "/")
            if not source.startswith("fixtures/"):
                continue
            fixture_path = (path.parent / Path(*PurePosixPath(source).parts)).resolve()
            self.assertTrue(fixture_path.is_relative_to(custom_root.resolve()))
            actual[item["dest"]] = fixture_path.read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_fixture_files_preserve_the_legacy_bytes_and_destinations(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            custom_root = ROOT / "tests/vally-custom" / channel
            cases = _read_legacy(channel)
            entries = {
                stimulus["tags"]["legacy_case"]: (path, stimulus)
                for path, _, stimulus in _spec_entries(channel)
            }
            for case_id, case in cases.items():
                path, stimulus = entries[case_id]
                expected = {name: _fixture_text(value) for name, value in case["fixture_files"].items()}
                actual = {}
                for item in stimulus["agent_environment"]["files"]:
                    source = item["src"].replace("\\", "/")
                    if not source.startswith("fixtures/"):
                        continue
                    fixture_path = (path.parent / Path(*PurePosixPath(source).parts)).resolve()
                    self.assertTrue(fixture_path.is_relative_to(custom_root.resolve()))
                    if (
                        channel == "edge"
                        and case_id == "edge-path-01"
                        and item["dest"] == ".codex/setup-rpi-pytest.py"
                    ):
                        # The RPI runtime helper is additional support; archived fixture inputs
                        # below still compare byte-for-byte with the original scenario.
                        self.assertEqual(source, "fixtures/setup-rpi-pytest.py")
                        self.assertTrue(fixture_path.is_file())
                        environment = stimulus["agent_environment"]
                        self.assertEqual(
                            environment.get("commands"),
                            ["python .codex/setup-rpi-pytest.py"],
                        )
                        self.assertEqual(environment.get("commandTimeout"), "120s")
                        self.assertEqual(
                            environment.get("env", {}).get("PYTHONDONTWRITEBYTECODE"),
                            "1",
                        )
                        spec = YAML_READER.load(path.read_text(encoding="utf-8"))
                        self.assertEqual(spec["defaults"]["timeout"], "3600s")
                        continue
                    if channel == "edge" and case_id == "edge-path-02" and item["dest"] == ".codex/setup-review-history.mjs":
                        # Authorized setup support is additional to the archived fixture inputs.
                        # All original input destinations and bytes still compare exactly below.
                        self.assertEqual(source, "fixtures/setup-review-history.mjs")
                        self.assertTrue(fixture_path.is_file())
                        self.assertEqual(
                            stimulus["agent_environment"].get("commands"),
                            ["node .codex/setup-review-history.mjs"],
                        )
                        self.assertEqual(stimulus["agent_environment"].get("commandTimeout"), "120s")
                        continue
                    actual[item["dest"]] = fixture_path.read_text(encoding="utf-8")
                with self.subTest(channel=channel, case=case_id):
                    self.assertEqual(actual, expected)

    def test_source_artifact_references_follow_the_matching_channel_scenario(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            custom_root = ROOT / "tests/vally-custom" / channel
            cases = _read_legacy(channel)
            entries = {
                stimulus["tags"]["legacy_case"]: (path, stimulus)
                for path, _, stimulus in _spec_entries(channel)
            }
            for case_id, case in cases.items():
                path, stimulus = entries[case_id]
                spec_directory = path.parent.relative_to(custom_root).as_posix()
                environment = stimulus["agent_environment"]
                references = set()
                for item in environment.get("files", []):
                    source = item["src"].replace("\\", "/")
                    if not source.startswith("fixtures/"):
                        references.add(_resolve_source(source, spec_directory))
                for skill in environment.get("skills", []):
                    identity = _resolve_source(skill.replace("\\", "/"), spec_directory)
                    if not identity.casefold().endswith("/skill.md"):
                        identity = f"{identity.rstrip('/')}/SKILL.md"
                    references.add(identity)
                with self.subTest(channel=channel, case=case_id):
                    self.assertEqual(
                        references,
                        set(case["source_artifacts"]) | _required_source_artifacts(case_id),
                    )
                    self.assertTrue(all(source.startswith(".github/") for source in references))
                    self.assertIn(
                        next(item["source"] for item in case["entrypoints"] if item["id"] == "primary"),
                        references,
                    )

        root_case = next(
            item
            for item in json.loads(
                (ROOT / "tests/fixtures/legacy-scenarios/edge-integration-scenarios.json").read_text(
                    encoding="utf-8"
                )
            )["scenarios"]
            if item["id"] == "edge-integration-05"
        )
        path, _, stimulus = next(
            entry
            for entry in _spec_entries("edge")
            if entry[2].get("tags", {}).get("legacy_case") == "edge-integration-05"
        )
        custom_root = ROOT / "tests/vally-custom/edge"
        spec_directory = path.parent.relative_to(custom_root).as_posix()
        environment = stimulus["agent_environment"]
        references = set()
        for item in environment.get("files", []):
            source = item["src"].replace("\\", "/")
            if not source.startswith("fixtures/"):
                references.add(_resolve_source(source, spec_directory))
        for skill in environment.get("skills", []):
            identity = _resolve_source(skill.replace("\\", "/"), spec_directory)
            if not identity.casefold().endswith("/skill.md"):
                identity = f"{identity.rstrip('/')}/SKILL.md"
            references.add(identity)
        self.assertEqual(
            references,
            set(root_case["source_artifacts"]) | _required_source_artifacts("edge-integration-05"),
        )

    def test_required_custom_workflow_skills_and_canonical_files_are_complete(self) -> None:
        entries = {
            stimulus["tags"]["legacy_case"]: (path, spec, stimulus)
            for path, spec, stimulus in _spec_entries("edge")
        }
        for case_id in set(REQUIRED_CUSTOM_SKILLS) | set(REQUIRED_CUSTOM_FILES):
            path, _, stimulus = entries[case_id]
            environment = stimulus["agent_environment"]
            custom_root = ROOT / "tests/vally-custom/edge"
            spec_directory = path.parent.relative_to(custom_root).as_posix()
            declared_skills = {
                _resolve_source(reference.replace("\\", "/"), spec_directory).removesuffix("/SKILL.md")
                for reference in environment.get("skills", [])
            }
            declared_files = {
                (
                    _resolve_source(item["src"].replace("\\", "/"), spec_directory),
                    item["dest"],
                )
                for item in environment.get("files", [])
                if not item["src"].replace("\\", "/").startswith("fixtures/")
            }
            required_skills = REQUIRED_CUSTOM_SKILLS.get(case_id, set())
            required_files = REQUIRED_CUSTOM_FILES.get(case_id, {})
            with self.subTest(case=case_id):
                self.assertTrue(required_skills <= declared_skills)
                self.assertTrue(
                    {
                        (f"{skill}/SKILL.md", f"{skill}/SKILL.md")
                        for skill in required_skills
                    }
                    | set(required_files.items())
                    <= declared_files
                )

    def test_custom_edge_environments_convert_with_synthetic_distribution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hve-custom-edge-") as temporary:
            manifest_path = _build_synthetic_edge_distribution_manifest(Path(temporary))
            artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for path, original, _ in _spec_entries("edge"):
                custom_root = ROOT / "tests/vally-custom/edge"
                spec_directory = path.parent.relative_to(custom_root).as_posix()
                fixture_manifest = _fixture_only_manifest(original, spec_directory)
                conversion_manifest = {**artifact_manifest, **fixture_manifest}
                with self.subTest(spec=path.parent.name):
                    converted = convert_vally_spec(
                        deepcopy(original),
                        conversion_manifest,
                        spec_directory=spec_directory,
                    )
                    self.assertEqual(converted.get("defaults", {}).get("executor"), "codex")
                    self.assertEqual(len(converted["stimuli"]), len(original["stimuli"]))
                    for environment_path, _, kind in _environment_nodes(original):
                        if kind == "grading_environment":
                            continue
                        original_environment = _value_at_path(original, environment_path)
                        converted_environment = _value_at_path(converted, environment_path)
                        for reference in original_environment.get("skills", []):
                            identity = _resolve_source(reference, spec_directory)
                            if not identity.casefold().endswith("/skill.md"):
                                identity = f"{identity.rstrip('/')}/SKILL.md"
                            self.assertTrue(
                                artifact_manifest.get(identity, {}).get("resources"),
                                f"synthetic distribution lacks skill {identity}",
                            )
                        self.assertEqual(
                            len(converted_environment.get("skills", [])),
                            len(original_environment.get("skills", [])),
                        )

    def test_all_custom_specs_convert_through_channel_scoped_manifest_paths(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            custom_root = ROOT / "tests/vally-custom" / channel
            spec_paths = sorted({path for path, _, _ in _spec_entries(channel)})
            for path in spec_paths:
                with self.subTest(channel=channel, spec=path.parent.name):
                    original = YAML_READER.load(path.read_text(encoding="utf-8"))
                    spec_directory = path.parent.relative_to(custom_root).as_posix()
                    manifest = _synthetic_artifact_manifest(original, spec_directory)
                    converted = convert_vally_spec(
                        deepcopy(original),
                        manifest,
                        spec_directory=spec_directory,
                    )
                    self.assertEqual(converted.get("defaults", {}).get("executor"), "codex")
                    self.assertEqual(len(converted["stimuli"]), len(original["stimuli"]))
                    for index, original_stimulus in enumerate(original["stimuli"]):
                        converted_stimulus = converted["stimuli"][index]
                        for key in ("turns", "prompt", "rubric", "graders", "tags"):
                            self.assertEqual(converted_stimulus.get(key), original_stimulus.get(key), key)
                    for environment_path, original_environment, kind in _environment_nodes(original):
                        converted_environment = _value_at_path(converted, environment_path)
                        original_files = original_environment.get("files", [])
                        converted_files = converted_environment.get("files", [])
                        self.assertGreaterEqual(len(converted_files), len(original_files))
                        for index, original_file in enumerate(original_files):
                            source_identity = _resolve_source(original_file["src"], spec_directory)
                            self.assertEqual(
                                converted_files[index]["src"],
                                manifest[source_identity]["resources"][0],
                            )
                            self.assertEqual(converted_files[index]["dest"], original_file["dest"])
                            if original_file["src"].replace("\\", "/").startswith("fixtures/"):
                                fixture = (path.parent / Path(*PurePosixPath(original_file["src"]).parts)).resolve()
                                self.assertTrue(fixture.is_file())
                        if kind != "grading_environment":
                            expected_skills = []
                            for reference in original_environment.get("skills", []):
                                identity = _resolve_source(reference, spec_directory)
                                if not identity.casefold().endswith("/skill.md"):
                                    identity = f"{identity.rstrip('/')}/SKILL.md"
                                resource = manifest[identity]["resources"][0]
                                expected_skills.append(str(PurePosixPath(resource).parent))
                            self.assertEqual(converted_environment.get("skills", []), expected_skills)

    def test_every_original_check_is_static_fixture_evidence_or_explicitly_prompt_only(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            cases = _read_legacy(channel)
            entries = {
                stimulus["tags"]["legacy_case"]: stimulus
                for _, _, stimulus in _spec_entries(channel)
            }
            for case_id, case in cases.items():
                stimulus = entries[case_id]
                prompt_only = set(stimulus["tags"].get("prompt_only_checks", []))
                checks = case.get("deterministic_checks", [])
                check_ids = {item["id"] for item in checks}
                static_grader_names = {
                    item["name"] for item in stimulus.get("graders", []) if item["type"] != "prompt"
                }
                staged_destinations = {
                    item["dest"] for item in stimulus["agent_environment"].get("files", [])
                }
                self.assertTrue(prompt_only <= check_ids, f"{case_id} tags an unknown check as prompt-only")
                for check in checks:
                    check_id = check["id"]
                    kind = check["kind"]
                    if kind == "fixture_absent":
                        self.assertNotIn(check.get("path"), staged_destinations, f"{case_id}:{check_id}")
                        self.assertIn(check_id, static_grader_names, f"{case_id}:{check_id}")
                    elif kind.startswith("fixture_"):
                        self.assertIn(check.get("path"), staged_destinations, f"{case_id}:{check_id}")
                        if kind == "fixture_hash_at_turn":
                            self.assertIn(check_id, prompt_only, f"temporal check cannot be a final-state check: {check_id}")
                    elif check_id not in static_grader_names:
                        self.assertIn(check_id, prompt_only, f"{case_id}:{check_id} was silently lost")

    def test_only_vally_builtin_graders_are_used_with_full_trajectory_evidence(self) -> None:
        for channel, contract in LEGACY_CASES.items():
            for _, _, stimulus in _spec_entries(channel):
                graders = stimulus.get("graders", [])
                with self.subTest(channel=channel, case=stimulus["tags"].get("legacy_case")):
                    self.assertTrue(graders)
                    self.assertTrue(all(item["type"] in BUILTIN_GRADERS for item in graders))
                    prompt_graders = [item for item in graders if item["type"] == "prompt"]
                    self.assertEqual(len(prompt_graders), 1)
                    self.assertEqual(
                        prompt_graders[0]["config"]["evidence"],
                        ["trajectory", "diff"],
                    )

    def test_absent_ado_connector_case_never_injects_a_fake_platform_tool(self) -> None:
        stimulus = next(
            stimulus
            for _, _, stimulus in _spec_entries("stable")
            if stimulus.get("tags", {}).get("legacy_case") == "golden-path-05"
        )
        environment = stimulus["agent_environment"]
        self.assertFalse(any(key.casefold() in {"mcpservers", "mcp_servers", "servers"} for key in environment))
        combined_prompt = "\n".join(stimulus["turns"])
        self.assertIn("If no real authorized platform tool and credentials are available, stop", combined_prompt)
        self.assertIn("Never create a local file to simulate a successful platform mutation", combined_prompt)
        self.assertIn("no-fake-platform-result", stimulus["tags"]["prompt_only_checks"])
        self.assertIn("no-fabricated-platform-write", stimulus["tags"]["prompt_only_checks"])
        self.assertTrue(any(item["type"] == "diff-empty" for item in stimulus["graders"]))

    def test_rpi_manual_boundaries_use_turn_scoped_protected_file_diffs(self) -> None:
        protected = {
            "importer": "src/importer.py",
            "tests": "tests/test_importer.py",
        }
        expected_pattern = {
            key: rf"(?m)^diff --git a/{re.escape(path)} b/{re.escape(path)}$"
            for key, path in protected.items()
        }
        for channel, case_id, expected_turns in (
            ("stable", "golden-path-01", (0, 1, 2)),
            ("edge", "edge-path-01", (0, 1, 2)),
        ):
            stimulus = next(
                stimulus
                for _, _, stimulus in _spec_entries(channel)
                if stimulus["tags"].get("legacy_case") == case_id
            )
            with self.subTest(channel=channel):
                self.assertEqual(len(stimulus["turns"]), 5 if channel == "stable" else 4)
                graders = {
                    item["name"]: item
                    for item in stimulus["graders"]
                    if item["type"] != "prompt"
                }
                for turn in expected_turns:
                    for key, path in protected.items():
                        name = f"{key}-unchanged-at-launch-boundary" if turn == 0 else (
                            f"{key}-unchanged-after-manual-work" if channel == "stable" and turn == 1 else
                            f"{key}-unchanged-after-manual-resume" if channel == "stable" else
                            f"{key}-unchanged-after-manual-research" if turn == 1 else
                            f"{key}-unchanged-after-plan-only-approval"
                        )
                        grader = graders[name]
                        self.assertEqual(grader["type"], "diff-not-contains")
                        self.assertEqual(grader["turn"], turn)
                        self.assertEqual(grader["config"]["pattern"], expected_pattern[key])

                prompt_only = set(stimulus["tags"]["prompt_only_checks"])
                if channel == "stable":
                    # The new final diff checks confirm changed paths, but the old
                    # exact fixture hashes at prompt 2/4 remain distinct assertions.
                    self.assertIn("manual-source-unchanged-after-resume", prompt_only)
                    self.assertIn("manual-tests-unchanged-after-resume", prompt_only)
                    self.assertIn("full-auto-source-modified-after-implementation-resume", prompt_only)
                    self.assertIn("full-auto-tests-modified-after-implementation-resume", prompt_only)
                    self.assertIn("manual-approval-remains-a-gate", prompt_only)
                    self.assertIn("exact fixture SHA-at-turn assertions", stimulus["tags"]["deterministic_additions"][-1])
                    for key in protected:
                        name = "authorized-importer-change-at-final-boundary" if key == "importer" else "authorized-test-change-at-final-boundary"
                        grader = graders[name]
                        self.assertEqual(grader["type"], "diff-contains")
                        self.assertNotIn("turn", grader)
                        self.assertEqual(grader["config"]["pattern"], expected_pattern[key])
                    # Vally prepends the Launch message, shifting legacy prompt 2
                    # to Vally turn 2 and legacy prompt 4 to Vally turn 4.
                    self.assertIn("Launch .github/prompts/hve-core/rpi.prompt.md", stimulus["turns"][0])
                    self.assertIn("MANUAL variant", stimulus["turns"][1])
                    self.assertIn("Ask for approval", stimulus["turns"][2])
                    self.assertIn("I explicitly authorize", stimulus["turns"][3])
                    self.assertIn("after an interruption", stimulus["turns"][4])
                else:
                    self.assertIn("literal fixture SHA-at-turn", " ".join(stimulus["tags"]["deterministic_additions"]))
                    self.assertIn("human approval semantics", " ".join(stimulus["tags"]["deterministic_additions"]))
                    self.assertIn("Plan only", stimulus["turns"][2])
                    self.assertIn("I explicitly authorize", stimulus["turns"][3])

    def test_rpi_turn_diff_patterns_are_anchored_and_allow_tracking_outputs(self) -> None:
        fixture_patch = """diff --git a/src/importer.py b/src/importer.py
index 1111111..2222222 100644
--- a/src/importer.py
+++ b/src/importer.py
@@ -1 +1,2 @@
 def import_rows(rows):
+    return rows
"""
        tracking_patch = """diff --git a/.copilot-tracking/research/2026-10-11/retry-limit-research.md b/.copilot-tracking/research/2026-10-11/retry-limit-research.md
new file mode 100644
--- /dev/null
+++ b/.copilot-tracking/research/2026-10-11/retry-limit-research.md
@@ -0,0 +1,2 @@
+Review src/importer.py and tests/test_importer.py.
+Do not edit src/importer.py yet.
"""
        source_pattern = r"(?m)^diff --git a/src/importer\.py b/src/importer\.py$"
        tests_pattern = r"(?m)^diff --git a/tests/test_importer\.py b/tests/test_importer\.py$"
        self.assertRegex(fixture_patch, source_pattern)
        self.assertNotRegex(tracking_patch, source_pattern)
        self.assertNotRegex(fixture_patch, tests_pattern)
        self.assertNotRegex("diff --git a/src/importer.py.backup b/src/importer.py.backup\n", source_pattern)
        self.assertNotRegex("diff --git a/tests/test_importer.pyc b/tests/test_importer.pyc\n", tests_pattern)

    def test_converted_prd_state_regexes_match_the_original_state_fields(self) -> None:
        stimulus = next(
            stimulus
            for _, _, stimulus in _spec_entries("stable")
            if stimulus.get("tags", {}).get("legacy_case") == "golden-path-04"
        )
        patterns = {
            item["name"]: item["config"]["pattern"]
            for item in stimulus["graders"]
            if item["type"] == "file-matches"
        }
        samples = {
            "prd-session-state": '{\n  "prdFile": "docs/prds/import-retry.md"\n}',
            "brd-session-state": '{\n  "brdFile": "docs/brds/import-retry-brd.md"\n}',
        }
        self.assertEqual(set(patterns), set(samples))
        for name, sample in samples.items():
            with self.subTest(name=name):
                self.assertRegex(sample, patterns[name])
                self.assertNotRegex(sample, patterns[name].replace("import-retry", "wrong-path"))

    def test_existing_scoped_output_graders_reject_out_of_scope_diffs(self) -> None:
        expected_allowed_paths = {
            "stable-handoff-output-scope": [
                ".copilot-tracking/dt/profile-preferences/coaching-state.md",
                ".copilot-tracking/dt/profile-preferences/handoff-summary.md",
                ".copilot-tracking/dt/profile-preferences/rpi-handoff-problem-space.md",
            ],
            "edge-handoff-output-scope": [
                ".copilot-tracking/dt/profile-preferences/coaching-state.md",
                ".copilot-tracking/dt/profile-preferences/handoff-summary.md",
                ".copilot-tracking/dt/profile-preferences/rpi-handoff-problem-space.md",
            ],
            "source-review-output-scope": [
                ".copilot-tracking/changes/2026-10-11/upstream-change-report.md",
            ],
            "analysis-output-scope": [
                "docs/data/retention-analysis.ipynb",
                ".copilot-tracking/ds/retention-analysis/session-state.md",
            ],
        }
        found = {}
        for channel in LEGACY_CASES:
            for _, _, stimulus in _spec_entries(channel):
                for grader in stimulus.get("graders", []):
                    if grader["name"] in expected_allowed_paths:
                        self.assertEqual(grader["type"], "diff-not-contains")
                        found[grader["name"]] = re.compile(grader["config"]["pattern"])
        self.assertEqual(set(found), set(expected_allowed_paths))
        for name, allowed_paths in expected_allowed_paths.items():
            with self.subTest(scope=name):
                for allowed in allowed_paths:
                    diff = f"diff --git a/{allowed} b/{allowed}\n"
                    self.assertIsNone(found[name].search(diff))
                self.assertIsNotNone(found[name].search("diff --git a/README.md b/README.md\n"))


if __name__ == "__main__":
    unittest.main()
