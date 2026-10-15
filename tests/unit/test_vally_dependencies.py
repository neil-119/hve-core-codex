from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.common import PortError  # noqa: E402
from hve_codex_porter.vally_dependencies import (  # noqa: E402
    supplement_environment_dependencies,
)


class VallyDependencySupplementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {
            ".github/agents/reviewer.agent.md": {"resources": ["resource/reviewer.md"]},
            ".github/instructions/first.md": {"resources": ["resource/first.md"]},
            ".github/instructions/second.md": {"resources": ["resource/second.md"]},
            ".github/skills/linked/SKILL.md": {"resources": ["resource/linked/SKILL.md"]},
            ".github/skills/declared/SKILL.md": {"resources": ["resource/declared/SKILL.md"]},
            ".github/skills/linked/reference.md": {"resources": ["resource/linked/reference.md"]},
            "package-lock.json": {
                "resources": ["plugins/hve-core-codex/resources/package-lock.json"]
            },
        }

    def _graph(self) -> list[dict[str, object]]:
        return [
            {
                "kind": "agent",
                "source": ".github/agents/reviewer.agent.md",
                "dependencies": [
                    ".github/instructions/first.md",
                    ".github/skills/linked/SKILL.md",
                    "package-lock.json",
                ],
            },
            {
                "kind": "instruction",
                "source": ".github/instructions/first.md",
                "dependencies": [
                    ".github/instructions/second.md",
                    ".github/instructions/second.md",
                ],
            },
            {
                "kind": "instruction",
                "source": ".github/instructions/second.md",
                "dependencies": [".github/instructions/first.md"],
            },
            {
                "kind": "skill",
                "source": ".github/skills/declared/SKILL.md",
                "dependencies": [".github/skills/linked/SKILL.md"],
            },
            {
                "kind": "skill",
                "source": ".github/skills/linked/SKILL.md",
                "dependencies": [".github/skills/linked/reference.md"],
            },
        ]

    def test_adds_transitive_dependencies_once_and_handles_cycles(self) -> None:
        document = {
            "stimuli": [
                {
                    "prompt": "Keep this prompt unchanged.",
                    "environment": {
                        "files": [
                            {
                                "src": "../../.github/agents/reviewer.agent.md",
                                "dest": "agents/reviewer.agent.md",
                            },
                            {"src": "fixtures/package-lock.json", "dest": "package-lock.json"},
                        ],
                        "skills": ["../../.github/skills/declared"],
                    },
                }
            ]
        }

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=self.manifest,
            artifacts=self._graph(),
            spec_directory="evals/demo",
        )

        environment = document["stimuli"][0]["environment"]
        expected = {
            ".github/instructions/first.md",
            ".github/instructions/second.md",
            ".github/skills/linked/SKILL.md",
            ".github/skills/linked/reference.md",
            "package-lock.json",
        }
        expected_destinations = expected - {"package-lock.json"}
        expected_destinations.add(
            ".codex/hve-core-codex/plugin-resources/resources/package-lock.json"
        )
        appended = environment["files"][2:]
        self.assertEqual({item["dest"] for item in appended}, expected_destinations)
        self.assertEqual(len(appended), len(expected))
        self.assertEqual(set(added), expected)
        self.assertTrue(
            all(item["src"].startswith("../../.github/") for item in appended if item["dest"].startswith(".github/"))
        )
        self.assertIn(
            {"src": "fixtures/package-lock.json", "dest": "package-lock.json"},
            environment["files"],
            "the consumer fixture must remain at the authored project destination",
        )
        package_lock_resource = next(
            item for item in appended if item["dest"].endswith("/package-lock.json")
        )
        self.assertEqual(package_lock_resource["src"], "../../package-lock.json")
        self.assertEqual(environment["skills"], ["../../.github/skills/declared"])
        self.assertEqual(document["stimuli"][0]["prompt"], "Keep this prompt unchanged.")

    def test_deduplicates_an_authored_canonical_source_destination_pair(self) -> None:
        document = {
            "environment": {
                "files": [
                    {"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"},
                    {
                        "src": "../../.github/instructions/first.md",
                        "dest": ".github/instructions/first.md",
                    },
                ]
            }
        }

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=self.manifest,
            artifacts=self._graph(),
            spec_directory="evals/demo",
        )

        files = document["environment"]["files"]
        self.assertEqual(len(files), 6)
        self.assertEqual(
            sum(item["dest"] == ".github/instructions/first.md" for item in files),
            1,
        )
        self.assertNotIn(".github/instructions/first.md", added)

    def test_does_not_touch_grading_fixtures_graders_or_tags(self) -> None:
        document = {
            "environment": {"files": [{"src": "fixtures/input.json", "dest": "input.json"}]},
            "grading_environment": {
                "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "grader-agent.md"}],
                "agent_environment": {"files": []},
            },
            "graders": [
                {"agent_environment": {"files": [{"src": "grader.md", "dest": "grader.md"}]}}
            ],
            "fixtures": {"environment": {"files": [{"src": "fixture.md", "dest": "fixture.md"}]}},
            "tags": {"agent_environment": {"files": []}},
        }
        original_grading = copy.deepcopy(
            {key: document[key] for key in ("grading_environment", "graders", "fixtures", "tags")}
        )

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=self.manifest,
            artifacts=self._graph(),
            spec_directory="evals/demo",
        )

        self.assertEqual(added, [])
        self.assertEqual(
            {key: document[key] for key in original_grading},
            original_grading,
        )
        self.assertEqual(document["environment"]["files"], [
            {"src": "fixtures/input.json", "dest": "input.json"}
        ])

    def test_supplements_named_vally_yaml_environments(self) -> None:
        document = {
            "environments": {
                "runtime": {
                    "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"}]
                },
                "secondary": {
                    "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "grader.md"}]
                },
            },
            "grading_environment": {
                "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "grading.md"}]
            },
        }

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=self.manifest,
            artifacts=self._graph(),
            spec_directory="evals/demo",
        )

        self.assertEqual(len(added), 5)
        self.assertEqual(len(document["environments"]["runtime"]["files"]), 6)
        self.assertEqual(len(document["environments"]["secondary"]["files"]), 6)
        self.assertEqual(len(document["grading_environment"]["files"]), 1)

    def test_rejects_destination_collision_without_partial_mutation(self) -> None:
        document = {
            "environment": {
                "files": [
                    {"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"},
                    {"src": "fixtures/override.md", "dest": ".github/instructions/first.md"},
                ]
            }
        }
        original = copy.deepcopy(document)

        with self.assertRaisesRegex(PortError, "collides"):
            supplement_environment_dependencies(
                document,
                artifact_manifest=self.manifest,
                artifacts=self._graph(),
                spec_directory="evals/demo",
            )

        self.assertEqual(document, original)

    def test_rejects_destination_collision_through_internal_dot_alias(self) -> None:
        document = {
            "environment": {
                "files": [
                    {"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"},
                    {
                        "src": "fixtures/override.md",
                        "dest": ".github/instructions/extra/../first.md",
                    },
                ]
            }
        }
        original = copy.deepcopy(document)

        with self.assertRaisesRegex(PortError, "collides"):
            supplement_environment_dependencies(
                document,
                artifact_manifest=self.manifest,
                artifacts=self._graph(),
                spec_directory="evals/demo",
            )

        self.assertEqual(document, original)

    def test_rejects_unsafe_authored_destinations_atomically(self) -> None:
        for destination in (
            "../outside.md",
            "/outside.md",
            "C:/outside.md",
            "C:\\outside.md",
            "outside" + chr(0) + ".md",
        ):
            document = {
                "environment": {
                    "files": [{
                        "src": "../../.github/agents/reviewer.agent.md",
                        "dest": destination,
                    }]
                }
            }
            original = copy.deepcopy(document)

            with self.subTest(destination=destination), self.assertRaisesRegex(
                PortError, "Unsafe Vally destination"
            ):
                supplement_environment_dependencies(
                    document,
                    artifact_manifest=self.manifest,
                    artifacts=self._graph(),
                    spec_directory="evals/demo",
                )

            self.assertEqual(document, original)

    def test_rejects_reachable_graph_dependency_missing_from_manifest_atomically(self) -> None:
        document = {
            "environment": {
                "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"}]
            }
        }
        original = copy.deepcopy(document)
        graph = [
            {
                "kind": "agent",
                "source": ".github/agents/reviewer.agent.md",
                "dependencies": [".github/instructions/not-in-manifest.md"],
            }
        ]

        with self.assertRaisesRegex(PortError, "absent from the artifact manifest"):
            supplement_environment_dependencies(
                document,
                artifact_manifest=self.manifest,
                artifacts=graph,
                spec_directory="evals/demo",
            )

        self.assertEqual(document, original)

    def test_skips_non_hve_fixture_seeds_without_graph_metadata(self) -> None:
        document = {
            "environment": {
                "files": [{"src": "fixtures/request.json", "dest": "request.json"}],
                "skills": ["vendor/skill-without-report-metadata"],
            }
        }

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=self.manifest,
            artifacts=self._graph(),
            spec_directory="evals/demo",
        )

        self.assertEqual(added, [])
        self.assertEqual(document["environment"]["files"], [
            {"src": "fixtures/request.json", "dest": "request.json"}
        ])
        self.assertEqual(document["environment"]["skills"], ["vendor/skill-without-report-metadata"])

    def test_non_plugin_resources_keep_their_layout_under_the_asset_root(self) -> None:
        source = "vendor/config.json"
        manifest = dict(self.manifest)
        manifest[source] = {"resources": ["resources/vendor/config.json"]}
        document = {
            "environment": {
                "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"}]
            }
        }
        graph = [
            {
                "kind": "agent",
                "source": ".github/agents/reviewer.agent.md",
                "dependencies": [source],
            }
        ]

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=manifest,
            artifacts=graph,
            spec_directory="evals/demo",
        )

        self.assertEqual(added, [source])
        self.assertEqual(
            document["environment"]["files"][1],
            {
                "src": "../../vendor/config.json",
                "dest": ".codex/hve-core-codex/plugin-resources/resources/vendor/config.json",
            },
        )

    def test_rejects_missing_ambiguous_and_invalid_resource_mappings(self) -> None:
        source = "package-lock.json"
        graph = [
            {
                "kind": "agent",
                "source": ".github/agents/reviewer.agent.md",
                "dependencies": [source],
            }
        ]
        cases = [
            ("missing", None, "absent from the artifact manifest"),
            ("empty", {"resources": []}, "exactly one resource artifact"),
            (
                "ambiguous",
                {"resources": ["resources/one.json", "resources/two.json"]},
                "exactly one resource artifact",
            ),
            ("invalid", {"resources": ["resources/../package-lock.json"]}, "Invalid converted resource path"),
        ]

        for name, entry, message in cases:
            with self.subTest(name=name):
                manifest = dict(self.manifest)
                if entry is None:
                    manifest.pop(source)
                else:
                    manifest[source] = entry
                document = {
                    "environment": {
                        "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"}]
                    }
                }
                original = copy.deepcopy(document)

                with self.assertRaisesRegex(PortError, message):
                    supplement_environment_dependencies(
                        document,
                        artifact_manifest=manifest,
                        artifacts=graph,
                        spec_directory="evals/demo",
                    )
                self.assertEqual(document, original)

    def test_rejects_two_dependencies_mapped_to_the_same_resource_destination(self) -> None:
        manifest = dict(self.manifest)
        manifest["vendor/a.json"] = {"resources": ["resources/shared.json"]}
        manifest["vendor/b.json"] = {"resources": ["resources/shared.json"]}
        graph = [
            {
                "kind": "agent",
                "source": ".github/agents/reviewer.agent.md",
                "dependencies": ["vendor/a.json", "vendor/b.json"],
            }
        ]
        document = {
            "environment": {
                "files": [{"src": "../../.github/agents/reviewer.agent.md", "dest": "agent.md"}]
            }
        }
        original = copy.deepcopy(document)

        with self.assertRaisesRegex(PortError, "collides"):
            supplement_environment_dependencies(
                document,
                artifact_manifest=manifest,
                artifacts=graph,
                spec_directory="evals/demo",
            )
        self.assertEqual(document, original)

    def test_recursive_yaml_reports_the_bundle_contract_message(self) -> None:
        document: dict[str, object] = {}
        document["nested"] = document

        with self.assertRaisesRegex(PortError, "Recursive Vally YAML data is not supported"):
            supplement_environment_dependencies(
                document,
                artifact_manifest=self.manifest,
                artifacts=self._graph(),
                spec_directory="evals/demo",
            )

    def test_directory_dependency_expands_exact_manifest_file_members(self) -> None:
        directory = ".github/skills/accessibility/scripts/runtime_a11y"
        nested_directory = f"{directory}/matrix"
        file_a = f"{directory}/__init__.py"
        file_b = f"{nested_directory}/_build.py"
        manifest = dict(self.manifest)
        manifest[file_a] = {
            "resources": ["plugins/hve-core-codex/skills/accessibility/scripts/runtime_a11y/__init__.py"]
        }
        manifest[nested_directory] = {
            "resources": ["plugins/hve-core-codex/skills/accessibility/scripts/runtime_a11y/matrix"]
        }
        manifest[file_b] = {
            "resources": ["plugins/hve-core-codex/skills/accessibility/scripts/runtime_a11y/matrix/_build.py"]
        }
        graph = [
            {
                "kind": "skill",
                "source": ".github/skills/accessibility/SKILL.md",
                "dependencies": [directory],
            }
        ]
        document = {
            "environment": {
                "skills": ["../../.github/skills/accessibility"],
            }
        }

        added = supplement_environment_dependencies(
            document,
            artifact_manifest=manifest,
            artifacts=graph,
            spec_directory="evals/demo",
        )

        files = document["environment"]["files"]
        self.assertEqual(set(added), {file_a, file_b})
        self.assertEqual({item["dest"] for item in files}, {file_a, file_b})
        self.assertNotIn(nested_directory, {item["dest"] for item in files})
        self.assertEqual(
            {item["src"] for item in files},
            {"../../" + file_a, "../../" + file_b},
        )

    def test_directory_dependency_expansion_is_bounded(self) -> None:
        directory = ".github/skills/accessibility/runtime_a11y"
        file_a = f"{directory}/a.py"
        file_b = f"{directory}/b.py"
        manifest = dict(self.manifest)
        manifest[file_a] = {"resources": [f"plugins/hve-core-codex/resources/{file_a}"]}
        manifest[file_b] = {"resources": [f"plugins/hve-core-codex/resources/{file_b}"]}
        graph = [
            {
                "kind": "skill",
                "source": ".github/skills/accessibility/SKILL.md",
                "dependencies": [directory],
            }
        ]
        document = {"environment": {"skills": ["../../.github/skills/accessibility"]}}
        original = copy.deepcopy(document)

        with patch("hve_codex_porter.vally_dependencies._MAX_DIRECTORY_MEMBERS", 1):
            with self.assertRaisesRegex(PortError, "too many manifest members"):
                supplement_environment_dependencies(
                    document,
                    artifact_manifest=manifest,
                    artifacts=graph,
                    spec_directory="evals/demo",
                )
        self.assertEqual(document, original)

    def test_unmatched_directory_prefix_still_fails_closed(self) -> None:
        directory = ".github/skills/accessibility/runtime_a11y"
        manifest = dict(self.manifest)
        manifest[f"{directory}-archive/file.py"] = {
            "resources": [f"plugins/hve-core-codex/resources/{directory}-archive/file.py"]
        }
        graph = [
            {
                "kind": "skill",
                "source": ".github/skills/accessibility/SKILL.md",
                "dependencies": [directory],
            }
        ]
        document = {"environment": {"skills": ["../../.github/skills/accessibility"]}}

        with self.assertRaisesRegex(PortError, "absent from the artifact manifest"):
            supplement_environment_dependencies(
                document,
                artifact_manifest=manifest,
                artifacts=graph,
                spec_directory="evals/demo",
            )


if __name__ == "__main__":
    unittest.main()
