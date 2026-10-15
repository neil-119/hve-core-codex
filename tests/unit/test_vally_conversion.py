from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.common import PortError  # noqa: E402
from hve_codex_porter.vally_conversion import convert_vally_spec  # noqa: E402


class VallySpecConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {
            ".github/skills/team/research/SKILL.md": {
                "originals": ["plugins/hve-core-codex/originals/.github/skills/team/research/SKILL.md"],
                "outputs": ["plugins/hve-core-codex/skills/research/SKILL.md"],
                "resources": ["plugins/hve-core-codex/skills/research/SKILL.md"],
            },
            ".github/skills/team/research/scripts/helper.py": {
                "originals": ["plugins/hve-core-codex/originals/.github/skills/team/research/scripts/helper.py"],
                "outputs": ["plugins/hve-core-codex/skills/research/scripts/helper.py"],
                "resources": ["plugins/hve-core-codex/skills/research/scripts/helper.py"],
            },
            ".github/skills/team/research": {
                "originals": ["plugins/hve-core-codex/originals/.github/skills/team/research"],
                "outputs": ["plugins/hve-core-codex/skills/research"],
                "resources": ["plugins/hve-core-codex/skills/research"],
            },
            "evals/behavior/fixtures/request.json": {
                "originals": ["plugins/hve-core-codex/originals/evals/behavior/fixtures/request.json"],
                "outputs": ["plugins/hve-core-codex/resources/evals/behavior/fixtures/request.json"],
                "resources": ["plugins/hve-core-codex/resources/evals/behavior/fixtures/request.json"],
            },
            "evals/behavior/fixtures/expected.json": {
                "originals": ["plugins/hve-core-codex/originals/evals/behavior/fixtures/expected.json"],
                "outputs": ["plugins/hve-core-codex/resources/evals/behavior/fixtures/expected.json"],
                "resources": ["plugins/hve-core-codex/resources/evals/behavior/fixtures/expected.json"],
            },
        }

    def test_maps_executor_and_manifest_artifacts_without_changing_eval_contract(self) -> None:
        spec = {
            "name": "behavior-suite",
            "executor": "copilot-sdk",
            "defaults": {
                "executor": "copilot-sdk",
                "runs": 7,
                "scoring": {"threshold": 0.83, "weights": {"quality": 2}},
            },
            "environment": {"skills": ["../../.github/skills/team/research"]},
            "stimuli": [
                {
                    "name": "request-roundtrip",
                    "executor": "copilot-sdk",
                    "prompt": "Use the supplied research procedure.",
                    "environment": {
                        "files": [{"src": "fixtures/request.json", "dest": "input/request.json"}],
                        "skills": ["../../.github/skills/team/research/SKILL.md"],
                    },
                    "agent_environment": {
                        "files": [{"src": "fixtures/expected.json", "dest": "expected.json"}],
                    },
                    "graders": [{"type": "json-match", "config": {"strict": True}}],
                    "assertions": [{"path": "/status", "equals": "ok"}],
                    "fixtures": {"case": "fixtures/request.json"},
                }
            ],
            "tags": ["smoke", "release-gate"],
        }
        original = copy.deepcopy(spec)

        converted = convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

        skill_directory = "plugins/hve-core-codex/skills/research"
        self.assertEqual(converted["executor"], "codex")
        self.assertEqual(converted["defaults"]["executor"], "codex")
        self.assertEqual(converted["stimuli"][0]["executor"], "codex")
        self.assertEqual(converted["defaults"]["runs"], 7)
        self.assertEqual(converted["defaults"]["scoring"], {"threshold": 0.83, "weights": {"quality": 2}})
        self.assertEqual(converted["tags"], original["tags"])

        stimulus = converted["stimuli"][0]
        self.assertEqual(stimulus["environment"]["skills"], [skill_directory])
        self.assertEqual(stimulus["environment"]["files"], [
            {
                "src": "plugins/hve-core-codex/resources/evals/behavior/fixtures/request.json",
                "dest": "input/request.json",
            },
            {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
            {
                "src": "plugins/hve-core-codex/resources",
                "dest": ".codex/hve-core-codex/plugin-resources/resources",
            },
            {"src": skill_directory, "dest": ".agents/skills/research"},
        ])
        self.assertEqual(
            self.manifest[".github/skills/team/research/scripts/helper.py"]["resources"][0],
            f"{skill_directory}/scripts/helper.py",
            "directory staging must contain nested converted skill resources",
        )
        self.assertEqual(stimulus["agent_environment"]["files"], [
            {
                "src": "plugins/hve-core-codex/resources/evals/behavior/fixtures/expected.json",
                "dest": "expected.json",
            },
            {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
            {
                "src": "plugins/hve-core-codex/resources",
                "dest": ".codex/hve-core-codex/plugin-resources/resources",
            },
        ])
        self.assertEqual(stimulus["prompt"], original["stimuli"][0]["prompt"])
        self.assertEqual(stimulus["graders"], original["stimuli"][0]["graders"])
        self.assertEqual(stimulus["assertions"], original["stimuli"][0]["assertions"])
        self.assertEqual(stimulus["fixtures"], original["stimuli"][0]["fixtures"])
        self.assertEqual(spec, original, "conversion must not mutate its input")
        self.assertEqual(
            convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior"),
            converted,
            "the same source and manifest must convert deterministically",
        )

    def test_maps_root_and_stimulus_grading_files_without_changing_grader_contracts(self) -> None:
        spec = {
            "grading_environment": {
                "files": [{"src": "fixtures/expected.json", "dest": "keys/expected.json"}],
                "fixture_note": "grader-only",
            },
            "stimuli": [{
                "name": "grade-hidden-answer",
                "grading_environment": {
                    "files": [{"src": "fixtures/expected.json", "dest": "expected.json"}],
                    "fixture_note": "stimulus-scoped",
                },
                "graders": [{
                    "type": "file-equals",
                    "config": {"actual": "output.json", "expected": "expected.json", "strict": True},
                }],
            }],
        }
        original_graders = copy.deepcopy(spec["stimuli"][0]["graders"])

        converted = convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

        artifact = "plugins/hve-core-codex/resources/evals/behavior/fixtures/expected.json"
        self.assertEqual(converted["grading_environment"]["files"], [{
            "src": artifact,
            "dest": "keys/expected.json",
        }])
        self.assertEqual(converted["grading_environment"]["fixture_note"], "grader-only")
        stimulus = converted["stimuli"][0]
        self.assertEqual(stimulus["grading_environment"]["files"], [{
            "src": artifact,
            "dest": "expected.json",
        }])
        self.assertEqual(stimulus["grading_environment"]["fixture_note"], "stimulus-scoped")
        self.assertEqual(stimulus["graders"], original_graders)

    def test_runtime_agent_files_add_native_role_companions_but_grading_files_do_not(self) -> None:
        source = ".github/agents/reviewer.agent.md"
        resource = "plugins/hve-core-codex/resources/.github/agents/reviewer.agent.md"
        profile = "codex-overlay/agents/reviewer.toml"
        self.manifest[source] = {
            "outputs": [profile],
            "resources": [resource],
        }
        spec = {
            "environment": {
                "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "roles/reviewer.md"}],
            },
            "stimuli": [{
                "agent_environment": {
                    "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "roles/reviewer.md"}],
                },
                "grading_environment": {
                    "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "grader/reviewer.md"}],
                },
            }],
        }

        converted = convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

        authored_resource = {"src": resource, "dest": "roles/reviewer.md"}
        native_profile = {"src": profile, "dest": ".codex/agents/reviewer.toml"}
        resource_directories = [
            {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
            {
                "src": "plugins/hve-core-codex/resources",
                "dest": ".codex/hve-core-codex/plugin-resources/resources",
            },
        ]
        self.assertEqual(converted["environment"]["files"], [
            authored_resource, native_profile, *resource_directories,
        ])
        self.assertEqual(
            converted["stimuli"][0]["agent_environment"]["files"],
            [authored_resource, native_profile, *resource_directories],
        )
        self.assertEqual(converted["stimuli"][0]["grading_environment"]["files"], [{
            "src": resource,
            "dest": "grader/reviewer.md",
        }])
        self.assertEqual(spec["environment"]["files"][0]["src"], "../../.github/agents/reviewer.agent.md")

    def test_agent_markdown_without_native_role_output_keeps_existing_resource_mapping(self) -> None:
        source = ".github/agents/manual.agent.md"
        resource = "resources/manual.agent.md"
        self.manifest[source] = {
            "outputs": [resource],
            "resources": [resource],
        }

        converted = convert_vally_spec(
            {"environment": {"files": [{"src": "../../.github/agents/manual.agent.md", "dest": "roles/manual.md"}]}},
            self.manifest,
            spec_directory="evals/behavior",
        )

        self.assertEqual(converted["environment"]["files"], [{"src": resource, "dest": "roles/manual.md"}])

    def test_runtime_plugin_resource_file_stages_both_resource_roots(self) -> None:
        converted = convert_vally_spec(
            {"environment": {"files": [{
                "src": "fixtures/request.json",
                "dest": ".agents/resources/evals/behavior/fixtures/request.json",
            }]}},
            self.manifest,
            spec_directory="evals/behavior",
        )

        self.assertEqual(converted["environment"]["files"], [
            {
                "src": "plugins/hve-core-codex/resources/evals/behavior/fixtures/request.json",
                "dest": ".agents/resources/evals/behavior/fixtures/request.json",
            },
            {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
            {
                "src": "plugins/hve-core-codex/resources",
                "dest": ".codex/hve-core-codex/plugin-resources/resources",
            },
        ])
        self.assertFalse(any(
            item.get("dest", "").startswith(".agents/skills/")
            for item in converted["environment"]["files"]
        ))

    def test_nested_codex_plugin_resource_child_stages_both_resource_roots(self) -> None:
        converted = convert_vally_spec(
            {"environment": {"files": [{
                "src": "fixtures/request.json",
                "dest": ".codex/hve-core-codex/plugin-resources/resources/evals/behavior/fixtures/request.json",
            }]}},
            self.manifest,
            spec_directory="evals/behavior",
        )

        self.assertEqual(converted["environment"]["files"], [
            {
                "src": "plugins/hve-core-codex/resources/evals/behavior/fixtures/request.json",
                "dest": ".codex/hve-core-codex/plugin-resources/resources/evals/behavior/fixtures/request.json",
            },
            {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
            {
                "src": "plugins/hve-core-codex/resources",
                "dest": ".codex/hve-core-codex/plugin-resources/resources",
            },
        ])

    def test_existing_exact_plugin_resource_root_is_deduplicated(self) -> None:
        resource_root = "plugins/hve-core-codex/resources"
        self.manifest[resource_root] = {"outputs": [resource_root], "resources": [resource_root]}
        converted = convert_vally_spec(
            {"environment": {"files": [{
                "src": "../../plugins/hve-core-codex/resources",
                "dest": ".agents/resources",
            }]}},
            self.manifest,
            spec_directory="evals/behavior",
        )

        files = converted["environment"]["files"]
        self.assertEqual(files, [
            {"src": resource_root, "dest": ".agents/resources"},
            {"src": resource_root, "dest": ".codex/hve-core-codex/plugin-resources/resources"},
        ])

    def test_conflicting_plugin_resource_directory_destinations_fail_closed(self) -> None:
        for destination in (
            ".agents/resources/unrelated",
            ".codex/hve-core-codex/plugin-resources/resources/unrelated",
        ):
            with self.subTest(destination=destination), self.assertRaisesRegex(
                PortError, "plugin resource destination.*conflicts"
            ):
                convert_vally_spec(
                    {"environment": {"files": [{
                        "src": "fixtures/request.json",
                        "dest": destination,
                    }]}},
                    self.manifest,
                    spec_directory="evals/behavior",
                )

    def test_ambiguous_native_role_outputs_fail_closed(self) -> None:
        source = ".github/agents/reviewer.agent.md"
        self.manifest[source] = {
            "outputs": [
                "codex-overlay/agents/reviewer.toml",
                "codex-overlay/agents/alternate-reviewer.toml",
            ],
            "resources": ["plugins/hve-core-codex/resources/.github/agents/reviewer.agent.md"],
        }

        with self.assertRaisesRegex(PortError, "Ambiguous native Codex role profiles"):
            convert_vally_spec(
                {"environment": {"files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "roles/reviewer.md"}]}},
                self.manifest,
                spec_directory="evals/behavior",
            )

    def test_native_role_destination_conflicts_fail_instead_of_overwriting_files(self) -> None:
        source = ".github/agents/reviewer.agent.md"
        self.manifest[source] = {
            "outputs": ["codex-overlay/agents/reviewer.toml"],
            "resources": ["plugins/hve-core-codex/resources/.github/agents/reviewer.agent.md"],
        }
        for conflicting_destination in (".codex/agents/reviewer.toml", ".codex/agents"):
            with self.subTest(destination=conflicting_destination), self.assertRaisesRegex(
                PortError, "conflicts with native Codex role profile staging"
            ):
                convert_vally_spec(
                    {"environment": {"files": [
                        {"src": "../../.github/agents/reviewer.agent.md", "dest": "roles/reviewer.md"},
                        {"src": "fixtures/request.json", "dest": conflicting_destination},
                    ]}},
                    self.manifest,
                    spec_directory="evals/behavior",
                )

    def test_native_role_destination_conflicts_detect_internal_dot_aliases(self) -> None:
        source = ".github/agents/reviewer.agent.md"
        self.manifest[source] = {
            "outputs": ["codex-overlay/agents/reviewer.toml"],
            "resources": ["plugins/hve-core-codex/resources/.github/agents/reviewer.agent.md"],
        }

        with self.assertRaisesRegex(PortError, "conflicts with native Codex role profile staging"):
            convert_vally_spec(
                {"environment": {"files": [
                    {"src": "../../.github/agents/reviewer.agent.md", "dest": "roles/reviewer.md"},
                    {
                        "src": "fixtures/request.json",
                        "dest": ".codex/agents/team/../reviewer.toml",
                    },
                ]}},
                self.manifest,
                spec_directory="evals/behavior",
            )

    def test_rejects_unsafe_environment_file_destinations(self) -> None:
        for destination in (
            "../outside.json",
            "/outside.json",
            "C:/outside.json",
            "C:\\outside.json",
            "outside" + chr(0) + ".json",
        ):
            with self.subTest(destination=destination), self.assertRaisesRegex(
                PortError, "Unsafe Vally destination"
            ):
                convert_vally_spec(
                    {"environment": {"files": [
                        {"src": "fixtures/request.json", "dest": destination}
                    ]}},
                    self.manifest,
                    spec_directory="evals/behavior",
                )

    def test_normalizes_destinations_for_checks_but_preserves_authored_spelling(self) -> None:
        authored_destination = "roles/../request.json"
        converted = convert_vally_spec(
            {"environment": {"files": [
                {"src": "fixtures/request.json", "dest": authored_destination}
            ]}},
            self.manifest,
            spec_directory="evals/behavior",
        )

        self.assertEqual(
            converted["environment"]["files"][0],
            {
                "src": "plugins/hve-core-codex/resources/evals/behavior/fixtures/request.json",
                "dest": authored_destination,
            },
        )

    def test_exact_existing_native_role_mapping_is_not_duplicated(self) -> None:
        source = ".github/agents/reviewer.agent.md"
        profile = "codex-overlay/agents/reviewer.toml"
        self.manifest[source] = {
            "outputs": [profile],
            "resources": ["plugins/hve-core-codex/resources/.github/agents/reviewer.agent.md"],
        }
        self.manifest[profile] = {"outputs": [profile], "resources": [profile]}

        converted = convert_vally_spec(
            {"environment": {"files": [
                {"src": "../../.github/agents/reviewer.agent.md", "dest": "roles/reviewer.md"},
                {"src": "../../codex-overlay/agents/reviewer.toml", "dest": ".codex/agents/reviewer.toml"},
            ]}},
            self.manifest,
            spec_directory="evals/behavior",
        )

        profile_mappings = [
            item for item in converted["environment"]["files"]
            if item["dest"] == ".codex/agents/reviewer.toml"
        ]
        self.assertEqual(profile_mappings, [{"src": profile, "dest": ".codex/agents/reviewer.toml"}])

    def test_converts_executor_selection_objects_and_supported_executor_allowlist(self) -> None:
        spec = {
            "defaults": {
                "executor": {
                    "name": "copilot-sdk",
                    "config": {"binary": "codex", "sandbox": "read-only", "reasoningEffort": "max"},
                },
            },
            "stimuli": [{
                "name": "codex-compatible",
                "supported_executors": ["copilot-sdk", "mock"],
            }],
        }

        converted = convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

        self.assertEqual(converted["defaults"]["executor"], {
            "name": "codex",
            "config": {"binary": "codex", "sandbox": "read-only", "reasoningEffort": "max"},
        })
        self.assertEqual(converted["stimuli"][0]["supported_executors"], ["codex", "mock"])

    def test_rejects_copilot_provider_config_instead_of_silently_dropping_it(self) -> None:
        spec = {
            "defaults": {
                "executor": {
                    "name": "copilot-sdk",
                    "config": {"provider": {"baseUrl": "https://example.invalid", "apiKeyEnv": "TOKEN"}},
                },
            },
        }

        with self.assertRaisesRegex(PortError, "Copilot provider configuration cannot be discarded"):
            convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

    def test_identical_existing_codex_skill_directory_staging_is_not_duplicated_or_rewritten(self) -> None:
        spec = {
            "environment": {
                "skills": ["../../.github/skills/team/research"],
                "files": [{
                    "src": "../../.github/skills/team/research",
                    "dest": ".agents/skills/research",
                    "mode": "read-only",
                }],
            }
        }

        converted = convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

        self.assertEqual(converted["environment"]["files"], [{
            "src": "plugins/hve-core-codex/skills/research",
            "dest": ".agents/skills/research",
            "mode": "read-only",
        }])

    def test_skill_staging_collision_fails_instead_of_overwriting_an_environment_file(self) -> None:
        spec = {
            "environment": {
                "skills": ["../../.github/skills/team/research"],
                "files": [{"src": "fixtures/request.json", "dest": ".agents/skills/research/SKILL.md"}],
            }
        }

        with self.assertRaisesRegex(PortError, "conflicts with an existing environment.files"):
            convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

    def test_nested_destination_collision_fails_instead_of_overwriting_skill_resources(self) -> None:
        spec = {
            "environment": {
                "skills": ["../../.github/skills/team/research"],
                "files": [{"src": "fixtures/request.json", "dest": ".agents/skills/research/scripts/helper.py"}],
            }
        }

        with self.assertRaisesRegex(PortError, "conflicts with an existing environment.files"):
            convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

    def test_identical_nested_resource_mapping_can_coexist_with_full_directory_staging(self) -> None:
        spec = {
            "environment": {
                "skills": ["../../.github/skills/team/research"],
                "files": [{
                    "src": "../../.github/skills/team/research/scripts/helper.py",
                    "dest": ".agents/skills/research/scripts/helper.py",
                }],
            }
        }

        converted = convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

        self.assertEqual(converted["environment"]["files"], [
            {
                "src": "plugins/hve-core-codex/skills/research/scripts/helper.py",
                "dest": ".agents/skills/research/scripts/helper.py",
            },
            {
                "src": "plugins/hve-core-codex/skills/research",
                "dest": ".agents/skills/research",
            },
        ])

    def test_workspace_root_destination_is_an_ancestor_collision(self) -> None:
        spec = {
            "environment": {
                "skills": ["../../.github/skills/team/research"],
                "files": [{"src": "fixtures/request.json", "dest": "."}],
            }
        }

        with self.assertRaisesRegex(PortError, "conflicts with an existing environment.files"):
            convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

    def test_unmapped_skill_or_file_reference_fails_explicitly(self) -> None:
        for spec in (
            {"environment": {"skills": ["../../.github/skills/team/missing"]}},
            {"stimuli": [{"environment": {"files": [{"src": "fixtures/missing.json", "dest": "x"}]}}]},
        ):
            with self.subTest(spec=spec), self.assertRaisesRegex(PortError, "Unmapped Vally"):
                convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

    def test_ambiguous_skill_resource_fails_closed(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest[".github/skills/team/research/SKILL.md"]["resources"].append("second/SKILL.md")

        with self.assertRaisesRegex(PortError, "exactly one resource artifact"):
            convert_vally_spec(
                {"environment": {"skills": ["../../.github/skills/team/research"]}},
                manifest,
                spec_directory="evals/behavior",
            )

    def test_rejects_unsupported_executor_and_unsafe_or_escaping_references(self) -> None:
        cases = (
            ({"defaults": {"executor": "shell"}}, "Unsupported Vally executor"),
            ({"environment": {"skills": ["C:/outside/SKILL.md"]}}, "Unsafe Vally skill reference"),
            ({"environment": {"files": [{"src": "../../../outside.json", "dest": "x"}]}}, "escapes the upstream root"),
        )
        for spec, error in cases:
            with self.subTest(spec=spec), self.assertRaisesRegex(PortError, error):
                convert_vally_spec(spec, self.manifest, spec_directory="evals/behavior")

    def test_requires_upstream_relative_spec_directory(self) -> None:
        with self.assertRaisesRegex(PortError, "upstream-root-relative"):
            convert_vally_spec({}, self.manifest, spec_directory="/absolute/eval")


if __name__ == "__main__":
    unittest.main()


class RepositoryContextTests(unittest.TestCase):
    def test_context_is_generated_only_for_implicit_repository_cases(self):
        manifest = {".github/agents/test.agent.md": {"resources": ["plugins/hve-core-codex/resources/.github/agents/test.agent.md"]}}
        original = {"name": "behavior-conformance-instructions", "stimuli": [{"name": "implicit", "prompt": "Read the repo", "graders": [{"type": "output-matches", "config": {"pattern": "test"}}]}]}
        result = convert_vally_spec(original, manifest, spec_directory="evals/contracts")
        self.assertEqual(result["stimuli"][0]["prompt"], original["stimuli"][0]["prompt"])
        self.assertEqual(result["stimuli"][0]["graders"], original["stimuli"][0]["graders"])
        self.assertEqual(result["stimuli"][0]["environment"]["files"][0]["dest"], ".github")
        self.assertNotIn("environment", original["stimuli"][0])

    def test_empty_baseline_control_receives_no_repository_context(self):
        manifest = {".github/agents/test.agent.md": {"resources": ["plugins/hve-core-codex/resources/.github/agents/test.agent.md"]}}
        original = {"name": "baseline-equivalence-baseline", "stimuli": [{"name": "control", "prompt": "Hello"}]}
        self.assertEqual(convert_vally_spec(original, manifest, spec_directory="evals/baseline"), original)

    def test_generic_inherited_environment_does_not_hide_repository_context(self):
        manifest = {".github/agents/test.agent.md": {"resources": ["plugins/hve-core-codex/resources/.github/agents/test.agent.md"]}}
        original = {"name": "agent-behavior", "environment": {"files": []}, "stimuli": [{"name": "query", "prompt": "Explain the saved query"}]}
        result = convert_vally_spec(original, manifest, spec_directory="evals/agent-behavior")
        self.assertEqual(result["environment"], original["environment"])
        self.assertEqual(result["stimuli"][0]["environment"]["files"][0]["dest"], ".github")
