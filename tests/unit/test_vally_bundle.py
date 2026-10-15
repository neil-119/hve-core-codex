from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from ruamel.yaml import YAML


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.vally_bundle import prepare_vally_bundle  # noqa: E402
from hve_codex_porter.common import PortError  # noqa: E402


class VallyBundlePreparationTests(unittest.TestCase):
    def test_graph_eval_support_uses_manifest_resource_instead_of_local_import(self) -> None:
        identity = "evals/reference/reference.yaml"
        resource = f"plugins/hve-core-codex/resources/{identity}"
        support = self.distribution / resource
        support.parent.mkdir(parents=True)
        support.write_text("name: reference-material\n", encoding="utf-8")
        self.manifest[identity] = {"resources": [resource]}
        (self.distribution / "generated/source-to-output.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )
        inventory_path = self.distribution / "generated/source-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["artifacts"] = [{
            "source": ".github/agents/security-review.agent.md", "kind": "agent",
            "dependencies": [identity],
        }]
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        output = self.root / "eval-support-bundle"
        report = prepare_vally_bundle(self.upstream, self.distribution, output)
        self.assertTrue(report["success"], report)
        spec = YAML(typ="safe").load((output / "evals/authentication/eval.yaml").read_text())
        entry = next(item for item in spec["environment"]["files"]
                     if item["dest"].endswith("resources/evals/reference/reference.yaml"))
        self.assertEqual(entry["src"], resource)
        self.assertEqual((output / "evals/authentication" / resource).read_bytes(), support.read_bytes())

    def test_inventory_dependencies_stage_support_without_changing_task_or_grading(self) -> None:
        identity = ".github/instructions/security.instructions.md"
        resource = "plugins/hve-core-codex/resources/.github/instructions/security.instructions.md"
        support = self.distribution / resource
        support.parent.mkdir(parents=True)
        support.write_text("# Required security guidance\n", encoding="utf-8")
        self.manifest[identity] = {"resources": [resource], "outputs": [resource]}
        (self.distribution / "generated/source-to-output.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )
        inventory_path = self.distribution / "generated/source-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["artifacts"] = [{
            "source": ".github/agents/security-review.agent.md", "kind": "agent",
            "dependencies": [identity],
        }]
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        output = self.root / "dependency-bundle"
        report = prepare_vally_bundle(self.upstream, self.distribution, output)
        self.assertTrue(report["success"], report)
        self.assertEqual(report["summary"]["dependency_file_supplements"], 1)
        converted = YAML(typ="safe").load((output / "evals/authentication/eval.yaml").read_text())
        original = YAML(typ="safe").load(self.spec_bytes)
        self.assertEqual(converted["stimuli"][0]["prompt"], original["stimuli"][0]["prompt"])
        files = converted["environment"]["files"]
        entry = next(item for item in files if item["dest"] == identity)
        self.assertEqual((output / "evals/authentication" / entry["src"]).read_bytes(), support.read_bytes())
        self.assertEqual(len(converted["stimuli"][0]["grading_environment"]["files"]), 1)
        self.assertEqual((output / "upstream/evals/authentication/eval.yaml").read_bytes(), self.spec_bytes)

    def test_whole_skill_directory_fixture_resolves_exact_mapped_members(self) -> None:
        output = self.root / "directory-fixture-bundle"
        raw = self.spec_bytes.replace(
            b"      dest: roles/security-review.md\n",
            b"      dest: roles/security-review.md\n"
            b"    - src: ../../.github/skills/security/owasp-top-10\n"
            b"      dest: .github/skills/security/owasp-top-10\n",
        )
        self.spec_path.write_bytes(raw)
        report = prepare_vally_bundle(self.upstream, self.distribution, output)
        self.assertTrue(report["success"], report)
        spec = YAML(typ="safe").load((output / "evals/authentication/eval.yaml").read_text())
        resource = spec["environment"]["files"][-1]["src"]
        directory = output / "evals/authentication" / resource
        self.assertTrue((directory / "SKILL.md").is_file())
        self.assertTrue((directory / "scripts/check.py").is_file())
        self.assertEqual((output / "upstream/evals/authentication/eval.yaml").read_bytes(), raw)

    def test_custom_specs_preserve_upstream_bytes_and_separate_originals(self) -> None:
        baseline = self.root / "baseline"
        prepare_vally_bundle(self.upstream, self.distribution, baseline)
        custom = self.root / "custom"
        case = custom / "evals" / "codex-port" / "handoff"
        case.mkdir(parents=True)
        (case / "fixture.txt").write_text("synthetic handoff\n", encoding="utf-8")
        raw = (
            "name: codex-port-handoff\nexecutor: copilot-sdk\n"
            "defaults:\n  runs: 2\n"
            "environment:\n  files:\n    - src: fixture.txt\n      dest: handoff.txt\n"
            "stimuli:\n  - name: check-handoff\n    prompt: Preserve the approval gate.\n"
            "    graders:\n      - type: output-matches\n        pattern: approval\n"
        ).encode()
        (case / "eval.yaml").write_bytes(raw)
        output = self.root / "with-custom"
        report = prepare_vally_bundle(self.upstream, self.distribution, output, custom_dir=custom)
        self.assertTrue(report["success"])
        self.assertEqual((output / "generated/source-to-output.json").read_bytes(), (self.distribution / "generated/source-to-output.json").read_bytes())
        self.assertEqual(report["summary"]["upstream_eval_specs_converted"], 1)
        self.assertEqual(report["summary"]["custom_eval_specs_converted"], 1)
        self.assertEqual((output / "evals/authentication/eval.yaml").read_bytes(), (baseline / "evals/authentication/eval.yaml").read_bytes())
        self.assertEqual((output / "custom/evals/codex-port/handoff/eval.yaml").read_bytes(), raw)
        self.assertEqual(
            (output / "custom/evals/codex-port/handoff/fixture.txt").read_bytes(),
            (case / "fixture.txt").read_bytes(),
        )
        self.assertFalse((output / "upstream/evals/codex-port/handoff/eval.yaml").exists())
        converted = json.loads((output / "evals/codex-port/handoff/eval.yaml").read_text())
        self.assertEqual(converted["defaults"]["runs"], 2)
        self.assertEqual(converted["stimuli"][0]["graders"][0]["pattern"], "approval")
        source_map = json.loads((output / "generated/vally-source-to-output.json").read_text())
        self.assertEqual(source_map["evals/codex-port/handoff/eval.yaml"]["origin"], "codex-custom")

    def test_custom_source_cannot_replace_upstream_file(self) -> None:
        custom = self.root / "custom"
        for root in (custom, self.upstream):
            path = root / "evals" / "codex-port" / "same.txt"
            path.parent.mkdir(parents=True)
            path.write_text("fixture", encoding="utf-8")
        output = self.root / "collision-output"
        with self.assertRaisesRegex(PortError, "collides"):
            prepare_vally_bundle(self.upstream, self.distribution, output, custom_dir=custom)
        self.assertFalse(output.exists())

    def test_custom_source_requires_distinct_namespace(self) -> None:
        custom = self.root / "custom"
        (custom / "evals").mkdir(parents=True)
        (custom / "evals" / "eval.yaml").write_text("name: unsafe", encoding="utf-8")
        with self.assertRaisesRegex(PortError, "namespace"):
            prepare_vally_bundle(self.upstream, self.distribution, self.root / "unsafe", custom_dir=custom)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.upstream = self.root / "upstream"
        self.distribution = self.root / "distribution"
        self.upstream.mkdir()
        (self.upstream / "evals" / "authentication" / "fixtures").mkdir(parents=True)
        (self.distribution / "generated").mkdir(parents=True)
        (self.distribution / "plugins" / "hve-core-codex" / "resources" / "agents").mkdir(parents=True)
        skill_resource = self.distribution / "plugins" / "hve-core-codex" / "skills" / "owasp-top-10"
        (skill_resource / "scripts").mkdir(parents=True)
        (skill_resource / "SKILL.md").write_text("# OWASP top 10 review\n", encoding="utf-8")
        (skill_resource / "scripts" / "check.py").write_text("print('fixture')\n", encoding="utf-8")

        self.resource_path = "plugins/hve-core-codex/resources/agents/security-review.md"
        self.resource_bytes = b"# Converted security review role\n"
        resource = self.distribution / self.resource_path
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(self.resource_bytes)

        self.manifest = {
            ".github/skills/security/owasp-top-10/SKILL.md": {
                "originals": ["plugins/hve-core-codex/originals/.github/skills/security/owasp-top-10/SKILL.md"],
                "outputs": ["plugins/hve-core-codex/skills/owasp-top-10/SKILL.md"],
                "resources": ["plugins/hve-core-codex/skills/owasp-top-10/SKILL.md"],
            },
            ".github/skills/security/owasp-top-10/scripts/check.py": {
                "originals": ["plugins/hve-core-codex/originals/.github/skills/security/owasp-top-10/scripts/check.py"],
                "outputs": ["plugins/hve-core-codex/skills/owasp-top-10/scripts/check.py"],
                "resources": ["plugins/hve-core-codex/skills/owasp-top-10/scripts/check.py"],
            },
            ".github/agents/security-review.agent.md": {
                "originals": ["plugins/hve-core-codex/originals/.github/agents/security-review.agent.md"],
                "outputs": [self.resource_path],
                "resources": [self.resource_path],
            }
        }
        (self.distribution / "generated" / "source-to-output.json").write_text(
            json.dumps(self.manifest, sort_keys=True), encoding="utf-8"
        )
        (self.distribution / "generated" / "source-inventory.json").write_text(
            json.dumps({
                "upstream_sha": "abc123",
                "upstream_ref": "main",
                "upstream_url": "https://example.invalid/upstream.git",
                "channel": "test",
            }, sort_keys=True),
            encoding="utf-8",
        )

        (self.upstream / ".vally.yaml").write_text(
            "paths:\n  evals: evals\n  results: results\n  evalFilenames: [eval.yaml]\n",
            encoding="utf-8",
        )
        (self.upstream / "evals" / "authentication" / "fixtures" / "secret-sample.json").write_text(
            '{"secret": "example", "label": "fixture"}\n', encoding="utf-8"
        )
        (self.upstream / "evals" / "authentication" / "fixtures" / "request.json").write_text(
            '{"request": "review this input"}\n', encoding="utf-8"
        )
        self.spec_path = self.upstream / "evals" / "authentication" / "eval.yaml"
        self.spec_bytes = (
            "name: authentication-review\n"
            "executor: copilot-sdk\n"
            "environment:\n"
            "  skills:\n"
            "    - ../../.github/skills/security/owasp-top-10\n"
            "  files:\n"
            "    - src: ../../.github/agents/security-review.agent.md\n"
            "      dest: roles/security-review.md\n"
            "stimuli:\n"
            "  - name: review-request\n"
            "    executor: copilot-sdk\n"
            "    prompt: |\n"
            "      Preserve the exact prompt whitespace and every line below.\n"
            "      This line is deliberately longer than the usual YAML emitter width so wrapping is exercised.\n"
            "      ```diff\n"
            "      -return resource.ownerId === user.id;\n"
            "      +return true;\n"
            "      ```\n"
            "      The closing fence above must remain at column zero in the decoded value.\n"
            "    environment:\n"
            "      files:\n"
            "        - src: fixtures/request.json\n"
            "          dest: input/request.json\n"
            "    grading_environment:\n"
            "      files:\n"
            "        - src: ../../.github/agents/security-review.agent.md\n"
            "          dest: grader/security-review.md\n"
        ).encode("utf-8")
        self.spec_path.write_bytes(self.spec_bytes)

    def test_repository_contract_materializes_context_without_declared_refs(self):
        resource = "plugins/hve-core-codex/resources/.github/agents/security-review.agent.md"
        target = self.distribution / resource
        target.parent.mkdir(parents=True)
        target.write_bytes(self.resource_bytes)
        self.manifest[".github/agents/security-review.agent.md"]["resources"] = [resource]
        support = "plugins/hve-core-codex/resources/scripts/linting/schemas/state.schema.json"
        (self.distribution / support).parent.mkdir(parents=True)
        (self.distribution / support).write_text('{}')
        self.manifest["scripts/linting/schemas/state.schema.json"] = {"resources": [support]}
        (self.distribution / "generated/source-to-output.json").write_text(json.dumps(self.manifest))
        self.spec_path.write_text("name: agent-conformance-fixture\nexecutor: copilot-sdk\nstimuli:\n  - name: launch\n    prompt: Launch the security reviewer.\n", encoding="utf-8")
        output = self.root / "repository-context-bundle"
        report = prepare_vally_bundle(self.upstream, self.distribution, output)
        self.assertTrue(report["success"], report)
        converted = YAML(typ="safe").load((output / "evals/authentication/eval.yaml").read_text())
        self.assertIn("environment", converted["stimuli"][0])
        for item in converted["stimuli"][0]["environment"]["files"]:
            self.assertTrue((output / "evals/authentication" / item["src"]).exists(), item)
        resource = "plugins/hve-core-codex/resources/.github/agents/security-review.agent.md"
        self.assertEqual((output / "evals/authentication" / resource).read_bytes(), (self.distribution / resource).read_bytes())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_converts_spec_and_rebases_copied_resources_without_running_anything(self) -> None:
        output = self.root / "bundle"

        report = prepare_vally_bundle(self.upstream, self.distribution, output)

        converted_path = output / "evals" / "authentication" / "eval.yaml"
        converted = YAML(typ="safe").load(converted_path.read_text(encoding="utf-8"))
        original = YAML(typ="safe").load(self.spec_bytes.decode("utf-8"))
        self.assertEqual(converted["executor"], "codex")
        self.assertEqual(converted["stimuli"][0]["executor"], "codex")
        self.assertEqual(converted["stimuli"][0]["prompt"], original["stimuli"][0]["prompt"])
        self.assertTrue(converted["stimuli"][0]["prompt"].endswith("```\nThe closing fence above must remain at column zero in the decoded value.\n"))
        self.assertEqual(converted["environment"]["skills"], ["plugins/hve-core-codex/skills/owasp-top-10"])
        self.assertEqual(
            (output / "evals" / "authentication" / "plugins" / "hve-core-codex" / "skills" / "owasp-top-10" / "SKILL.md").read_text(encoding="utf-8"),
            "# OWASP top 10 review\n",
        )
        self.assertEqual(
            (output / "evals" / "authentication" / "plugins" / "hve-core-codex" / "skills" / "owasp-top-10" / "scripts" / "check.py").read_text(encoding="utf-8"),
            "print('fixture')\n",
        )
        self.assertEqual(
            converted["environment"]["files"][0]["src"],
            self.resource_path,
        )
        self.assertEqual(
            converted["stimuli"][0]["environment"]["files"][0]["src"],
            "fixtures/request.json",
        )
        self.assertEqual(
            converted["stimuli"][0]["grading_environment"]["files"],
            [{"src": self.resource_path, "dest": "grader/security-review.md"}],
        )
        self.assertEqual(
            (output / "evals" / "authentication" / self.resource_path).read_bytes(),
            self.resource_bytes,
        )
        self.assertEqual(
            (output / "upstream" / "evals" / "authentication" / "eval.yaml").read_bytes(),
            self.spec_bytes,
        )
        self.assertEqual(
            (output / "upstream" / ".vally.yaml").read_bytes(),
            (self.upstream / ".vally.yaml").read_bytes(),
        )
        self.assertEqual(
            (output / "evals" / "authentication" / "fixtures" / "secret-sample.json").read_bytes(),
            (self.upstream / "evals" / "authentication" / "fixtures" / "secret-sample.json").read_bytes(),
            "auth/secret fixture names and explicit example values remain test data, not path-based exclusions",
        )
        self.assertTrue(report["success"])
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["source"]["source_inventory"]["upstream_sha"], "abc123")
        self.assertEqual(report["source"]["source_inventory"]["upstream_ref"], "main")
        self.assertEqual(report["source"]["source_inventory"]["channel"], "test")
        self.assertEqual(report["execution"], {
            "commands_executed": False,
            "upstream_scripts_executed": False,
            "upstream_executors_run": False,
            "upstream_graders_run": False,
            "models_run": False,
        })
        source_map = json.loads((output / "generated" / "vally-source-to-output.json").read_text(encoding="utf-8"))
        self.assertIn("evals/authentication/fixtures/request.json", source_map)
        self.assertIn(f"evals/authentication/{self.resource_path}", [
            entry["path"] for entry in source_map[".github/agents/security-review.agent.md"]["outputs"]
        ])

    def test_stages_native_role_and_plugin_resources_only_for_runtime_environments(self) -> None:
        profile_source = "codex-overlay/agents/security-review.toml"
        profile_bytes = (
            b'name = "security-review"\n'
            b'description = "Review security-sensitive changes."\n'
            b'developer_instructions = "Read @@HVE_PLUGIN_ROOT@@/resources/agents/security-review.md"\n'
        )
        profile_path = self.distribution / profile_source
        profile_path.parent.mkdir(parents=True)
        profile_path.write_bytes(profile_bytes)

        nested_resource = "plugins/hve-core-codex/resources/agents/reviewer-guide.md"
        nested_resource_bytes = b"Use the security review checklist.\n"
        nested_resource_path = self.distribution / nested_resource
        nested_resource_path.write_bytes(nested_resource_bytes)

        self.manifest[".github/agents/security-review.agent.md"]["outputs"].append(profile_source)
        (self.distribution / "generated" / "source-to-output.json").write_text(
            json.dumps(self.manifest, sort_keys=True), encoding="utf-8"
        )
        output = self.root / "native-role-bundle"

        report = prepare_vally_bundle(self.upstream, self.distribution, output)

        self.assertTrue(report["success"], report)
        self.assertEqual(report["summary"]["native_role_profiles"], 1)
        converted = YAML(typ="safe").load(
            (output / "evals" / "authentication" / "eval.yaml").read_text(encoding="utf-8")
        )
        runtime_files = converted["environment"]["files"]
        self.assertIn(
            {"src": profile_source, "dest": ".codex/agents/security-review.toml"},
            runtime_files,
        )
        self.assertIn(
            {"src": "plugins/hve-core-codex/resources", "dest": ".agents/resources"},
            runtime_files,
        )
        self.assertIn(
            {
                "src": "plugins/hve-core-codex/resources",
                "dest": ".codex/hve-core-codex/plugin-resources/resources",
            },
            runtime_files,
        )

        staged_profile = (output / "evals" / "authentication" / profile_source).read_bytes()
        self.assertEqual(
            staged_profile,
            profile_bytes.replace(
                b"@@HVE_PLUGIN_ROOT@@",
                b".codex/hve-core-codex/plugin-resources",
            ),
        )
        staged_resource_root = output / "evals" / "authentication" / "plugins" / "hve-core-codex" / "resources"
        self.assertEqual((staged_resource_root / "agents" / "security-review.md").read_bytes(), self.resource_bytes)
        self.assertEqual((staged_resource_root / "agents" / "reviewer-guide.md").read_bytes(), nested_resource_bytes)

        grading_files = converted["stimuli"][0]["grading_environment"]["files"]
        self.assertEqual(grading_files, [{"src": self.resource_path, "dest": "grader/security-review.md"}])
        self.assertFalse(any(item["src"].startswith("codex-overlay/agents/") for item in grading_files))
        self.assertFalse(any(item["src"] == "plugins/hve-core-codex/resources" for item in grading_files))

        source_map = json.loads((output / "generated" / "vally-source-to-output.json").read_text(encoding="utf-8"))
        self.assertTrue(any(
            entry["path"] == f"evals/authentication/{profile_source}"
            and entry["status"] == "native-role-profile"
            for entry in source_map[profile_source]["outputs"]
        ))

    def test_missing_manifest_mapping_is_reported_and_original_spec_is_retained(self) -> None:
        del self.manifest[".github/agents/security-review.agent.md"]
        (self.distribution / "generated" / "source-to-output.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )
        output = self.root / "partial-bundle"

        report = prepare_vally_bundle(self.upstream, self.distribution, output)

        self.assertFalse(report["success"])
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["unsupported"]["conversion_errors"])
        self.assertEqual(
            (output / "evals" / "authentication" / "eval.yaml").read_bytes(),
            self.spec_bytes,
        )
        self.assertEqual(
            (output / "upstream" / "evals" / "authentication" / "eval.yaml").read_bytes(),
            self.spec_bytes,
        )
        self.assertEqual(report["execution"]["models_run"], False)

    def test_source_map_is_deterministic_across_output_locations(self) -> None:
        first = self.root / "bundle-one"
        second = self.root / "bundle-two"

        prepare_vally_bundle(self.upstream, self.distribution, first)
        prepare_vally_bundle(self.upstream, self.distribution, second)

        self.assertEqual(
            (first / "generated" / "vally-source-to-output.json").read_bytes(),
            (second / "generated" / "vally-source-to-output.json").read_bytes(),
        )
        self.assertEqual(
            (first / "evals" / "authentication" / "eval.yaml").read_bytes(),
            (second / "evals" / "authentication" / "eval.yaml").read_bytes(),
        )


    def test_safe_yaml_rejects_custom_constructors_without_execution(self) -> None:
        marker = self.root / "should-not-exist"
        raw = (
            f"value: !!python/object/apply:builtins.open ['{marker.as_posix()}', 'w']\n"
        ).encode("utf-8")
        self.spec_path.write_bytes(raw)
        output = self.root / "custom-tag-bundle"

        report = prepare_vally_bundle(self.upstream, self.distribution, output)

        self.assertFalse(marker.exists(), "safe YAML parsing must not execute custom constructors")
        self.assertEqual(report["status"], "partial")
        errors = report["unsupported"]["conversion_errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["source"], "evals/authentication/eval.yaml")
        self.assertIn("Could not parse YAML", errors[0]["error"])
        self.assertEqual((output / "evals/authentication/eval.yaml").read_bytes(), raw)
        self.assertEqual((output / "upstream/evals/authentication/eval.yaml").read_bytes(), raw)

    def test_recursive_yaml_alias_is_rejected_and_original_spec_is_preserved(self) -> None:
        raw = b"name: recursive\ncycle: &cycle [*cycle]\n"
        self.spec_path.write_bytes(raw)
        output = self.root / "recursive-bundle"

        report = prepare_vally_bundle(self.upstream, self.distribution, output)

        self.assertEqual(report["status"], "partial")
        errors = report["unsupported"]["conversion_errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["source"], "evals/authentication/eval.yaml")
        self.assertIn("Recursive Vally YAML data is not supported", errors[0]["error"])
        self.assertEqual((output / "evals/authentication/eval.yaml").read_bytes(), raw)
        self.assertEqual((output / "upstream/evals/authentication/eval.yaml").read_bytes(), raw)

    def test_symlinked_eval_file_is_reported_and_never_copied(self) -> None:
        outside = self.root / "outside.yaml"
        outside.write_text("name: outside-secret\n", encoding="utf-8")
        link = self.upstream / "evals" / "authentication" / "linked.yaml"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        output = self.root / "symlink-bundle"

        report = prepare_vally_bundle(self.upstream, self.distribution, output)

        self.assertEqual(report["status"], "partial")
        self.assertTrue(any(
            item["code"] == "symlink-skipped" and item["source"] == "evals/authentication/linked.yaml"
            for item in report["findings"]
        ))
        self.assertFalse((output / "evals/authentication/linked.yaml").exists())
        self.assertFalse((output / "upstream/evals/authentication/linked.yaml").exists())


if __name__ == "__main__":
    unittest.main()
