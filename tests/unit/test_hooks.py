from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.hook_runtime import (  # noqa: E402
    MAX_CONTEXT_BYTES,
    _fallback_match_rules,
    extract_apply_patch_paths,
    extract_prompt_paths,
    extract_tool_paths,
    find_registry_root,
    handle_event,
)
from hve_codex_porter.rules import match_rules  # noqa: E402


class HookRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.rules_dir = self.root / "rules"
        self.rules_dir.mkdir()
        self.state_dir = self.root / "state"
        self.previous_state_dir = os.environ.get("HVE_CODEX_HOOK_STATE_DIR")
        os.environ["HVE_CODEX_HOOK_STATE_DIR"] = str(self.state_dir)

    def tearDown(self) -> None:
        if self.previous_state_dir is None:
            os.environ.pop("HVE_CODEX_HOOK_STATE_DIR", None)
        else:
            os.environ["HVE_CODEX_HOOK_STATE_DIR"] = self.previous_state_dir
        self.temporary.cleanup()

    def write_registry(self, content: str = "Follow the project rule.", apply_to: list[str] | None = None) -> None:
        (self.rules_dir / "style.md").write_text(content, encoding="utf-8")
        (self.rules_dir / "index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "style",
                            "source": ".github/instructions/style.instructions.md",
                            "path": "style.md",
                            "apply_to": apply_to or ["src/**/*.py", "tests/**/*.py"],
                            "precedence": 10,
                            "imports": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_prompt_hints_inject_only_relevant_rules(self) -> None:
        self.write_registry()
        result = handle_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "cwd": str(self.root),
                "prompt": "Please update `src/app.py` and explain the changes.",
            }
        )

        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "UserPromptSubmit")
        self.assertIn("src/app.py", output["additionalContext"])
        self.assertIn("Follow the project rule.", output["additionalContext"])

    def test_session_and_subagent_start_load_global_but_not_specialty_rules(self) -> None:
        self.write_registry(content="PYTHON_ONLY")
        registry_path = self.rules_dir / "index.json"
        registry = json.loads(registry_path.read_text())
        (self.rules_dir / "global.md").write_text("ALWAYS_ON", encoding="utf-8")
        (self.rules_dir / "manual.md").write_text("ON_DEMAND_ONLY", encoding="utf-8")
        for name, auto_attach in (("global", True), ("manual", False)):
            registry["rules"].append({"id": name, "source": f"{name}.md", "path": f"{name}.md",
                "apply_to": [], "auto_attach": auto_attach, "precedence": 0, "imports": []})
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        for event in ("SessionStart", "SubagentStart"):
            with self.subTest(event=event):
                result = handle_event({"hook_event_name": event, "session_id": event, "cwd": str(self.root)})
                context = result["hookSpecificOutput"]["additionalContext"]
                self.assertIn("ALWAYS_ON", context)
                self.assertNotIn("PYTHON_ONLY", context)
                self.assertNotIn("ON_DEMAND_ONLY", context)

    def test_absolute_target_from_nested_cwd_uses_repository_root(self) -> None:
        overlay_rules = self.root / "codex-overlay" / "rules"
        overlay_rules.mkdir(parents=True)
        (self.rules_dir / "style.md").write_text("Nested cwd still finds the project rule.", encoding="utf-8")
        (self.rules_dir / "index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "style",
                            "source": ".github/instructions/style.instructions.md",
                            "path": "style.md",
                            "apply_to": ["src/**/*.py"],
                            "precedence": 10,
                            "imports": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.rules_dir / "style.md").replace(overlay_rules / "style.md")
        (self.rules_dir / "index.json").replace(overlay_rules / "index.json")
        nested_cwd = self.root / "packages" / "app" / "src"
        nested_cwd.mkdir(parents=True)
        target = self.root / "src" / "app.py"

        result = handle_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "nested-root-session",
                "turn_id": "nested-root-turn",
                "cwd": str(nested_cwd),
                "prompt": f"Please inspect `{target}`.",
            }
        )

        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("src/app.py", context)
        self.assertIn("Nested cwd still finds the project rule.", context)

    def test_prompt_without_recognized_path_does_not_inject_specialty_rule(self) -> None:
        self.write_registry()
        result = handle_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "cwd": str(self.root),
                "prompt": "What conventions should I keep in mind?",
            }
        )
        self.assertIsNone(result)

    def test_finds_project_codex_rules_directory(self) -> None:
        self.write_registry()
        codex_rules = self.root / ".codex" / "rules"
        codex_rules.mkdir(parents=True)
        (self.rules_dir / "index.json").replace(codex_rules / "index.json")
        (self.rules_dir / "style.md").replace(codex_rules / "style.md")

        self.assertEqual(find_registry_root(self.root), self.root / ".codex")

    def test_session_and_subagent_start_return_small_indexing_guidance(self) -> None:
        self.write_registry()
        session_result = handle_event(
            {"hook_event_name": "SessionStart", "session_id": "session-1", "cwd": str(self.root)}
        )
        subagent_result = handle_event(
            {
                "hook_event_name": "SubagentStart",
                "session_id": "session-1",
                "turn_id": "turn-2",
                "agent_id": "agent-1",
                "cwd": str(self.root),
            }
        )
        self.assertIn("hve-codex rules explain", session_result["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(subagent_result["hookSpecificOutput"]["hookEventName"], "SubagentStart")

    def test_apply_patch_paths_include_add_update_and_rename_targets(self) -> None:
        patch = """*** Begin Patch
*** Update File: src/app.py
*** Add File: docs/new.md
*** Move to: src/renamed.py
*** End Patch"""

        self.assertEqual(
            extract_apply_patch_paths(patch, self.root),
            ["src/app.py", "docs/new.md", "src/renamed.py"],
        )

    def test_tool_paths_follow_hook_input_shapes_for_patch_read_and_shell(self) -> None:
        self.write_registry()
        patch_paths, uncertain = extract_tool_paths(
            "apply_patch",
            {"command": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch"},
            self.root,
        )
        self.assertEqual(patch_paths, ["src/app.py"])
        self.assertFalse(uncertain)

        read_paths, uncertain = extract_tool_paths(
            "mcp__filesystem__read_file", {"file_path": "src/app.py"}, self.root
        )
        self.assertEqual(read_paths, ["src/app.py"])
        self.assertFalse(uncertain)

        multi_read_paths, _ = extract_tool_paths(
            "mcp__filesystem__read_multiple_files",
            {"paths": ["src/app.py", "docs/guide.md"]},
            self.root,
        )
        self.assertEqual(multi_read_paths, ["src/app.py", "docs/guide.md"])

        shell_paths, uncertain = extract_tool_paths(
            "Bash", {"command": "sed -n '1,40p' src/app.py"}, self.root
        )
        self.assertEqual(shell_paths, ["src/app.py"])
        self.assertFalse(uncertain)

        absolute_target = self.root / "src" / "app.py"
        absolute_paths, _ = extract_tool_paths(
            "Bash", {"command": f'cat "{absolute_target}"'}, self.root
        )
        self.assertEqual(absolute_paths, ["src/app.py"])

        variable_paths, uncertain = extract_tool_paths(
            "Bash", {"command": 'rm -rf "$TARGET/src/app.py"'}, self.root
        )
        self.assertEqual(variable_paths, [])
        self.assertTrue(uncertain)

        powershell_variable_paths, uncertain = extract_tool_paths(
            "Bash", {"command": 'Remove-Item "$env:TARGET\\src\\app.py"'}, self.root
        )
        self.assertEqual(powershell_variable_paths, [])
        self.assertTrue(uncertain)

    def test_non_patch_paths_keep_a_and_b_directory_names(self) -> None:
        self.assertEqual(extract_prompt_paths("Update b/src/app.py", self.root), ["b/src/app.py"])
        self.assertEqual(
            extract_apply_patch_paths("--- a/src/old.py\n+++ b/src/new.py", self.root),
            ["src/old.py", "src/new.py"],
        )

    def test_pre_tool_use_injects_matching_rule_for_patch_target(self) -> None:
        self.write_registry()
        result = handle_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "tool_name": "apply_patch",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch"},
                "cwd": str(self.root),
            }
        )
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertIn("src/app.py", result["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("permissionDecision", result["hookSpecificOutput"])

    def test_shell_target_inference_is_advisory_when_path_is_concealed(self) -> None:
        self.write_registry()
        paths, uncertain = extract_tool_paths("Bash", {"command": "rm -rf $TARGET"}, self.root)
        self.assertEqual(paths, [])
        self.assertTrue(uncertain)

        partial_paths, uncertain = extract_tool_paths(
            "Bash", {"command": "rm -rf src/app.py $TARGET"}, self.root
        )
        self.assertEqual(partial_paths, ["src/app.py"])
        self.assertTrue(uncertain)

        result = handle_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "rm -rf $TARGET"},
                "cwd": str(self.root),
            }
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("target paths could not be inferred", context)
        self.assertNotIn("permissionDecision", result["hookSpecificOutput"])

        partial = handle_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-2",
                "turn_id": "turn-1",
                "tool_name": "Bash",
                "tool_use_id": "tool-2",
                "tool_input": {"command": "rm -rf src/app.py $TARGET"},
                "cwd": str(self.root),
            }
        )
        partial_context = partial["hookSpecificOutput"]["additionalContext"]
        self.assertIn("may miss variable", partial_context)
        self.assertIn("Follow the project rule.", partial_context)

    def test_exec_command_cmd_field_gets_shell_uncertainty_advisory(self) -> None:
        self.write_registry()
        paths, uncertain = extract_tool_paths("exec_command", {"cmd": "rm -rf $TARGET"}, self.root)
        self.assertEqual(paths, [])
        self.assertTrue(uncertain)

        result = handle_event(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "exec-command-session",
                "turn_id": "exec-command-turn",
                "tool_name": "exec_command",
                "tool_input": {"cmd": "rm -rf $TARGET"},
                "cwd": str(self.root),
            }
        )

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("target paths could not be inferred", context)
        self.assertNotIn("permissionDecision", result["hookSpecificOutput"])

    def test_hook_context_deduplicates_by_session_turn_and_rule_hash(self) -> None:
        self.write_registry()
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.root),
            "prompt": "Please edit src/app.py",
        }
        self.assertIsNotNone(handle_event(payload))
        self.assertIsNone(handle_event(payload))

        next_turn = {**payload, "turn_id": "turn-2"}
        self.assertIsNotNone(handle_event(next_turn))

        same_turn_tool = {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "apply_patch",
            "tool_use_id": "tool-1",
            "tool_input": {"command": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch"},
            "cwd": str(self.root),
        }
        self.assertIsNotNone(handle_event(same_turn_tool))
        self.assertIsNone(handle_event(same_turn_tool))
        self.assertIsNotNone(handle_event({**same_turn_tool, "tool_use_id": "tool-2"}))

    def test_start_events_deliver_guidance_for_each_compaction_and_subagent(self) -> None:
        self.write_registry()
        session_start = {
            "hook_event_name": "SessionStart",
            "session_id": "session-starts",
            "source": "compact",
            "cwd": str(self.root),
        }
        self.assertIsNotNone(handle_event(session_start))
        self.assertIsNotNone(handle_event(session_start))

        subagent_start = {
            "hook_event_name": "SubagentStart",
            "session_id": "session-starts",
            "turn_id": "turn-starts",
            "agent_type": "worker",
            "agent_id": "agent-1",
            "cwd": str(self.root),
        }
        self.assertIsNotNone(handle_event(subagent_start))
        self.assertIsNone(handle_event(subagent_start))
        second_agent = {**subagent_start, "agent_id": "agent-2"}
        self.assertIsNotNone(handle_event(second_agent))

    def test_standalone_rule_fallback_matches_package_resolver(self) -> None:
        (self.rules_dir / "base.md").write_text("Base instructions.", encoding="utf-8")
        (self.rules_dir / "scoped.md").write_text("Scoped instructions.", encoding="utf-8")
        (self.rules_dir / "literal.md").write_text("Literal class instructions.", encoding="utf-8")
        (self.rules_dir / "index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "base",
                            "source": "source/base.instructions.md",
                            "path": "base.md",
                            "apply_to": ["src/**/*.py"],
                            "precedence": 1,
                            "imports": [],
                        },
                        {
                            "id": "scoped",
                            "source": "source/scoped.instructions.md",
                            "path": "scoped.md",
                            "apply_to": ["src/{api,core}/**/*.py", "!src/core/generated/**"],
                            "precedence": 10,
                            "imports": ["base"],
                        },
                        {
                            "id": "literal-class",
                            "source": "source/literal.instructions.md",
                            "path": "literal.md",
                            "apply_to": ["src/[^a].py"],
                            "precedence": 0,
                            "imports": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        paths = ["src/b.py", "src/api/handler.py", "src/core/generated/skip.py"]

        package_rules = match_rules(self.root, paths)
        fallback_rules = _fallback_match_rules(self.root, paths)

        self.assertEqual(
            [(rule["id"], rule["content"]) for rule in fallback_rules],
            [(rule["id"], rule["content"]) for rule in package_rules],
        )

    def test_injected_context_is_capped_at_eight_kibibytes(self) -> None:
        self.write_registry("é" * 10000)
        result = handle_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "cwd": str(self.root),
                "prompt": "Please edit src/app.py",
            }
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context.encode("utf-8")), MAX_CONTEXT_BYTES)

    def test_malformed_dedup_state_does_not_hide_valid_hook_output(self) -> None:
        self.write_registry()
        state_path = self.state_dir / "rule-hook-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"version": 1, "scopes": {f"bad-{index}": [] for index in range(70)}}),
            encoding="utf-8",
        )
        result = handle_event(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-state",
                "turn_id": "turn-state",
                "cwd": str(self.root),
                "prompt": "Please edit src/app.py",
            }
        )

        self.assertIsNotNone(result)
        self.assertIn("Follow the project rule.", result["hookSpecificOutput"]["additionalContext"])

    def test_prompt_path_extraction_ignores_urls_and_accepts_multiple_targets(self) -> None:
        paths = extract_prompt_paths(
            "Change src/app.py and tests/test_app.py; docs at https://example.test/path/page.html",
            self.root,
        )
        self.assertEqual(paths, ["src/app.py", "tests/test_app.py"])

    def test_copied_script_runs_directly_and_emits_hook_json(self) -> None:
        self.write_registry()
        source_script = SOURCE_ROOT / "hve_codex_porter" / "hook_runtime.py"
        script = self.root / "copied" / "hooks" / "hve_rules.py"
        script.parent.mkdir(parents=True)
        script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-standalone",
            "turn_id": "turn-standalone",
            "cwd": str(self.root),
            "prompt": "Please update src/app.py",
        }
        environment = dict(os.environ)
        environment["HVE_CODEX_HOOK_STATE_DIR"] = str(self.state_dir)
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=self.root,
            env=environment,
            check=True,
        )
        emitted = json.loads(completed.stdout)
        self.assertEqual(emitted["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("Follow the project rule.", emitted["hookSpecificOutput"]["additionalContext"])

    def test_copied_script_rules_match_uses_stdlib_fallback_without_state_changes(self) -> None:
        rules_dir = self.root / ".codex" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "base.md").write_text("Imported base guidance.", encoding="utf-8")
        (rules_dir / "source.md").write_text("Source-only guidance.", encoding="utf-8")
        (rules_dir / "index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "base",
                            "source": "base.instructions.md",
                            "path": "base.md",
                            "apply_to": ["never/**"],
                            "precedence": 1,
                            "imports": [],
                        },
                        {
                            "id": "source",
                            "source": "source.instructions.md",
                            "path": "source.md",
                            "apply_to": ["src/**/*.py", "!src/generated/**"],
                            "precedence": 10,
                            "imports": ["base"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        source_script = SOURCE_ROOT / "hve_codex_porter" / "hook_runtime.py"
        script = self.root / ".codex" / "hooks" / "hve_rules.py"
        script.parent.mkdir(parents=True)
        script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")

        environment = dict(os.environ)
        environment["PYTHONPATH"] = ""
        environment["HVE_CODEX_HOOK_STATE_DIR"] = str(self.state_dir)
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(script),
                "--rules-match",
                "src/app.py",
                "src/generated/app.py",
                "notes.txt",
            ],
            text=True,
            capture_output=True,
            cwd=self.root,
            env=environment,
            check=True,
        )

        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        by_path = {target["path"]: target for target in result["targets"]}
        self.assertEqual(by_path["src/app.py"]["rule_ids"], ["base", "source"])
        self.assertIn("Imported base guidance.", by_path["src/app.py"]["rules"][0]["content"])
        self.assertTrue(by_path["src/app.py"]["matched"])
        self.assertEqual(by_path["src/generated/app.py"]["rule_ids"], [])
        self.assertEqual(by_path["notes.txt"]["rule_ids"], [])
        self.assertFalse(self.state_dir.exists())

        explicit_root = subprocess.run(
            [
                sys.executable,
                "-S",
                str(script),
                "--rules-match",
                "src/app.py",
                "--root",
                str(self.root),
            ],
            text=True,
            capture_output=True,
            cwd=self.root.parent,
            env=environment,
            check=True,
        )
        self.assertEqual(json.loads(explicit_root.stdout)["targets"][0]["rule_ids"], ["base", "source"])
        self.assertFalse(self.state_dir.exists())

        unsafe = subprocess.run(
            [sys.executable, "-S", str(script), "--rules-match", "../outside.py", "--root", str(self.root)],
            text=True,
            capture_output=True,
            cwd=self.root,
            env=environment,
        )
        self.assertEqual(unsafe.returncode, 2)
        self.assertIn("unsafe or out-of-project", unsafe.stderr)
        self.assertFalse(self.state_dir.exists())

    def test_copied_script_preserves_unicode_over_legacy_windows_text_pipes(self) -> None:
        content = "Follow the project rule. \U0001f916 \u65e5\u672c\u8a9e"
        self.write_registry(content)
        script = self.root / "hve_rules.py"
        script.write_text((SOURCE_ROOT / "hve_codex_porter/hook_runtime.py").read_text(encoding="utf-8"), encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH="", PYTHONIOENCODING="cp1252")
        commands = [
            (["--rules-match", "src/app.py", "--root", str(self.root)], None),
            ([], json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(self.root), "prompt": "Read src/app.py"})),
        ]
        for arguments, payload in commands:
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    [sys.executable, "-S", str(script), *arguments], input=payload,
                    text=True, encoding="ascii", capture_output=True, cwd=self.root,
                    env=environment, check=True,
                )
                result = json.loads(completed.stdout)
                actual = (result["targets"][0]["rules"][0]["content"] if arguments
                          else result["hookSpecificOutput"]["additionalContext"])
                self.assertIn(content, actual)
                self.assertEqual(completed.stderr, "")

    def test_copied_script_emits_documented_json_for_supported_hook_events(self) -> None:
        self.write_registry()
        source_script = SOURCE_ROOT / "hve_codex_porter" / "hook_runtime.py"
        script = self.root / "installed" / "hooks" / "hve_rules.py"
        script.parent.mkdir(parents=True)
        script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
        common = {
            "session_id": "session-host-contract",
            "transcript_path": None,
            "cwd": str(self.root),
            "model": "test-model",
            "permission_mode": "default",
        }
        patch = "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch"
        payloads = [
            {**common, "hook_event_name": "SessionStart", "source": "compact"},
            {
                **common,
                "hook_event_name": "SubagentStart",
                "turn_id": "turn-host-contract",
                "agent_id": "agent-host-contract",
                "agent_type": "worker",
            },
            {
                **common,
                "hook_event_name": "UserPromptSubmit",
                "turn_id": "turn-host-contract",
                "prompt": "Please edit src/app.py",
            },
            {
                **common,
                "hook_event_name": "PreToolUse",
                "turn_id": "turn-host-contract",
                "tool_name": "apply_patch",
                "tool_use_id": "tool-host-contract",
                "tool_input": {"command": patch},
            },
        ]
        environment = dict(os.environ)
        environment["HVE_CODEX_HOOK_STATE_DIR"] = str(self.state_dir)

        for payload in payloads:
            with self.subTest(event=payload["hook_event_name"]):
                completed = subprocess.run(
                    [sys.executable, str(script)],
                    input=json.dumps(payload),
                    text=True,
                    capture_output=True,
                    cwd=self.root,
                    env=environment,
                    check=True,
                )
                self.assertEqual(completed.stderr, "")
                self.assertEqual(len(completed.stdout.splitlines()), 1)
                emitted = json.loads(completed.stdout)
                self.assertEqual(set(emitted), {"hookSpecificOutput"})
                output = emitted["hookSpecificOutput"]
                self.assertEqual(
                    set(output), {"hookEventName", "additionalContext"}
                )
                self.assertEqual(output["hookEventName"], payload["hook_event_name"])
                self.assertIsInstance(output["additionalContext"], str)
                self.assertTrue(output["additionalContext"])


if __name__ == "__main__":
    unittest.main()
