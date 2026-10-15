from __future__ import annotations

import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.host_runtime import (  # noqa: E402
    DEFAULT_EVAL_MODEL,
    DEFAULT_EVAL_REASONING_EFFORT,
    _agent_default_config_capability,
    _apply_checkpoint_assertions,
    _capture_workspace_checkpoint,
    _check_result,
    _codex_event_summary,
    _codex_executable,
    _evaluation_workspace_parent,
    _execute_codex_turn,
    _existing_project_is_trusted,
    _json_pointer,
    _pin_staged_model_settings,
    _probe_codex,
    _probe_windows_sandbox_readiness,
    _read_windows_sandbox_mode,
    _resolve_entrypoints,
    _scenario_checks,
    _scenario_stage_preconditions,
    _scenario_workspace_priority_paths,
    _source_artifact_evidence,
    _windows_native_sandbox_config_args,
    _validate_native_relative_path,
    _validate_native_attested_path,
    _workspace_snapshot,
    prepare_evaluations,
    run_evaluations,
)
from hve_codex_porter.common import PortError  # noqa: E402
from hve_codex_porter.conversion import convert  # noqa: E402


REPOSITORY = Path(__file__).resolve().parents[2]


def _build_fixture_distribution(root: Path) -> Path:
    """Convert a compact synthetic upstream fixture for cross-subsystem tests."""
    upstream = root / "synthetic-upstream"
    (upstream / ".github/agents/hve-core").mkdir(parents=True)
    (upstream / ".github/skills/research").mkdir(parents=True)
    (upstream / "plugin.json").write_text(
        json.dumps({
            "version": "1.0.0",
            "skills": [".github/skills/research"],
            "agents": [".github/agents/hve-core/rpi-agent.agent.md"],
            "commands": [],
        }),
        encoding="utf-8",
    )
    (upstream / ".github/agents/hve-core/rpi-agent.agent.md").write_text(
        "---\nname: RPI Agent\ndescription: Coordinate the RPI workflow\n"
        "disable-model-invocation: true\n---\nPreserve the user's approval boundaries.\n",
        encoding="utf-8",
    )
    (upstream / ".github/skills/research/SKILL.md").write_text(
        "---\nname: research\ndescription: Research a task\n---\nKeep source evidence intact.\n",
        encoding="utf-8",
    )
    distribution = root / "synthetic-distribution"
    convert(
        upstream,
        distribution,
        {"upstream_ref": "fixture", "upstream_sha": "a" * 40, "channel": "edge"},
        REPOSITORY / "mappings",
    )
    return distribution


class HostRuntimeUtilityTests(unittest.TestCase):
    def test_literal_executable_path_with_spaces_is_not_shell_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "native client.exe"
            executable.write_bytes(b"fixture")
            resolved, error = _codex_executable(str(executable))
            self.assertIsNone(error)
            self.assertEqual(Path(resolved), executable.resolve())
            resolved, error = _codex_executable(str(executable) + " --unsafe-argument")
            self.assertIsNone(resolved)
            self.assertIsNotNone(error)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_windows_npm_codex_layout(
        self,
        name: str = "npm-install",
        *,
        shim_name: str = "codex.cmd",
        architecture: str = "x64",
        root_metadata: dict[str, object] | str | None = None,
        optional_metadata: dict[str, object] | str | None = None,
        install_optional_package: bool = True,
        create_optional_binary: bool = True,
        create_fallback_binary: bool = False,
    ) -> tuple[Path, Path, Path, Path]:
        npm_prefix = self.root / name
        shim = npm_prefix / shim_name
        shim.parent.mkdir(parents=True)
        shim.write_text("# npm shim must not be executed\n", encoding="utf-8")

        package_root = npm_prefix / "node_modules" / "@openai" / "codex"
        package_root.mkdir(parents=True)
        default_root_metadata: dict[str, object] = {
            "name": "@openai/codex",
            "version": "1.2.3",
            "bin": {"codex": "bin/codex.js"},
        }
        package_json_text = root_metadata if isinstance(root_metadata, str) else json.dumps(root_metadata or default_root_metadata)
        (package_root / "package.json").write_text(package_json_text, encoding="utf-8")
        bin_script = package_root / "bin" / "codex.js"
        bin_script.parent.mkdir()
        bin_script.write_text("throw new Error('must not be executed');\n", encoding="utf-8")

        optional_root = package_root / "node_modules" / "@openai" / f"codex-win32-{architecture}"
        native_name = "x86_64-pc-windows-msvc" if architecture == "x64" else "aarch64-pc-windows-msvc"
        optional_binary = optional_root / "vendor" / native_name / "bin" / "codex.exe"
        if install_optional_package:
            optional_root.mkdir(parents=True)
            default_optional_metadata: dict[str, object] = {
                "name": "@openai/codex",
                "version": f"1.2.3-win32-{architecture}",
            }
            optional_json_text = optional_metadata if isinstance(optional_metadata, str) else json.dumps(optional_metadata or default_optional_metadata)
            (optional_root / "package.json").write_text(optional_json_text, encoding="utf-8")
            if create_optional_binary:
                optional_binary.parent.mkdir(parents=True)
                optional_binary.write_bytes(b"native executable fixture")

        fallback_binary = package_root / "vendor" / native_name / "bin" / "codex.exe"
        if create_fallback_binary:
            fallback_binary.parent.mkdir(parents=True)
            fallback_binary.write_bytes(b"fallback executable fixture")
        return shim, package_root, optional_binary, fallback_binary

    def test_windows_npm_shims_resolve_exact_supported_optional_native_binaries(self) -> None:
        cases = [
            ("codex.cmd", "x64", "AMD64"),
            ("codex.ps1", "arm64", "ARM64"),
        ]
        for index, (shim_name, architecture, machine) in enumerate(cases):
            with self.subTest(shim=shim_name, architecture=architecture):
                shim, _, optional_binary, _ = self._write_windows_npm_codex_layout(
                    f"npm-install-{index}",
                    shim_name=shim_name,
                    architecture=architecture,
                )
                with (
                    patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
                    patch("hve_codex_porter.host_runtime.platform.machine", return_value=machine),
                    patch("hve_codex_porter.host_runtime.shutil.which", return_value=str(shim)),
                ):
                    executable, error = _codex_executable("codex")

                self.assertIsNone(error)
                self.assertEqual(Path(executable), optional_binary.resolve())

    def test_windows_npm_shim_uses_package_vendor_fallback_when_optional_package_is_absent(self) -> None:
        shim, _, _, fallback_binary = self._write_windows_npm_codex_layout(
            install_optional_package=False,
            create_fallback_binary=True,
        )
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime.platform.machine", return_value="AMD64"),
            patch("hve_codex_porter.host_runtime.shutil.which", return_value=None),
        ):
            executable, error = _codex_executable(str(shim))

        self.assertIsNone(error)
        self.assertEqual(Path(executable), fallback_binary.resolve())

    def test_windows_npm_shim_rejects_vendor_symlink_outside_package_root(self) -> None:
        shim, _, optional_binary, _ = self._write_windows_npm_codex_layout()
        vendor_root = optional_binary.parents[2]
        optional_binary.unlink()
        optional_binary.parent.rmdir()
        optional_binary.parent.parent.rmdir()
        vendor_root.rmdir()

        escaped_vendor = self.root / "outside-package-vendor"
        escaped_binary = escaped_vendor / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        escaped_binary.parent.mkdir(parents=True)
        escaped_binary.write_bytes(b"outside package fixture")
        try:
            vendor_root.symlink_to(escaped_vendor, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            unsupported_windows_link = os.name == "nt" and (
                isinstance(exc, NotImplementedError)
                or getattr(exc, "winerror", None) in {1, 5, 50, 1314}
            )
            if not unsupported_windows_link:
                raise
            self.skipTest(f"Directory symlink creation is unavailable: {exc}")

        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime.platform.machine", return_value="AMD64"),
            patch("hve_codex_porter.host_runtime.shutil.which", return_value=str(shim)),
        ):
            executable, error = _codex_executable("codex")

        self.assertIsNone(executable)
        self.assertIn("Supported native Windows Codex binary was not found", error)

    def test_windows_npm_shims_reject_unrelated_malformed_and_missing_native_layouts(self) -> None:
        scenarios = [
            {
                "name": "unrelated-shim",
                "shim_name": "other.cmd",
                "expected_error": "Only the npm codex.cmd and codex.ps1 shims",
            },
            {
                "name": "wrong-package-name",
                "root_metadata": {"name": "@example/codex", "version": "1.2.3", "bin": {"codex": "bin/codex.js"}},
                "expected_error": "not the official",
            },
            {
                "name": "wrong-bin-target",
                "root_metadata": {"name": "@openai/codex", "version": "1.2.3", "bin": {"codex": "run.ps1"}},
                "expected_error": "must map codex to bin/codex.js",
            },
            {
                "name": "malformed-package-json",
                "root_metadata": "{malformed",
                "expected_error": "metadata is invalid",
            },
            {
                "name": "mismatched-optional-package-version",
                "optional_metadata": {"name": "@openai/codex", "version": "9.9.9-win32-x64"},
                "expected_error": "version does not match",
            },
            {
                "name": "oversized-package-json",
                "root_metadata": {"name": "@openai/codex", "version": "1.2.3", "bin": {"codex": "bin/codex.js"}, "description": "x" * 70000},
                "expected_error": "exceeds the size limit",
            },
            {
                "name": "missing-optional-native-binary",
                "create_optional_binary": False,
                "create_fallback_binary": True,
                "expected_error": "Supported native Windows Codex binary was not found",
            },
            {
                "name": "missing-native-binary",
                "install_optional_package": False,
                "expected_error": "Supported native Windows Codex binary was not found",
            },
        ]
        for scenario in scenarios:
            with self.subTest(scenario=scenario["name"]):
                kwargs = {key: value for key, value in scenario.items() if key not in {"name", "expected_error"}}
                shim, _, _, _ = self._write_windows_npm_codex_layout(scenario["name"], **kwargs)
                with (
                    patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
                    patch("hve_codex_porter.host_runtime.platform.machine", return_value="AMD64"),
                    patch("hve_codex_porter.host_runtime.shutil.which", return_value=str(shim)),
                ):
                    executable, error = _codex_executable("codex")

                self.assertIsNone(executable)
                self.assertIsNotNone(error)
                self.assertIn(scenario["expected_error"], error)

    def test_windows_npm_shim_rejects_missing_adjacent_package(self) -> None:
        shim = self.root / "npm-missing-package" / "codex.cmd"
        shim.parent.mkdir(parents=True)
        shim.write_text("# inert shim fixture\n", encoding="utf-8")
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime.platform.machine", return_value="AMD64"),
            patch("hve_codex_porter.host_runtime.shutil.which", return_value=str(shim)),
        ):
            executable, error = _codex_executable("codex")

        self.assertIsNone(executable)
        self.assertIsNotNone(error)
        self.assertIn("Adjacent npm Codex package was not found", error)

    def test_non_windows_command_lookup_preserves_existing_shim_resolution(self) -> None:
        shim = self.root / "codex.cmd"
        shim.write_text("inert fixture\n", encoding="utf-8")
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=False),
            patch("hve_codex_porter.host_runtime.shutil.which", return_value=str(shim)),
        ):
            executable, error = _codex_executable("codex")

        self.assertIsNone(error)
        self.assertEqual(executable, str(shim))

    def test_codex_command_arguments_are_rejected_before_windows_shim_resolution(self) -> None:
        with patch("hve_codex_porter.host_runtime.shutil.which") as which:
            executable, error = _codex_executable('"C:\\npm\\codex.cmd" --version')

        self.assertIsNone(executable)
        self.assertEqual(error, "codex_command must identify one executable; arguments are not accepted")
        which.assert_not_called()



class EvaluationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.distribution = _build_fixture_distribution(self.root)
        self.scenarios = self.root / "scenarios.json"
        self.output = self.root / "reports"
        self.windows_config_patch = patch(
            "hve_codex_porter.host_runtime._existing_windows_sandbox_mode",
            return_value=("elevated", None) if os.name == "nt" else (None, None),
        )
        self.windows_config_patch.start()
        self.addCleanup(self.windows_config_patch.stop)
        self.windows_readiness_patch = patch(
            "hve_codex_porter.host_runtime._probe_windows_sandbox_readiness",
            return_value={
                "method": "app-server/windowsSandbox/readiness",
                "status": "ready",
                "readiness_status": "ready",
                "model_turns_started": 0,
                "reason": None,
            },
        )
        self.windows_readiness_patch.start()
        self.addCleanup(self.windows_readiness_patch.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_scenarios(self, scenarios: list[dict], *, scope: str | None = "codex-host") -> None:
        document = {"schema_version": 1, "scenarios": scenarios}
        if scope is not None:
            document["scope"] = scope
        self.scenarios.write_text(
            json.dumps(document, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def one_scenario(*, prompts: list[str] | None = None, checks: list[dict] | None = None) -> dict:
        return {
            "id": "sample-eval",
            "title": "Sample evaluation",
            "prompts": prompts or ["Inspect the fixture and stop."],
            "rubric": ["Report evidence without making unsupported claims."],
            "fixture_files": {"fixture/input.txt": "source content\n"},
            "deterministic_checks": checks or [
                {"id": "fixture-staged", "kind": "fixture_exists", "path": "fixture/input.txt"},
                {"id": "source-unchanged", "kind": "workspace_file_unchanged", "path": "fixture/input.txt"},
            ],
        }

    def test_prepare_installs_full_distribution_and_never_probes_codex(self) -> None:
        self.write_scenarios([self.one_scenario()])
        with patch("hve_codex_porter.host_runtime._probe_codex") as probe:
            report = prepare_evaluations(self.distribution, self.scenarios, self.output)

        probe.assert_not_called()
        self.assertEqual(report["run_mode"], "prepare-only-no-model-sessions")
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertFalse(report["stable_ready"])
        scenario = report["scenarios"][0]
        self.assertEqual(scenario["full_installer"]["status"], "installed")
        self.assertEqual(scenario["deterministic_checks"][0]["status"], "passed")
        self.assertEqual(scenario["deterministic_checks"][1]["status"], "pending-runtime")
        self.assertEqual(scenario["rubric_review"][0]["status"], "manual-review-pending")
        self.assertTrue((self.output / "evaluations-report.json").is_file())

    def test_persistent_stage_is_preserved_and_untrusted_stage_never_runs(self) -> None:
        self.write_scenarios([self.one_scenario()])
        stage = self.root / "reviewed"
        project = stage / "project"
        (project / "fixture").mkdir(parents=True)
        fixture = project / "fixture/input.txt"
        fixture.write_bytes(b"source content\n")
        receipt = {
            "scenario_id": "sample-eval", "project": str(project.resolve()),
            "scenario_definition_sha256": hashlib.sha256(self.scenarios.read_bytes()).hexdigest(),
            "baseline_hashes": {"fixture/input.txt": hashlib.sha256(fixture.read_bytes()).hexdigest()},
            "provenance": json.loads((self.distribution / "generated/provenance.json").read_text()),
            "fixture_errors": [], "fixtures_staged": [], "full_installer": {"status": "installed"},
            "staged_model_settings": {"requested_model": "gpt-6-luna", "requested_reasoning_effort": "max"},
        }
        (stage / "acceptance-stage.json").write_text(json.dumps(receipt))
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value={"version": "codex-cli 0.162.1"}),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host.probe_existing_project", return_value={"ready": False}),
            patch("hve_codex_porter.host_runtime._install_scenario") as install,
            patch("hve_codex_porter.host_runtime._stage_fixtures") as rewrite,
            patch("hve_codex_porter.host_runtime._pin_staged_model_settings") as pin,
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, persistent_stage=stage)
        install.assert_not_called()
        rewrite.assert_not_called()
        pin.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(fixture.read_bytes(), b"source content\n")
        self.assertTrue((stage / "acceptance-stage.json").exists())
        self.assertEqual(report["execution"]["temporary_workspace_cleanup"]["status"], "preserved-persistent")
        self.assertEqual(report["execution"]["model_sessions_started"], 0)

        fixture.write_bytes(b"human edit")
        with patch("hve_codex_porter.host_runtime._probe_codex") as probe, self.assertRaisesRegex(PortError, "inputs changed"):
            run_evaluations(self.distribution, self.scenarios, self.root / "changed-report", persistent_stage=stage)
        probe.assert_not_called()
        self.assertEqual(fixture.read_bytes(), b"human edit")

    def test_direct_runtime_entrypoints_reject_unscoped_legacy_definitions_before_actions(self) -> None:
        self.write_scenarios([self.one_scenario()], scope=None)
        with (
            patch("hve_codex_porter.host_runtime._probe_codex") as probe,
            patch("hve_codex_porter.host_runtime._install_scenario") as install,
        ):
            with self.assertRaisesRegex(PortError, "scope=codex-host"):
                prepare_evaluations(self.distribution, self.scenarios, self.output)
            with self.assertRaisesRegex(PortError, "scope=codex-host"):
                run_evaluations(self.distribution, self.scenarios, self.output)

        probe.assert_not_called()
        install.assert_not_called()

    def test_native_windows_eval_workspace_is_created_under_report_output_and_cleaned(self) -> None:
        self.write_scenarios([self.one_scenario()])
        real_temporary_directory = tempfile.TemporaryDirectory

        probe = {
            "available": True,
            "exec_help_available": True,
            "resume_help_available": False,
            "features": {
                "json_events": False,
                "workspace_write_sandbox": True,
                "ignore_user_config": True,
                "resume": False,
                "model_override": True,
                "config_override": True,
                "resume_model_override": False,
                "resume_config_override": False,
            },
        }
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime._existing_windows_sandbox_mode", return_value=("elevated", None)),
            patch(
                "hve_codex_porter.host_runtime._probe_windows_sandbox_readiness",
                return_value={
                    "method": "app-server/windowsSandbox/readiness",
                    "status": "ready",
                    "readiness_status": "ready",
                    "model_turns_started": 0,
                    "reason": None,
                },
            ),
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime.tempfile.TemporaryDirectory", wraps=real_temporary_directory) as tempdir,
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, timeout_seconds=30)

        expected_parent = self.output.resolve() / ".workspaces"
        self.assertEqual(
            _evaluation_workspace_parent(self.output.resolve() / "evaluations-report.json", is_windows=True),
            expected_parent,
        )
        self.assertIsNone(
            _evaluation_workspace_parent(self.output.resolve() / "evaluations-report.json", is_windows=False)
        )
        tempdir.assert_called_once_with(
            prefix="hve-codex-eval-", dir=expected_parent, ignore_cleanup_errors=True
        )
        self.assertTrue(expected_parent.is_dir())
        self.assertEqual(list(expected_parent.iterdir()), [])
        execute.assert_not_called()
        self.assertEqual(report["execution"]["model_sessions_started"], 0)

    def test_cleanup_permission_error_preserves_completed_report_and_marks_incomplete(self) -> None:
        self.write_scenarios([self.one_scenario()])
        real_temporary_directory = tempfile.TemporaryDirectory
        created_directories: list[tempfile.TemporaryDirectory] = []

        class CleanupDeniedTemporaryDirectory:
            def __init__(self, **options: object) -> None:
                self._temporary = real_temporary_directory(**options)
                created_directories.append(self._temporary)

            def __enter__(self) -> str:
                return self._temporary.__enter__()

            def __exit__(self, exception_type: object, exception: object, traceback: object) -> bool:
                raise PermissionError(5, "Access is denied", ".copilot-tracking")

        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": False,
            "features": {
                "json_events": True,
                "workspace_write_sandbox": True,
                "model_override": True,
                "config_override": True,
                "resume": False,
                "resume_model_override": False,
                "resume_config_override": False,
            },
        }
        completed_turn = {
            "status": "completed",
            "session_id": "session-cleanup-test",
            "event_types": ["thread.started", "turn.completed"],
            "turn_completed_event": True,
            "observed_models": [],
        }
        try:
            with (
                patch("hve_codex_porter.host_runtime._is_native_windows", return_value=False),
                patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
                patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
                patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
                patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
                patch("hve_codex_porter.host_runtime._execute_codex_turn", return_value=completed_turn) as execute,
                patch("hve_codex_porter.host_runtime._workspace_snapshot", return_value=([], [])),
                patch(
                    "hve_codex_porter.host_runtime._capture_workspace_checkpoint",
                    return_value={"status": "complete", "changed_files": [], "errors": []},
                ),
                patch(
                    "hve_codex_porter.host_runtime.tempfile.TemporaryDirectory",
                    side_effect=CleanupDeniedTemporaryDirectory,
                ),
            ):
                report = run_evaluations(
                    self.distribution,
                    self.scenarios,
                    self.output,
                    max_sessions=1,
                    timeout_seconds=30,
                )

            execute.assert_called_once()
            self.assertEqual(report["summary"]["behavioral_sessions_completed"], 1)
            self.assertEqual(report["scenarios"][0]["behavioral_status"], "session-complete")
            self.assertTrue((self.output / "evaluations-report.json").is_file())
            persisted = json.loads((self.output / "evaluations-report.json").read_text(encoding="utf-8"))
            cleanup = persisted["execution"]["temporary_workspace_cleanup"]
            self.assertEqual(cleanup["status"], "incomplete")
            self.assertEqual(cleanup["error"]["type"], "PermissionError")
            self.assertIn("Access is denied", cleanup["error"]["message"])
            self.assertIn(
                "temporary-workspace-cleanup-incomplete",
                {item["code"] for item in persisted["stable_blockers"]},
            )
        finally:
            for temporary_directory in created_directories:
                temporary_directory.cleanup()

    def test_standalone_install_resolves_skill_command_and_implicit_mode(self) -> None:
        from hve_codex_porter.host_runtime import _install_scenario

        stage = self.root / "standalone-project"
        stage.mkdir()
        _install_scenario(self.distribution, stage)
        entrypoints = [{"id": "primary", "source": ".github/agents/hve-core/rpi-agent.agent.md"}]
        explicit = {
            "prompts": ["Run $actual-entrypoint on the supplied task."],
            "entrypoints": entrypoints,
        }
        resolved = _resolve_entrypoints(explicit, stage)
        self.assertEqual(resolved["status"], "resolved")
        alias_name = resolved["resolved"][0]["name"]
        self.assertEqual(resolved["prompts"], [f"Run ${alias_name} on the supplied task."])
        self.assertEqual(resolved["resolved"][0]["invocation"], f"${alias_name}")
        self.assertEqual(resolved["resolved"][0]["skill_path"], f".agents/skills/{alias_name}/SKILL.md")

        implicit = {
            "invocation_mode": "implicit",
            "prompts": ["Research the supplied task using the appropriate installed workflow."],
            "entrypoints": entrypoints,
        }
        implicit_resolution = _resolve_entrypoints(implicit, stage)
        self.assertEqual(implicit_resolution["status"], "resolved")
        self.assertEqual(implicit_resolution["invocation_mode"], "implicit")
        self.assertEqual(implicit_resolution["prompts"], implicit["prompts"])

    def test_implicit_mode_rejects_explicit_entrypoint_placeholders(self) -> None:
        from hve_codex_porter.host_runtime import _scenario_data

        scenario = self.one_scenario(prompts=["Use $actual-entrypoint for this task."])
        scenario["invocation_mode"] = "implicit"
        scenario["entrypoints"] = [{"id": "primary", "source": ".github/agents/hve-core/rpi-agent.agent.md"}]
        self.write_scenarios([scenario])
        self.assertEqual(_scenario_data(self.scenarios)["scenarios"][0]["invocation_mode"], "implicit")
        stage = self.root / "plugin-fixture"
        plugin = stage / ".agents" / "plugins" / "hve-core-codex"
        (plugin / "skills" / "rpi-agent").mkdir(parents=True)
        (plugin / "skills" / "rpi-agent" / "SKILL.md").write_text("skill\n", encoding="utf-8")
        (plugin / "aliases.json").write_text(
            json.dumps([{"id": "primary", "name": "rpi-agent", "source": ".github/agents/hve-core/rpi-agent.agent.md", "user_invocable": True}]),
            encoding="utf-8",
        )
        result = _resolve_entrypoints(scenario, stage)
        self.assertEqual(result["status"], "unresolved")
        self.assertTrue(any("must not contain explicit" in error for error in result["errors"]))

    def test_scenario_data_rejects_unknown_invocation_mode(self) -> None:
        from hve_codex_porter.host_runtime import _scenario_data

        scenario = self.one_scenario()
        scenario["invocation_mode"] = "automatic"
        self.write_scenarios([scenario])
        with self.assertRaisesRegex(PortError, "invocation_mode"):
            _scenario_data(self.scenarios)

    def test_staged_model_settings_pin_defaults_and_custom_profiles_only(self) -> None:
        import tomlkit

        stage = self.root / "staged"
        agents = stage / ".codex" / "agents"
        agents.mkdir(parents=True)
        profile_path = agents / "worker.toml"
        profile_path.write_text(
            'name = "worker"\ndescription = "fixture agent"\nmodel = "old-model"\nmodel_reasoning_effort = "low"\n',
            encoding="utf-8",
        )
        source_profile = self.distribution / "codex-overlay" / "agents" / "rpi-agent.toml"
        original_source = source_profile.read_bytes()
        result = _pin_staged_model_settings(stage, DEFAULT_EVAL_MODEL, DEFAULT_EVAL_REASONING_EFFORT)

        config = tomlkit.parse((stage / ".codex" / "config.toml").read_text(encoding="utf-8"))
        profile = tomlkit.parse(profile_path.read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "gpt-6-luna")
        self.assertEqual(config["model_reasoning_effort"], "max")
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(config["agents"]["default_subagent_reasoning_effort"], "max")
        self.assertEqual(profile["model"], "gpt-6-luna")
        self.assertEqual(profile["model_reasoning_effort"], "max")
        self.assertEqual(result["custom_agent_profile_count"], 1)
        self.assertEqual(source_profile.read_bytes(), original_source)

    def test_legacy_staged_model_settings_omit_global_defaults_but_pin_root_and_profiles(self) -> None:
        import tomlkit

        stage = self.root / "legacy-staged"
        agents = stage / ".codex" / "agents"
        agents.mkdir(parents=True)
        (stage / ".codex" / "config.toml").write_text(
            '[agents]\nworkers = 3\ndefault_subagent_model = "old-model"\n'
            'default_subagent_reasoning_effort = "low"\n',
            encoding="utf-8",
        )
        profile_path = agents / "reviewer.toml"
        profile_path.write_text('name = "reviewer"\nmodel = "old-model"\n', encoding="utf-8")

        result = _pin_staged_model_settings(
            stage,
            DEFAULT_EVAL_MODEL,
            DEFAULT_EVAL_REASONING_EFFORT,
            supports_agent_default_config=False,
            agent_default_config_status="unsupported",
        )
        config = tomlkit.parse((stage / ".codex" / "config.toml").read_text(encoding="utf-8"))
        profile = tomlkit.parse(profile_path.read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "gpt-6-luna")
        self.assertEqual(config["model_reasoning_effort"], "max")
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertEqual(config["agents"]["workers"], 3)
        self.assertNotIn("default_subagent_model", config["agents"])
        self.assertNotIn("default_subagent_reasoning_effort", config["agents"])
        self.assertEqual(profile["model"], "gpt-6-luna")
        self.assertEqual(profile["model_reasoning_effort"], "max")
        self.assertEqual(result["agent_default_config_status"], "unsupported")
        self.assertEqual(result["generic_subagent_model_status"], "unverified")

    def test_agent_default_config_compatibility_is_exact_version_scoped(self) -> None:
        legacy = _agent_default_config_capability("codex-cli 0.144.6")
        known_alpha = _agent_default_config_capability("codex-cli 0.162.0-alpha.17.2")
        known_stable = _agent_default_config_capability("codex-cli 0.162.1")
        unknown = _agent_default_config_capability("codex-cli 0.150.0")
        unknown_patch = _agent_default_config_capability("codex-cli 0.162.1+unknown")
        missing = _agent_default_config_capability(None)

        self.assertEqual(legacy["status"], "unsupported")
        self.assertIn("AgentRoleToml", legacy["evidence"])
        self.assertEqual(known_alpha["status"], "supported")
        self.assertEqual(known_stable["status"], "supported")
        self.assertIn("config/read", known_stable["evidence"])
        self.assertEqual(unknown["status"], "unverified")
        self.assertEqual(unknown["version"], "codex-cli 0.150.0")
        self.assertEqual(unknown_patch["status"], "unverified")
        self.assertEqual(missing["status"], "unverified")

    def test_source_artifact_recovery_verifies_byte_exact_original_archive(self) -> None:
        import hashlib

        distribution = self.root / "distribution"
        stage = self.root / "stage"
        packaged_resource = distribution / "plugins" / "hve-core-codex" / "resources" / ".github" / "agents" / "sample.md"
        packaged_original = distribution / "plugins" / "hve-core-codex" / "originals" / ".github" / "agents" / "sample.agent.md"
        installed_resource = stage / ".agents" / "plugins" / "hve-core-codex" / "resources" / ".github" / "agents" / "sample.md"
        installed_original = stage / ".agents" / "plugins" / "hve-core-codex" / "originals" / ".github" / "agents" / "sample.agent.md"
        raw_source = b"---\nname: sample\n---\nOriginal bytes.\r\n"
        translated_resource = b"Translated mirror.\n"
        original_path = "plugins/hve-core-codex/originals/.github/agents/sample.agent.md"
        resource_path = "plugins/hve-core-codex/resources/.github/agents/sample.md"
        source_hash = hashlib.sha256(raw_source).hexdigest()
        for target, content in (
            (packaged_resource, translated_resource),
            (installed_resource, translated_resource),
            (packaged_original, raw_source),
            (installed_original, raw_source),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for name, value in (
            ("source-to-output.json", {".github/agents/sample.agent.md": {"resources": [resource_path], "originals": [original_path]}}),
            ("source-inventory.json", {"artifacts": [{"source": ".github/agents/sample.agent.md", "source_sha": "a" * 40, "hash": source_hash, "originals": [original_path]}]}),
            ("provenance.json", {"upstream_sha": "a" * 40}),
        ):
            path = distribution / "generated" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")

        check = {"id": "source", "kind": "source_artifact_recovery", "sources": [".github/agents/sample.agent.md"]}
        summary = _source_artifact_evidence(distribution, [".github/agents/sample.agent.md"])[0]
        self.assertIsNone(summary["original_source_verified"])
        self.assertEqual(summary["original_source_verification_status"], "not-checked")

        result = _check_result("source-test", check, stage, distribution)
        outcome = result["source_artifacts"][0]
        self.assertEqual(result["status"], "passed")
        self.assertTrue(outcome["original_source_verified"])
        self.assertEqual(outcome["original_source_recovery"][0]["installed_sha256"], source_hash)

        installed_original.write_bytes(b"changed original archive\n")
        corrupted = _check_result("source-test", check, stage, distribution)
        self.assertEqual(corrupted["status"], "failed")
        self.assertFalse(corrupted["source_artifacts"][0]["original_source_verified"])

    def test_legacy_source_artifact_record_does_not_claim_exact_original_verification(self) -> None:
        distribution = self.root / "legacy-distribution"
        stage = self.root / "legacy-stage"
        resource_path = "plugins/hve-core-codex/resources/legacy.md"
        packaged = distribution / resource_path
        installed = stage / ".agents" / "plugins" / "hve-core-codex" / "resources" / "legacy.md"
        for path in (packaged, installed):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("translated legacy resource\n", encoding="utf-8")
        generated = distribution / "generated"
        generated.mkdir(parents=True)
        (generated / "source-to-output.json").write_text(
            json.dumps({"legacy.md": {"resources": [resource_path]}}), encoding="utf-8"
        )
        (generated / "source-inventory.json").write_text(
            json.dumps({"artifacts": [{"source": "legacy.md", "source_sha": "b" * 40, "hash": "not-an-original-hash"}]}), encoding="utf-8"
        )
        (generated / "provenance.json").write_text(json.dumps({"upstream_sha": "b" * 40}), encoding="utf-8")
        result = _check_result(
            "legacy-test",
            {"id": "source", "kind": "source_artifact_recovery", "sources": ["legacy.md"]},
            stage,
            distribution,
        )
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["source_artifacts"][0]["original_source_verified"])

    def test_source_recovery_reads_standalone_skill_destination(self) -> None:
        distribution = self.root / "standalone-distribution"
        stage = self.root / "standalone-stage"
        relative = "plugins/hve-core-codex/skills/demo/SKILL.md"
        packaged = distribution / relative
        installed = stage / ".agents/skills/demo/SKILL.md"
        for path in (packaged, installed):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"skill content\n")
        # Standalone resources do not contain a duplicate skills tree.
        (stage / ".codex/hve-core-codex/plugin-resources").mkdir(parents=True)
        generated = distribution / "generated"
        generated.mkdir()
        source = ".github/skills/demo/SKILL.md"
        for name, value in (
            ("source-to-output.json", {source: {"resources": [relative]}}),
            ("source-inventory.json", {"artifacts": [{"source": source, "source_sha": "a" * 40}]}),
            ("provenance.json", {"upstream_sha": "a" * 40}),
        ):
            (generated / name).write_text(json.dumps(value), encoding="utf-8")
        check = {"id": "source", "kind": "source_artifact_recovery", "sources": [source]}
        self.assertEqual(_check_result("standalone", check, stage, distribution)["status"], "passed")
        installed.write_bytes(b"modified\n")
        self.assertEqual(_check_result("standalone", check, stage, distribution)["status"], "failed")

    def test_runner_resumes_captured_session_and_keeps_rubric_pending(self) -> None:
        self.write_scenarios([self.one_scenario(prompts=["First prompt", "Follow-up prompt"])])
        probe = {
            "available": True,
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
            "version": "codex-cli 0.162.0-alpha.17.2",
        }
        first = {"status": "completed", "session_id": "session-1", "event_types": ["thread.started", "turn.completed"], "turn_completed_event": True, "output_file": None}
        second = {"status": "completed", "session_id": "session-1", "event_types": ["thread.resumed", "turn.completed"], "turn_completed_event": True, "output_file": None}
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn", side_effect=[first, second]) as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=1, timeout_seconds=30)

        self.assertEqual(execute.call_count, 2)
        self.assertIsNone(execute.call_args_list[0].kwargs["session_id"])
        self.assertEqual(execute.call_args_list[1].kwargs["session_id"], "session-1")
        self.assertEqual(execute.call_args_list[0].kwargs["model"], DEFAULT_EVAL_MODEL)
        self.assertEqual(execute.call_args_list[1].kwargs["model"], DEFAULT_EVAL_MODEL)
        self.assertEqual(execute.call_args_list[0].kwargs["reasoning_effort"], DEFAULT_EVAL_REASONING_EFFORT)
        self.assertEqual(execute.call_args_list[1].kwargs["reasoning_effort"], DEFAULT_EVAL_REASONING_EFFORT)
        self.assertEqual(report["execution"]["model_sessions_started"], 1)
        windows_preflight = report["host_evidence"]["windows_native_sandbox"]
        if os.name == "nt":
            self.assertEqual(windows_preflight["requested_mode"], "elevated")
            self.assertEqual(windows_preflight["effective_mode"], "unverified")
            self.assertIn(
                "windows-native-permission-context-unverified",
                {item["code"] for item in report["stable_blockers"]},
            )
        self.assertEqual(report["summary"]["behavioral_sessions_completed"], 1)
        self.assertFalse(report["success"])
        self.assertFalse(report["stable_ready"])
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "session-complete")
        self.assertTrue(all(item["status"] == "manual-review-pending" for item in report["scenarios"][0]["rubric_review"]))
        self.assertIn("ide-runtime-evidence-missing", {item["code"] for item in report["stable_blockers"]})
        self.assertEqual([call.kwargs["session_id"] for call in execute.call_args_list], [None, "session-1"])

    def test_runner_does_not_start_if_help_probe_lacks_safe_required_features(self) -> None:
        self.write_scenarios([self.one_scenario()])
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value={"available": True, "exec_help_available": True, "features": {"json_events": False, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": False, "model_override": True, "config_override": True, "resume_model_override": False, "resume_config_override": False}}),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, timeout_seconds=30)

        execute.assert_not_called()
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "not-started-cli-incompatible")
        self.assertIn("codex-cli-feature-probe-failed", {item["code"] for item in report["stable_blockers"]})

    def test_unknown_agent_default_version_stages_but_fails_closed_before_any_turn(self) -> None:
        import tomllib

        self.write_scenarios([self.one_scenario()])
        probe = {
            "available": True,
            "version": "codex-cli 0.150.0",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {
                "json_events": True,
                "workspace_write_sandbox": True,
                "ignore_user_config": True,
                "resume": True,
                "model_override": True,
                "config_override": True,
                "resume_model_override": True,
                "resume_config_override": True,
            },
        }
        staged_configs: list[dict[str, object]] = []
        real_pin = _pin_staged_model_settings

        def record_pin(*args, **kwargs):
            result = real_pin(*args, **kwargs)
            config_path = args[0] / ".codex" / "config.toml"
            staged_configs.append(tomllib.loads(config_path.read_text(encoding="utf-8")))
            return result

        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=False),
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._pin_staged_model_settings", side_effect=record_pin),
            patch("hve_codex_porter.host_runtime._init_isolated_repo") as initialize,
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, timeout_seconds=30)

        self.assertEqual(len(staged_configs), 1)
        self.assertEqual(staged_configs[0]["model"], "gpt-6-luna")
        self.assertEqual(staged_configs[0]["model_reasoning_effort"], "max")
        self.assertNotIn("default_subagent_model", staged_configs[0].get("agents", {}))
        self.assertNotIn("default_subagent_reasoning_effort", staged_configs[0].get("agents", {}))
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "not-started-agent-default-config-unverified")
        self.assertEqual(report["scenarios"][0]["model_settings"]["generic_subagent_model_status"], "unverified")
        self.assertEqual(report["codex_cli"]["agent_default_config_compatibility"]["status"], "unverified")
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertEqual(report["execution"]["turn_processes_attempted"], 0)
        self.assertIn(
            "codex-cli-agent-default-config-unverified",
            {item["code"] for item in report["stable_blockers"]},
        )
        initialize.assert_not_called()
        execute.assert_not_called()

    def test_runner_does_not_start_when_installed_alias_cannot_resolve_entrypoint(self) -> None:
        scenario = self.one_scenario(prompts=["Invoke $actual-entrypoint for this case."])
        scenario["entrypoints"] = [{"id": "primary", "source": ".github/agents/missing/entrypoint.agent.md"}]
        self.write_scenarios([scenario])
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, timeout_seconds=30)

        execute.assert_not_called()
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "not-started-entrypoint-unresolved")
        self.assertEqual(report["scenarios"][0]["entrypoint_resolution"]["status"], "unresolved")
        self.assertIn("scenario-entrypoint-resolution-failed", {item["code"] for item in report["stable_blockers"]})

    def test_native_hook_case_stops_at_explicit_persistent_trust_preflight(self) -> None:
        integration_path = REPOSITORY / "tests" / "codex-host" / "stable.json"
        native_scenario = next(
            scenario for scenario in json.loads(integration_path.read_text(encoding="utf-8"))["scenarios"]
            if scenario["id"] == "integration-03"
        )
        self.write_scenarios([native_scenario])
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
            patch("hve_codex_porter.host_runtime._init_isolated_repo") as initialize,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=1, timeout_seconds=30)

        execute.assert_not_called()
        initialize.assert_not_called()
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "not-started-native-hook-trust-pending")
        self.assertFalse(report["scenarios"][0]["native_host_preflight"]["trust_verified"])
        self.assertIn("native-hook-trust-preflight-required", {item["code"] for item in report["stable_blockers"]})

    def test_observed_model_mismatch_stops_followup_turns_and_scenarios(self) -> None:
        scenarios = [self.one_scenario(prompts=["First prompt", "Second prompt"]), self.one_scenario()]
        scenarios[0]["id"] = "mismatched"
        scenarios[1]["id"] = "must-not-start"
        self.write_scenarios(scenarios)
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        wrong_model_turn = {
            "status": "completed",
            "session_id": "session-1",
            "event_types": ["thread.started", "turn.completed"],
            "turn_completed_event": True,
            "observed_models": ["gpt-6-sol"],
        }
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn", return_value=wrong_model_turn) as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=2, timeout_seconds=30)

        self.assertEqual(execute.call_count, 1)
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "session-stopped-observed-model-mismatch")
        self.assertEqual(report["scenarios"][1]["behavioral_status"], "not-started-requested-model-mismatch")
        self.assertIn("requested-model-mismatch", {item["code"] for item in report["stable_blockers"]})

    def test_native_startup_failure_stops_followup_turns_and_scenarios(self) -> None:
        scenarios = [
            self.one_scenario(prompts=["First prompt", "Second prompt"]),
            self.one_scenario(),
        ]
        scenarios[0]["id"] = "startup-failure"
        scenarios[1]["id"] = "must-not-start"
        self.write_scenarios(scenarios)
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        fatal_turn = {
            "status": "completed",
            "session_id": "session-1",
            "event_types": ["thread.started", "item.completed", "turn.completed"],
            "turn_completed_event": True,
            "native_startup_failure": True,
            "usage_observations": [{"observed_model": "gpt-6-luna", "input_tokens": 5}],
        }
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
            patch("hve_codex_porter.host_runtime._workspace_snapshot", return_value=([], [])),
            patch(
                "hve_codex_porter.host_runtime._capture_workspace_checkpoint",
                return_value={"status": "complete", "changed_files": [], "errors": []},
            ),
            patch("hve_codex_porter.host_runtime._execute_codex_turn", return_value=fatal_turn) as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=2, timeout_seconds=30)

        self.assertEqual(execute.call_count, 1)
        first, second = report["scenarios"]
        self.assertEqual(first["behavioral_status"], "session-stopped-native-cli-startup-failure")
        self.assertEqual(first["turns"][0]["status"], "completed")
        self.assertTrue(first["turns"][0]["turn_completed_event"])
        self.assertEqual(first["turns"][0]["behavioral_status"], "failed-native-cli-startup")
        self.assertEqual(len(first["turns"][0]["usage_observations"]), 1)
        self.assertEqual(second["behavioral_status"], "not-started-native-cli-startup-failure")
        self.assertIn("native-cli-startup-failure", {item["code"] for item in report["stable_blockers"]})

    def test_ordinary_nonzero_command_failure_does_not_stop_later_scenarios(self) -> None:
        scenarios = [self.one_scenario(), self.one_scenario()]
        scenarios[0]["id"] = "ordinary-command-failure"
        scenarios[1]["id"] = "later-scenario"
        self.write_scenarios(scenarios)
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        ordinary_failure = {
            "status": "failed",
            "session_id": "session-1",
            "event_types": ["thread.started", "item.completed"],
            "turn_completed_event": False,
            "native_startup_failure": False,
        }
        completed = {
            "status": "completed",
            "session_id": "session-2",
            "event_types": ["thread.started", "turn.completed"],
            "turn_completed_event": True,
            "native_startup_failure": False,
        }
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
            patch("hve_codex_porter.host_runtime._workspace_snapshot", return_value=([], [])),
            patch(
                "hve_codex_porter.host_runtime._capture_workspace_checkpoint",
                return_value={"status": "complete", "changed_files": [], "errors": []},
            ),
            patch(
                "hve_codex_porter.host_runtime._execute_codex_turn",
                side_effect=[ordinary_failure, completed],
            ) as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=2, timeout_seconds=30)

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "session-partial")
        self.assertEqual(report["scenarios"][1]["behavioral_status"], "session-complete")
        self.assertNotIn("native-cli-startup-failure", {item["code"] for item in report["stable_blockers"]})

    def test_windows_eval_blocks_without_an_existing_sandbox_mode(self) -> None:
        self.write_scenarios([self.one_scenario()])
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        reason = "Windows evals require an existing `[windows] sandbox` mode."
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime._existing_windows_sandbox_mode", return_value=(None, reason)),
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
            patch("hve_codex_porter.host_runtime._init_isolated_repo") as initialize,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, timeout_seconds=30)

        execute.assert_not_called()
        initialize.assert_not_called()
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertEqual(report["scenarios"][0]["behavioral_status"], "not-started-windows-sandbox-config-unavailable")
        self.assertEqual(report["host_evidence"]["windows_native_sandbox"]["status"], "blocked-config-mode-unavailable")
        self.assertIn("windows-native-sandbox-config-required", {item["code"] for item in report["stable_blockers"]})

    def test_windows_eval_blocks_before_exec_when_readiness_is_not_ready(self) -> None:
        self.write_scenarios([self.one_scenario()])
        probe = {
            "available": True,
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {
                "json_events": True,
                "workspace_write_sandbox": True,
                "ignore_user_config": True,
                "resume": True,
                "model_override": True,
                "config_override": True,
                "resume_model_override": True,
                "resume_config_override": True,
            },
        }
        readiness = {
            "method": "app-server/windowsSandbox/readiness",
            "status": "blocked-readiness-update-required",
            "readiness_status": "updateRequired",
            "model_turns_started": 0,
            "reason": "Codex reports that the native Windows sandbox needs setup.",
        }
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime._existing_windows_sandbox_mode", return_value=("elevated", None)),
            patch("hve_codex_porter.host_runtime._probe_windows_sandbox_readiness", return_value=readiness) as preflight,
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._install_scenario", return_value={"status": "installed"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn") as execute,
            patch("hve_codex_porter.host_runtime._init_isolated_repo") as initialize,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, timeout_seconds=30)

        preflight.assert_called_once()
        execute.assert_not_called()
        initialize.assert_not_called()
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertEqual(
            report["scenarios"][0]["behavioral_status"],
            "not-started-windows-sandbox-readiness-unverified",
        )
        windows_evidence = report["host_evidence"]["windows_native_sandbox"]
        self.assertEqual(windows_evidence["status"], "blocked-readiness-update-required")
        self.assertEqual(windows_evidence["readiness_status"], "updateRequired")
        self.assertEqual(windows_evidence["model_turns_started_by_preflight"], 0)
        self.assertFalse(windows_evidence["app_server_user_config_isolated"])
        self.assertFalse(windows_evidence["exec_user_config_isolated"])
        self.assertEqual(
            windows_evidence["preflight_rpc_methods"],
            ["initialize", "initialized", "windowsSandbox/readiness"],
        )
        self.assertFalse(windows_evidence["setup_or_turn_started_by_preflight"])
        self.assertIn(
            "windows-native-sandbox-readiness-required",
            {item["code"] for item in report["stable_blockers"]},
        )

    def test_windows_sandbox_readiness_probe_uses_only_initialize_and_readiness(self) -> None:
        class RecordingInput(io.BytesIO):
            def close(self) -> None:
                self.closed_by_probe = True

        class FakeProcess:
            def __init__(self) -> None:
                messages = [
                    {"id": 1, "result": {}},
                    {"id": 2, "result": {"status": "updateRequired"}},
                ]
                self.stdin = RecordingInput()
                self.stdout = io.BytesIO(
                    b"".join(json.dumps(item).encode("utf-8") + b"\n" for item in messages)
                )
                self.pid = 4567

            @staticmethod
            def poll() -> int:
                return 0

            @staticmethod
            def wait(timeout: float | None = None) -> int:
                return 0

        fake = FakeProcess()
        with patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=fake) as popen:
            result = _probe_windows_sandbox_readiness(
                "codex-test",
                "elevated",
                timeout_seconds=10,
                is_windows=True,
            )

        self.assertEqual(result["status"], "blocked-readiness-update-required")
        self.assertEqual(result["readiness_status"], "updateRequired")
        self.assertEqual(result["model_turns_started"], 0)
        sent = [json.loads(line) for line in fake.stdin.getvalue().splitlines()]
        self.assertEqual(
            [item["method"] for item in sent],
            ["initialize", "initialized", "windowsSandbox/readiness"],
        )
        self.assertNotIn("thread/start", {item["method"] for item in sent})
        self.assertNotIn("turn/start", {item["method"] for item in sent})
        command = popen.call_args.args[0]
        self.assertIn('windows.sandbox="elevated"', command)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_probe_reads_help_without_assuming_a_specific_version(self) -> None:
        outputs = [
            SimpleNamespace(returncode=0, stdout="codex local-build", stderr=""),
            SimpleNamespace(returncode=0, stdout="Codex usage", stderr=""),
            SimpleNamespace(returncode=0, stdout="--json --sandbox workspace-write --ignore-user-config --model --config", stderr=""),
            SimpleNamespace(returncode=0, stdout="resume --json --model --config", stderr=""),
        ]
        with (
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime.subprocess.run", side_effect=outputs) as run,
        ):
            result = _probe_codex("codex-test", 15)

        self.assertEqual(result["version"], "codex local-build")
        self.assertTrue(result["features"]["json_events"])
        self.assertTrue(result["features"]["workspace_write_sandbox"])
        self.assertTrue(result["features"]["ignore_user_config"])
        self.assertTrue(result["features"]["resume"])
        self.assertTrue(result["features"]["model_override"])
        self.assertTrue(result["features"]["config_override"])
        self.assertTrue(result["features"]["resume_model_override"])
        self.assertTrue(result["features"]["resume_config_override"])
        self.assertEqual(run.call_count, 4)
        self.assertTrue(all(call.kwargs["shell"] is False for call in run.call_args_list))
        self.assertEqual(run.call_args_list[1].args[0], ["codex-test", "--help"])
        self.assertEqual(
            run.call_args_list[3].args[0],
            ["codex-test", "exec", "--json", "resume", "--help"],
        )

    def test_turn_execution_forwards_model_and_effort_to_exec_and_resume_without_shell(self) -> None:
        output = (
            b'{"type":"thread.started","thread_id":"thread-1"}\n'
            b'{"type":"turn.completed","model":"gpt-6-luna","usage":{"input_tokens":25,"output_tokens":7}}\n'
        )

        def fake_process():
            return SimpleNamespace(
                stdout=io.BytesIO(output),
                returncode=0,
                wait=lambda timeout=None: 0,
                poll=lambda: 0,
                kill=lambda: None,
                pid=4321,
            )

        first_target = self.root / "turn.jsonl"
        resume_target = self.root / "resume.jsonl"
        with patch(
            "hve_codex_porter.host_runtime.subprocess.Popen",
            side_effect=[fake_process(), fake_process()],
        ) as popen, patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True):
            first_result = _execute_codex_turn(
                "codex-test", self.root, "review only", session_id=None, timeout_seconds=10, output_path=first_target,
                windows_sandbox_mode="elevated",
            )
            resume_result = _execute_codex_turn(
                "codex-test", self.root, "resume review", session_id="thread-1", timeout_seconds=10,
                output_path=resume_target, model="gpt-6-luna", reasoning_effort="max",
                windows_sandbox_mode="elevated",
            )

        first_command, resume_command = [call.args[0] for call in popen.call_args_list]
        for command in (first_command, resume_command):
            self.assertIn("--model", command)
            model_index = command.index("--model")
            self.assertEqual(command[model_index + 1], "gpt-6-luna")
            config_values = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--config"]
            self.assertIn('model_reasoning_effort="max"', config_values)
            self.assertIn('agents.default_subagent_model="gpt-6-luna"', config_values)
            self.assertIn('agents.default_subagent_reasoning_effort="max"', config_values)
            self.assertIn('windows.sandbox="elevated"', config_values)
        self.assertEqual(first_command[:6], ["codex-test", "exec", "--ignore-user-config", "--json", "--sandbox", "workspace-write"])
        self.assertEqual(first_command[-1], "review only")
        self.assertIn("resume", resume_command)
        self.assertEqual(resume_command[resume_command.index("resume") + 1], "thread-1")
        self.assertEqual(resume_command[-1], "resume review")
        self.assertTrue(all(call.kwargs["shell"] is False for call in popen.call_args_list))
        self.assertNotIn("--ask-for-approval", first_command)
        self.assertNotIn("--full-auto", first_command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", first_command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", resume_command)
        self.assertEqual(first_result["session_id"], "thread-1")
        self.assertEqual(resume_result["session_id"], "thread-1")
        self.assertEqual(first_result["status"], "completed")
        self.assertTrue(first_result["turn_completed_event"])
        self.assertEqual(first_result["usage_observations"][0]["tokens"]["input_tokens"], 25)
        self.assertEqual(first_target.read_bytes(), output)

    def test_project_config_execution_preserves_profile_sandbox_defaults(self) -> None:
        config_dir = self.root / ".codex"
        config_dir.mkdir(exist_ok=True)
        config = config_dir / "config.toml"
        config.write_text(
            'sandbox_mode="workspace-write"\nmodel="gpt-6-luna"\nmodel_reasoning_effort="max"\n',
            encoding="utf-8",
        )
        process = SimpleNamespace(
            stdout=io.BytesIO(b'{"type":"turn.completed"}\n'), returncode=0,
            wait=lambda timeout=None: 0, poll=lambda: 0, kill=lambda: None, pid=4321,
        )
        with (
            patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=process) as popen,
            patch("hve_codex_porter.host_runtime._existing_project_is_trusted", return_value=True),
        ):
            result = _execute_codex_turn(
                "codex-test", self.root, "inspect", session_id=None, timeout_seconds=10,
                output_path=self.root / "project-turn.jsonl", windows_sandbox_mode="elevated",
                use_project_config=True,
            )
        command = popen.call_args.args[0]
        self.assertNotIn("--ignore-user-config", command)
        self.assertNotIn("--sandbox", command)
        self.assertFalse(any(item.startswith("projects.") for item in command))
        self.assertEqual(result["status"], "completed")
        for content in (
            'sandbox_mode="read-only"\nmodel="gpt-6-luna"\nmodel_reasoning_effort="max"\n',
            'sandbox_mode="workspace-write"\nmodel="different"\nmodel_reasoning_effort="max"\n',
            'malformed [',
        ):
            config.write_text(content, encoding="utf-8")
            with self.subTest(content=content), patch("hve_codex_porter.host_runtime.subprocess.Popen") as launch:
                invalid = _execute_codex_turn(
                    "codex-test", self.root, "inspect", session_id=None, timeout_seconds=10,
                    output_path=self.root / "invalid-turn.jsonl", windows_sandbox_mode="elevated",
                    use_project_config=True,
                )
                self.assertEqual(invalid["status"], "launch-error")
                launch.assert_not_called()

        config.write_text(
            'sandbox_mode="workspace-write"\nmodel="gpt-6-luna"\nmodel_reasoning_effort="max"\n',
            encoding="utf-8",
        )
        with (
            patch("hve_codex_porter.host_runtime._existing_project_is_trusted", return_value=False),
            patch("hve_codex_porter.host_runtime.subprocess.Popen") as launch,
        ):
            result = _execute_codex_turn(
                "codex-test", self.root, "inspect", session_id=None, timeout_seconds=10,
                output_path=self.root / "untrusted-turn.jsonl", windows_sandbox_mode="elevated",
                use_project_config=True,
            )
            self.assertEqual(result["status"], "launch-error")
            launch.assert_not_called()

    def test_legacy_project_execution_never_passes_global_agent_defaults_to_cli(self) -> None:
        config_dir = self.root / ".codex"
        config_dir.mkdir(exist_ok=True)
        config = config_dir / "config.toml"
        config.write_text(
            'sandbox_mode="workspace-write"\nmodel="gpt-6-luna"\nmodel_reasoning_effort="max"\n',
            encoding="utf-8",
        )
        process = SimpleNamespace(
            stdout=io.BytesIO(b'{"type":"turn.completed"}\n'), returncode=0,
            wait=lambda timeout=None: 0, poll=lambda: 0, kill=lambda: None, pid=4321,
        )
        with (
            patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=process) as popen,
            patch("hve_codex_porter.host_runtime._existing_project_is_trusted", return_value=True),
        ):
            result = _execute_codex_turn(
                "codex-test", self.root, "inspect", session_id=None, timeout_seconds=10,
                output_path=self.root / "legacy-project-turn.jsonl", windows_sandbox_mode="elevated",
                use_project_config=True, supports_agent_default_config=False,
            )
        command = popen.call_args.args[0]
        config_values = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--config"]
        self.assertNotIn('agents.default_subagent_model="gpt-6-luna"', config_values)
        self.assertNotIn('agents.default_subagent_reasoning_effort="max"', config_values)
        self.assertIn('model_reasoning_effort="max"', config_values)
        self.assertIn('windows.sandbox="elevated"', config_values)
        self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
        self.assertEqual(result["status"], "completed")

        config.write_text(
            'sandbox_mode="workspace-write"\nmodel="gpt-6-luna"\nmodel_reasoning_effort="max"\n'
            '[agents]\ndefault_subagent_model="gpt-6-luna"\n',
            encoding="utf-8",
        )
        with (
            patch("hve_codex_porter.host_runtime.subprocess.Popen") as launch,
            patch("hve_codex_porter.host_runtime._existing_project_is_trusted", return_value=True),
        ):
            blocked = _execute_codex_turn(
                "codex-test", self.root, "inspect", session_id=None, timeout_seconds=10,
                output_path=self.root / "legacy-invalid-project-turn.jsonl", windows_sandbox_mode="elevated",
                use_project_config=True, supports_agent_default_config=False,
            )
        self.assertEqual(blocked["status"], "launch-error")
        self.assertIn("unsupported by this CLI version", blocked["error"])
        launch.assert_not_called()

    def test_existing_project_trust_requires_exact_fixture_path_not_ancestor(self) -> None:
        import tomlkit

        config_home = self.root / "trust-home"
        config_home.mkdir()
        workspace = self.root / "trusted-project" / "fixture"
        workspace.mkdir(parents=True)
        config = config_home / "config.toml"
        self.assertFalse(_existing_project_is_trusted(workspace, config_home))
        settings = {"projects": {str(self.root.resolve()): {"trust_level": "trusted"}}}
        config.write_text(tomlkit.dumps(settings), encoding="utf-8")
        self.assertFalse(_existing_project_is_trusted(workspace, config_home))
        settings["projects"][str(workspace.parent.resolve())] = {"trust_level": "untrusted"}
        config.write_text(tomlkit.dumps(settings), encoding="utf-8")
        self.assertFalse(_existing_project_is_trusted(workspace, config_home))
        settings["projects"][str(workspace.resolve())] = {"trust_level": "trusted"}
        config.write_text(tomlkit.dumps(settings), encoding="utf-8")
        self.assertTrue(_existing_project_is_trusted(workspace, config_home))
        self.assertFalse(_existing_project_is_trusted(self.root.parent / "unrelated", config_home))
        settings["projects"][str(workspace.resolve()) + os.sep + "."] = {"trust_level": "untrusted"}
        config.write_text(tomlkit.dumps(settings), encoding="utf-8")
        self.assertFalse(_existing_project_is_trusted(workspace, config_home))
        config.write_text("invalid [", encoding="utf-8")
        self.assertFalse(_existing_project_is_trusted(workspace, config_home))

    def test_windows_native_sandbox_override_is_narrow_and_platform_scoped(self) -> None:
        self.assertEqual(
            _windows_native_sandbox_config_args("elevated", is_windows=True),
            ["--config", 'windows.sandbox="elevated"'],
        )
        self.assertEqual(
            _windows_native_sandbox_config_args("unelevated", is_windows=True),
            ["--config", 'windows.sandbox="unelevated"'],
        )
        self.assertEqual(_windows_native_sandbox_config_args(None, is_windows=False), [])
        for invalid_mode in (None, ["elevated"], {"mode": "elevated"}, "danger-full-access"):
            with self.subTest(mode=invalid_mode), self.assertRaises(PortError):
                _windows_native_sandbox_config_args(invalid_mode, is_windows=True)

    def test_windows_sandbox_mode_reads_only_supported_setting_from_existing_config(self) -> None:
        for mode in ("elevated", "unelevated"):
            config_home = self.root / mode
            config_home.mkdir()
            (config_home / "config.toml").write_text(
                f'model = "gpt-6-luna"\n[windows]\nsandbox = "{mode}"\n',
                encoding="utf-8",
            )
            self.assertEqual(_read_windows_sandbox_mode(config_home), (mode, None))

    def test_windows_sandbox_mode_requires_explicit_valid_user_config_without_fallback(self) -> None:
        missing = self.root / "missing-config"
        mode, reason = _read_windows_sandbox_mode(missing)
        self.assertIsNone(mode)
        self.assertIn("will not install", reason)

        invalid = self.root / "invalid-config"
        invalid.mkdir()
        (invalid / "config.toml").write_text('[windows]\nsandbox = "danger-full-access"\n', encoding="utf-8")
        mode, reason = _read_windows_sandbox_mode(invalid)
        self.assertIsNone(mode)
        self.assertIn("will not choose a mode", reason)

        for directory, value in (("array-config", '["elevated"]'), ("table-config", '{mode="elevated"}')):
            malformed_mode = self.root / directory
            malformed_mode.mkdir()
            (malformed_mode / "config.toml").write_text(
                f"[windows]\nsandbox = {value}\n",
                encoding="utf-8",
            )
            mode, reason = _read_windows_sandbox_mode(malformed_mode)
            self.assertIsNone(mode)
            self.assertIn("will not choose a mode", reason)

    def test_event_summary_extracts_session_id_and_tolerates_non_json_lines(self) -> None:
        result = _codex_event_summary(
            b'{"type":"thread.started","thread_id":"id-1"}\nnoise\n'
            b'{"type":"turn.completed","model":"gpt-6-luna","usage":{"input_tokens":5}}\n'
        )
        self.assertEqual(result["session_id"], "id-1")
        self.assertEqual(result["event_types"], ["thread.started", "turn.completed"])
        self.assertEqual(result["unparsed_lines"], 1)
        self.assertTrue(result["turn_completed"])
        self.assertEqual(result["observed_models"], ["gpt-6-luna"])
        self.assertEqual(len(result["usage_observations"]), 1)
        self.assertEqual(result["usage_observations"][0]["session_id"], "id-1")

    def test_zero_exit_without_turn_completed_event_is_not_a_completed_turn(self) -> None:
        fake_process = SimpleNamespace(
            stdout=io.BytesIO(b'{"type":"thread.started","thread_id":"thread-1"}\n'),
            returncode=0,
            wait=lambda timeout=None: 0,
            poll=lambda: 0,
            kill=lambda: None,
            pid=4321,
        )
        with patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=fake_process):
            result = _execute_codex_turn(
                "codex-test", self.root, "inspect only", session_id=None,
                timeout_seconds=10, output_path=self.root / "missing-completion.jsonl",
                windows_sandbox_mode="elevated",
            )

        self.assertEqual(result["status"], "missing-completion-event")
        self.assertFalse(result["turn_completed_event"])

    def test_eval_computer_use_isolation_is_process_only_and_applies_to_resume(self) -> None:
        from hve_codex_porter.host_runtime import _computer_use_isolation_args
        for session_id in (None, "existing-thread"):
            process = SimpleNamespace(stdout=io.BytesIO(b'{"type":"turn.completed"}\n'),
                                      returncode=0, wait=lambda timeout=None: 0, poll=lambda: 0)
            with patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=process) as launch:
                _execute_codex_turn("codex-test", self.root, "inspect", session_id=session_id,
                                    timeout_seconds=10, output_path=self.root / "isolated.jsonl",
                                    windows_sandbox_mode="elevated",
                                    isolate_computer_use=True)
            command = launch.call_args.args[0]
            for setting in _computer_use_isolation_args()[1::2]:
                self.assertIn(setting, command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertFalse((self.root / ".codex/config.toml").exists())

    def test_structured_native_startup_failure_preserves_completed_turn_and_raw_trace(self) -> None:
        signature = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"
        events = [
            {"type": "thread.started", "thread_id": "thread-1"},
            {
                "type": "item.completed",
                "item": {
                    "id": "item-1",
                    "type": "command_execution",
                    "aggregated_output": signature,
                    "exit_code": -1,
                    "status": "failed",
                },
            },
            {
                "type": "turn.completed",
                "model": "gpt-6-luna",
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        ]
        trace = b"".join(json.dumps(event).encode("utf-8") + b"\n" for event in events)
        fake_process = SimpleNamespace(
            stdout=io.BytesIO(trace),
            returncode=0,
            wait=lambda timeout=None: 0,
            poll=lambda: 0,
            kill=lambda: None,
            pid=4321,
        )
        output_path = self.root / "native-startup.jsonl"
        with patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=fake_process):
            result = _execute_codex_turn(
                "codex-test", self.root, "inspect only", session_id=None,
                timeout_seconds=10, output_path=output_path,
                windows_sandbox_mode="elevated",
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["turn_completed_event"])
        self.assertTrue(result["native_startup_failure"])
        self.assertEqual(result["usage_observations"][0]["tokens"]["input_tokens"], 5)
        self.assertEqual(output_path.read_bytes(), trace)

    def test_running_turn_is_terminated_on_structured_startup_failure(self) -> None:
        event = {"type": "item.completed", "item": {
            "type": "command_execution", "status": "failed", "exit_code": -1,
            "aggregated_output": "Failed to create unified exec process: helper_unknown_error: setup refresh had errors",
        }}
        trace = json.dumps(event).encode() + b"\n"
        process = SimpleNamespace(stdout=io.BytesIO(trace), returncode=-1,
                                  wait=lambda timeout=None: -1, poll=lambda: None, pid=4321)
        with patch("hve_codex_porter.host_runtime.subprocess.Popen", return_value=process), patch(
            "hve_codex_porter.host_runtime._terminate_process_tree"
        ) as terminate:
            result = _execute_codex_turn(
                "codex-test", self.root, "inspect only", session_id=None,
                timeout_seconds=10, output_path=self.root / "fail-fast.jsonl",
                windows_sandbox_mode="elevated",
            )
        terminate.assert_called_once_with(process)
        self.assertTrue(result["native_startup_failure"])
        self.assertEqual((self.root / "fail-fast.jsonl").read_bytes(), trace)

    def test_native_startup_signature_in_completed_file_output_is_ignored(self) -> None:
        signature = "Failed to create unified exec process: helper_unknown_error: setup refresh had errors"
        output = json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "aggregated_output": signature,
                "exit_code": 0,
                "status": "completed",
            },
        }).encode("utf-8")
        result = _codex_event_summary(output)
        self.assertFalse(result["native_startup_failure"])

    def test_ordinary_nonzero_command_event_is_not_native_startup_failure(self) -> None:
        output = json.dumps({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "aggregated_output": "pytest failed: helper_unknown_error was printed by a fixture",
                "exit_code": 1,
                "status": "failed",
            },
        }).encode("utf-8")
        result = _codex_event_summary(output)
        self.assertFalse(result["native_startup_failure"])

    def test_partial_session_keeps_workspace_postchecks_pending_and_retains_turn_artifacts(self) -> None:
        scenario = self.one_scenario(
            prompts=["Create output", "Resume and inspect"],
            checks=[
                {"id": "fixture-unchanged-after-first-turn", "kind": "fixture_hash_at_turn", "path": "fixture/input.txt", "after_prompt": 1, "expected_status": "unchanged"},
                {"id": "fixture-unverified-after-failed-turn", "kind": "fixture_hash_at_turn", "path": "fixture/input.txt", "after_prompt": 2, "expected_status": "unchanged"},
                {"id": "result-exists", "kind": "workspace_file_exists", "path": "generated/result.md"},
            ],
        )
        self.write_scenarios([scenario])
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        calls = 0

        def execute(_executable, workspace, _prompt, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                result = workspace / "generated" / "result.md"
                result.parent.mkdir(parents=True)
                result.write_text("captured output\n", encoding="utf-8")
                return {
                    "status": "completed", "session_id": "session-1",
                    "event_types": ["thread.started", "turn.completed"],
                    "turn_completed_event": True,
                }
            return {
                "status": "failed", "session_id": "session-1",
                "event_types": ["thread.resumed"], "turn_completed_event": False,
            }

        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn", side_effect=execute),
        ):
            # This exercises completion accounting, not installation speed;
            # staging the real distribution can be slow under Windows scans.
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=1, timeout_seconds=120)

        scenario_report = report["scenarios"][0]
        checks = {item["check_id"]: item for item in scenario_report["deterministic_checks"]}
        self.assertEqual(scenario_report["behavioral_status"], "session-partial")
        self.assertEqual(checks["fixture-unchanged-after-first-turn"]["status"], "passed")
        self.assertEqual(checks["fixture-unverified-after-failed-turn"]["status"], "pending-runtime")
        self.assertEqual(checks["result-exists"]["status"], "pending-runtime")
        self.assertTrue(all(item["status"] == "manual-review-pending" for item in scenario_report["rubric_review"]))
        checkpoint = scenario_report["turns"][0]["workspace_checkpoint"]
        retained = next(item for item in checkpoint["changed_files"] if item["path"] == "generated/result.md")
        self.assertTrue(retained["retained"])
        evidence_path = self.output / "evaluations-report.sessions" / retained["evidence_path"]
        self.assertEqual(evidence_path.read_text(encoding="utf-8"), "captured output\n")

    def test_session_budget_counts_launched_attempts_without_a_started_event(self) -> None:
        scenarios = [self.one_scenario(), self.one_scenario()]
        scenarios[0]["id"] = "first"
        scenarios[1]["id"] = "second"
        self.write_scenarios(scenarios)
        probe = {
            "available": True,
            "version": "codex-cli 0.162.0-alpha.17.2",
            "exec_help_available": True,
            "resume_help_available": True,
            "features": {"json_events": True, "workspace_write_sandbox": True, "ignore_user_config": True, "resume": True, "model_override": True, "config_override": True, "resume_model_override": True, "resume_config_override": True},
        }
        launch_error = {"status": "launch-error", "session_id": None, "event_types": [], "turn_completed_event": False}
        with (
            patch("hve_codex_porter.host_runtime._probe_codex", return_value=probe),
            patch("hve_codex_porter.host_runtime._codex_executable", return_value=("codex-test", None)),
            patch("hve_codex_porter.host_runtime._init_isolated_repo", return_value={"status": "initialized"}),
            patch("hve_codex_porter.host_runtime._execute_codex_turn", return_value=launch_error) as execute,
        ):
            report = run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=1, timeout_seconds=120)

        self.assertEqual(execute.call_count, 1)
        self.assertEqual(report["execution"]["sessions_attempted"], 1)
        self.assertEqual(report["execution"]["model_sessions_started"], 0)
        self.assertEqual(report["scenarios"][1]["behavioral_status"], "not-started-session-budget-exhausted")

    def test_path_traversal_fixture_is_reported_and_budget_validation_rejects_zero(self) -> None:
        scenario = self.one_scenario()
        scenario["fixture_files"] = {"../escape.txt": "must not write"}
        self.write_scenarios([scenario])
        report = prepare_evaluations(self.distribution, self.scenarios, self.output)
        self.assertTrue(report["scenarios"][0]["fixture_errors"])
        self.assertFalse((self.root / "escape.txt").exists())

        with self.assertRaisesRegex(PortError, "max_sessions"):
            run_evaluations(self.distribution, self.scenarios, self.output, max_sessions=0)

    def test_canonical_artifact_glob_requires_a_date_directory(self) -> None:
        root = self.root / "workspace"
        valid = root / ".copilot-tracking" / "research" / "2026-10-10" / "retry-limit-research.md"
        valid.parent.mkdir(parents=True)
        valid.write_text("research\n", encoding="utf-8")
        check = {
            "id": "canonical-research",
            "kind": "workspace_glob_exists",
            "path": ".copilot-tracking/research/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/retry-limit-research.md",
        }
        result = _check_result("golden-path-01", check, root, self.distribution)
        self.assertEqual(result["status"], "passed")

        valid.unlink()
        invalid = root / ".copilot-tracking" / "research" / "latest" / "retry-limit-research.md"
        invalid.parent.mkdir(parents=True)
        invalid.write_text("wrong directory contract\n", encoding="utf-8")
        result = _check_result("golden-path-01", check, root, self.distribution)
        self.assertEqual(result["status"], "failed")

    def test_workspace_postchecks_use_snapshot_without_host_path_resolution(self) -> None:
        root = self.root / "workspace"
        relative = ".copilot-tracking/reviews/retry-policy-review.md"
        source = root / Path(*relative.split("/"))
        record = {"sha256": hashlib.sha256(b"review").hexdigest(), "bytes": len(b"review")}
        workspace_evidence = {"files": {relative: record}, "errors": []}
        real_resolve = Path.resolve

        def deny_tracking_resolution(path, *args, **kwargs):
            if path == source:
                raise PermissionError(5, "Access is denied", str(path))
            return real_resolve(path, *args, **kwargs)

        exists_check = {
            "id": "review-exists",
            "kind": "workspace_file_exists",
            "path": relative,
        }
        glob_check = {
            "id": "review-glob",
            "kind": "workspace_glob_exists",
            "path": ".copilot-tracking/reviews/*.md",
        }
        with patch.object(Path, "resolve", deny_tracking_resolution):
            exists_result = _check_result(
                "golden-path-03",
                exists_check,
                root,
                self.distribution,
                workspace_evidence=workspace_evidence,
            )
            glob_result = _check_result(
                "golden-path-03",
                glob_check,
                root,
                self.distribution,
                workspace_evidence=workspace_evidence,
            )

        self.assertEqual(exists_result["status"], "passed")
        self.assertEqual(glob_result["status"], "passed")

    def test_stage_fixture_checks_are_preserved_after_model_creates_workspace_file(self) -> None:
        root = self.root / "workspace"
        root.mkdir()
        fixture = root / "fixtures" / "coaching-state.md"
        fixture.parent.mkdir()
        fixture.write_text("initial coaching state\n", encoding="utf-8")
        summary_path = ".copilot-tracking/dt/profile-preferences/handoff-summary.md"
        scenario = {
            "id": "integration-05",
            "deterministic_checks": [
                {"id": "summary-absent-before-run", "kind": "fixture_absent", "path": summary_path},
                {"id": "state-fixture-exists", "kind": "fixture_exists", "path": "fixtures/coaching-state.md"},
                {
                    "id": "state-fixture-contains",
                    "kind": "fixture_contains",
                    "path": "fixtures/coaching-state.md",
                    "text": "initial coaching state",
                },
                {"id": "summary-created", "kind": "workspace_file_exists", "path": summary_path},
                {
                    "id": "source-recovery-rechecked",
                    "kind": "source_artifact_recovery",
                    "sources": [".github/agents/design-thinking/dt-coach.agent.md"],
                },
            ],
        }
        stage_results = _scenario_stage_preconditions(scenario, root, self.distribution, {})
        self.assertEqual(
            {value["check_id"]: value["status"] for value in stage_results.values()},
            {
                "summary-absent-before-run": "passed",
                "state-fixture-exists": "passed",
                "state-fixture-contains": "passed",
            },
        )

        summary = root / Path(*summary_path.split("/"))
        summary.parent.mkdir(parents=True)
        summary.write_text("model-created summary\n", encoding="utf-8")
        fixture.write_text("updated coaching state\n", encoding="utf-8")
        workspace_evidence = {
            "files": {
                summary_path: {
                    "sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
                    "bytes": summary.stat().st_size,
                },
            },
            "errors": [],
        }
        with patch("hve_codex_porter.host_acceptance._check_result", wraps=_check_result) as evaluate_check:
            checks = _scenario_checks(
                scenario,
                root,
                self.distribution,
                {},
                execute_postchecks=True,
                stage_preconditions=stage_results,
                workspace_evidence=workspace_evidence,
            )

        by_id = {item["check_id"]: item for item in checks}
        self.assertEqual(by_id["summary-absent-before-run"]["status"], "passed")
        self.assertEqual(by_id["state-fixture-exists"]["status"], "passed")
        self.assertEqual(by_id["state-fixture-contains"]["status"], "passed")
        self.assertEqual(by_id["summary-created"]["status"], "passed")
        self.assertTrue(
            any(call.args[1].get("kind") == "source_artifact_recovery" for call in evaluate_check.call_args_list)
        )

    def test_workspace_postcheck_path_resolution_permission_is_pending_without_inventory(self) -> None:
        root = self.root / "workspace"
        relative = ".copilot-tracking/reviews/retry-policy-review.md"
        target = root / Path(*relative.split("/"))
        real_resolve = Path.resolve

        def deny_target_resolution(path, *args, **kwargs):
            if path == target:
                raise PermissionError(5, "Access is denied", str(path))
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", deny_target_resolution):
            result = _check_result(
                "golden-path-03",
                {"id": "review-exists", "kind": "workspace_file_exists", "path": relative},
                root,
                self.distribution,
            )

        self.assertEqual(result["status"], "pending-runtime")
        self.assertIn("metadata could not be verified", result["reason"])

    def test_workspace_json_postcheck_reaches_native_read_after_resolution_denial(self) -> None:
        root = self.root / "workspace"
        relative = ".copilot-tracking/rai-plans/profile-preferences/state.json"
        target = root / Path(*relative.split("/"))
        content = b'{"projectSlug":"profile-preferences"}\n'
        snapshot = {
            relative: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            },
        }
        real_resolve = Path.resolve

        def deny_target_resolution(path, *args, **kwargs):
            if path == target:
                raise PermissionError(5, "Access is denied", str(path))
            return real_resolve(path, *args, **kwargs)

        with (
            patch.object(Path, "resolve", deny_target_resolution),
            patch(
                "hve_codex_porter.host_acceptance._native_workspace_read_files",
                return_value=({relative: content}, []),
            ) as native_read,
        ):
            result = _check_result(
                "golden-path-03",
                {
                    "id": "rai-state",
                    "kind": "workspace_json_value",
                    "path": relative,
                    "pointer": "/projectSlug",
                    "expected": "profile-preferences",
                },
                root,
                self.distribution,
                workspace_evidence={"files": snapshot, "errors": []},
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
            )

        self.assertEqual(result["status"], "passed")
        native_read.assert_called_once_with(
            root,
            [relative],
            r"C:\Codex\codex.exe",
            "elevated",
        )

    def test_workspace_glob_postcheck_does_not_follow_symlinked_parent(self) -> None:
        root = self.root / "workspace"
        actual = root / "actual"
        actual.mkdir(parents=True)
        (actual / "review.md").write_text("not eligible through a link", encoding="utf-8")
        link = root / "linked"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink creation is unavailable")

        result = _check_result(
            "golden-path-03",
            {"id": "linked-review", "kind": "workspace_glob_exists", "path": "linked/*.md"},
            root,
            self.distribution,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["matches"], [])

    def test_workspace_snapshot_uses_native_inventory_after_denied_directory_listing(self) -> None:
        root = self.root / "workspace"
        denied = root / ".copilot-tracking"
        denied.mkdir(parents=True)
        (root / "visible.txt").write_text("visible", encoding="utf-8")
        (denied / "created.md").write_text("native evidence", encoding="utf-8")
        native_files = [
            {
                "path": "visible.txt",
                "bytes": len(b"visible"),
                "sha256": hashlib.sha256(b"visible").hexdigest(),
            },
            {
                "path": ".copilot-tracking/created.md",
                "bytes": len(b"native evidence"),
                "sha256": hashlib.sha256(b"native evidence").hexdigest(),
            },
        ]

        def denied_walk(path, *, topdown, onerror, followlinks):
            self.assertTrue(topdown)
            self.assertFalse(followlinks)
            yield str(path), [".copilot-tracking"], ["visible.txt"]
            onerror(PermissionError(5, "Access is denied", str(denied)))

        def denied_tracking_metadata(path):
            if path.name == ".copilot-tracking":
                raise PermissionError(5, "Access is denied", str(path))
            return path.is_symlink()

        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime.os.walk", side_effect=denied_walk),
            patch(
                "hve_codex_porter.host_runtime._is_link_or_reparse",
                side_effect=denied_tracking_metadata,
            ),
            patch(
                "hve_codex_porter.host_runtime._native_workspace_response",
                return_value=({"version": 1, "complete": True, "files": native_files, "errors": [], "truncated": False}, None),
            ) as native_inventory,
        ):
            snapshot, errors = _workspace_snapshot(
                root,
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
            )

        self.assertEqual(set(snapshot), {"visible.txt", ".copilot-tracking/created.md"})
        self.assertEqual(errors, [])
        native_inventory.assert_called_once_with(
            root.resolve(),
            r"C:\Codex\codex.exe",
            "elevated",
            "inventory",
            None,
        )

    def test_native_inventory_truncation_supplements_and_retains_prioritized_file(self) -> None:
        root = self.root / "workspace"
        relative = ".copilot-tracking/reviews/target-review.md"
        source = root / relative
        source.parent.mkdir(parents=True)
        before = b"before review\n"
        after = b"target review evidence\n"
        source.write_bytes(after)
        baseline = {relative: {"sha256": hashlib.sha256(before).hexdigest(), "bytes": len(before)}}
        fixture_baseline = {relative: hashlib.sha256(before).hexdigest()}
        scenario = {
            "deterministic_checks": [
                {"kind": "workspace_file_exists", "path": relative},
                {"kind": "workspace_file_contains", "path": relative, "text": "evidence"},
                {"kind": "workspace_file_unchanged", "path": relative},
                {"kind": "workspace_json_value", "path": "summary.json"},
                {"kind": "fixture_hash_at_turn", "path": "src/input.py"},
                {"kind": "workspace_glob_exists", "path": "reviews/*.md"},
                {"kind": "workspace_file_absent", "path": "not-created.md"},
            ],
        }
        priority_paths = _scenario_workspace_priority_paths(scenario, fixture_baseline)
        self.assertEqual(
            priority_paths,
            sorted({relative, "summary.json", "src/input.py"}),
        )

        def denied_walk(path, *, topdown, onerror, followlinks):
            self.assertTrue(topdown)
            self.assertFalse(followlinks)
            yield str(path), [], []
            onerror(PermissionError(5, "Access is denied", str(root / ".copilot-tracking")))

        incomplete_inventory = {
            "version": 1,
            "complete": False,
            "files": [],
            "errors": [{"path": ".", "reason": "native inventory file limit exceeded"}],
            "truncated": True,
        }

        def prioritized_read(workspace, paths, executable, sandbox_mode):
            self.assertEqual(workspace, root.resolve())
            self.assertIn(relative, paths)
            self.assertEqual(executable, r"C:\Codex\codex.exe")
            self.assertEqual(sandbox_mode, "elevated")
            errors = [
                {"path": path, "reason": "file not found"}
                for path in paths
                if path != relative
            ]
            return {relative: after}, errors

        checkpoint_root = self.output / "evaluations-report.sessions" / "case" / "workspace" / "turn-01"
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime.os.walk", side_effect=denied_walk),
            patch(
                "hve_codex_porter.host_runtime._native_workspace_response",
                return_value=(incomplete_inventory, None),
            ),
            patch(
                "hve_codex_porter.host_runtime._native_workspace_read_files",
                side_effect=prioritized_read,
            ) as native_read,
        ):
            current, snapshot_errors = _workspace_snapshot(
                root,
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
                priority_paths=priority_paths,
            )
            content_check = _check_result(
                "edge-priority-capture",
                {
                    "id": "target-content",
                    "kind": "workspace_file_contains",
                    "path": relative,
                    "text": "target review evidence",
                },
                root,
                self.distribution,
                workspace_evidence={"files": current, "errors": snapshot_errors},
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
            )
            checkpoint = _capture_workspace_checkpoint(
                root,
                baseline,
                checkpoint_root,
                fixture_baseline,
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
                priority_paths=[relative],
            )

        self.assertTrue(snapshot_errors)
        self.assertEqual(current[relative]["sha256"], hashlib.sha256(after).hexdigest())
        self.assertEqual(content_check["status"], "passed")
        self.assertEqual(checkpoint["status"], "incomplete")
        self.assertEqual(checkpoint["inventory_status"], "incomplete")
        self.assertTrue(any("file limit" in item["reason"] for item in checkpoint["errors"]))
        self.assertEqual(checkpoint["fixture_hashes"][0]["status"], "modified")
        self.assertEqual(checkpoint["fixture_hashes"][0]["actual_sha256"], hashlib.sha256(after).hexdigest())
        self.assertEqual(len(checkpoint["changed_files"]), 1)
        self.assertTrue(checkpoint["changed_files"][0]["retained"])
        self.assertEqual((checkpoint_root / relative).read_bytes(), after)
        self.assertEqual(native_read.call_count, 2)

    def test_missing_prioritized_file_after_truncation_stays_pending(self) -> None:
        root = self.root / "workspace"
        root.mkdir()
        relative = ".copilot-tracking/state/profile.md"
        expected_hash = "a" * 64
        scenario = {
            "id": "edge-priority-missing",
            "deterministic_checks": [{
                "id": "fixture-state-modified",
                "kind": "fixture_hash_at_turn",
                "path": relative,
                "after_prompt": 1,
                "expected_status": "modified",
            }],
        }
        initial_checks = [{
            "scenario_id": scenario["id"],
            "check_id": "fixture-state-modified",
            "kind": "fixture_hash_at_turn",
            "status": "pending-runtime",
        }]

        def denied_walk(path, *, topdown, onerror, followlinks):
            self.assertTrue(topdown)
            self.assertFalse(followlinks)
            yield str(path), [], []
            onerror(PermissionError(5, "Access is denied", str(root / ".copilot-tracking")))

        incomplete_inventory = {
            "version": 1,
            "complete": False,
            "files": [],
            "errors": [{"path": ".", "reason": "native inventory file limit exceeded"}],
            "truncated": True,
        }
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime.os.walk", side_effect=denied_walk),
            patch(
                "hve_codex_porter.host_runtime._native_workspace_response",
                return_value=(incomplete_inventory, None),
            ),
            patch(
                "hve_codex_porter.host_runtime._native_workspace_read_files",
                return_value=({}, [{"path": relative, "reason": "file not found"}]),
            ) as native_read,
        ):
            current, errors = _workspace_snapshot(
                root,
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
                priority_paths=[relative],
            )
            self.assertNotIn(relative, current)
            checkpoint = _capture_workspace_checkpoint(
                root,
                {relative: {"sha256": expected_hash, "bytes": 10}},
                self.output / "evaluations-report.sessions" / "case" / "workspace" / "turn-01",
                {relative: expected_hash},
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
                priority_paths=[relative],
            )

        self.assertTrue(errors)
        self.assertEqual(checkpoint["fixture_hashes"][0]["status"], "unknown")
        self.assertEqual(checkpoint["deleted_files"], [])
        self.assertEqual(checkpoint["unknown_paths"], [relative])
        checks = _apply_checkpoint_assertions(
            scenario,
            initial_checks,
            [{"prompt_index": 0, "status": "completed", "workspace_checkpoint": checkpoint}],
        )
        self.assertEqual(checks[0]["status"], "pending-runtime")
        self.assertEqual(checks[0]["actual_status"], "unknown")
        native_read.assert_called()

    def test_native_workspace_paths_reject_malformed_and_escaping_values(self) -> None:
        root = self.root / "workspace"
        root.mkdir()
        for value in ("../escape.txt", "/outside.txt", "C:/outside.txt", "dir\\file.txt", ".git/config"):
            with self.subTest(path=value), self.assertRaises(PortError):
                _validate_native_relative_path(root, value)

    def test_native_attestation_allows_denied_host_metadata_but_still_rejects_reparse_points(self) -> None:
        root = self.root / "workspace"
        tracking = root / ".copilot-tracking"
        tracking.mkdir(parents=True)
        source = tracking / "review.md"
        source.write_text("native attestation", encoding="utf-8")

        def metadata_result(path):
            if path == tracking:
                raise PermissionError(5, "Access is denied", str(path))
            return False

        with patch("hve_codex_porter.host_acceptance._is_link_or_reparse", side_effect=metadata_result):
            self.assertEqual(
                _validate_native_attested_path(root, ".copilot-tracking/review.md"),
                ".copilot-tracking/review.md",
            )

        def reparse_result(path):
            return path == tracking

        with patch("hve_codex_porter.host_acceptance._is_link_or_reparse", side_effect=reparse_result):
            with self.assertRaises(PortError):
                _validate_native_attested_path(root, ".copilot-tracking/review.md")

        for value in ("../escape.txt", "/outside.txt", "C:/outside.txt", "dir\\file.txt", "a//b", "CON.txt"):
            with self.subTest(path=value), patch("hve_codex_porter.host_acceptance._is_link_or_reparse") as metadata:
                with self.assertRaises(PortError):
                    _validate_native_attested_path(root, value)
                metadata.assert_not_called()

    def test_checkpoint_reads_denied_changed_file_through_native_sandbox_evidence(self) -> None:
        root = self.root / "workspace"
        source = root / ".copilot-tracking" / "changes" / "2026-10-10" / "bounded-retry.md"
        source.parent.mkdir(parents=True)
        content = b"bounded native checkpoint evidence\n"
        source.write_bytes(content)
        checkpoint_root = self.output / "evaluations-report.sessions" / "case" / "workspace" / "turn-01"
        real_open = Path.open

        def denied_host_open(path, *args, **kwargs):
            if path.resolve() == source.resolve() and args and args[0] == "rb":
                raise PermissionError(5, "Access is denied", str(path))
            return real_open(path, *args, **kwargs)

        snapshot = {
            ".copilot-tracking/changes/2026-10-10/bounded-retry.md": {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            },
        }
        with (
            patch("hve_codex_porter.host_runtime._is_native_windows", return_value=True),
            patch("hve_codex_porter.host_runtime._workspace_snapshot", return_value=(snapshot, [])),
            patch(
                "hve_codex_porter.host_runtime._native_workspace_read_files",
                return_value=({".copilot-tracking/changes/2026-10-10/bounded-retry.md": content}, []),
            ) as native_read,
            patch.object(Path, "open", denied_host_open),
        ):
            checkpoint = _capture_workspace_checkpoint(
                root,
                {},
                checkpoint_root,
                {},
                native_command=r"C:\Codex\codex.exe",
                native_sandbox_mode="elevated",
            )

        self.assertEqual(checkpoint["status"], "complete")
        self.assertTrue(checkpoint["changed_files"][0]["retained"])
        evidence_path = self.output / "evaluations-report.sessions" / checkpoint["changed_files"][0]["evidence_path"]
        self.assertEqual(evidence_path.read_bytes(), content)
        native_read.assert_called_once_with(
            root,
            [".copilot-tracking/changes/2026-10-10/bounded-retry.md"],
            r"C:\Codex\codex.exe",
            "elevated",
        )

    def test_incomplete_checkpoint_inventory_does_not_claim_fixture_deletion(self) -> None:
        root = self.root / "workspace"
        root.mkdir()
        relative = ".copilot-tracking/dt/profile-preferences/coaching-state.md"
        expected_hash = "a" * 64
        baseline = {relative: {"sha256": expected_hash, "bytes": 20}}
        scenario = {
            "id": "edge-integration-05",
            "deterministic_checks": [
                {
                    "id": "state-updated-after-explicit-choice",
                    "kind": "fixture_hash_at_turn",
                    "path": relative,
                    "after_prompt": 1,
                    "expected_status": "modified",
                },
            ],
        }
        initial_checks = [{
            "scenario_id": scenario["id"],
            "check_id": "state-updated-after-explicit-choice",
            "kind": "fixture_hash_at_turn",
            "status": "pending-runtime",
        }]

        def capture(errors: list[dict[str, str]]) -> dict:
            checkpoint_root = self.output / "evaluations-report.sessions" / "case" / "workspace" / "turn-01"
            with patch(
                "hve_codex_porter.host_runtime._workspace_snapshot",
                return_value=({}, errors),
            ):
                return _capture_workspace_checkpoint(
                    root,
                    baseline,
                    checkpoint_root,
                    {relative: expected_hash},
                )

        incomplete = capture([{"path": relative, "reason": "Native workspace inventory was truncated."}])
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertEqual(incomplete["inventory_status"], "incomplete")
        self.assertEqual(incomplete["deleted_files"], [])
        self.assertEqual(incomplete["unknown_paths"], [relative])
        self.assertEqual(incomplete["fixture_hashes"][0]["status"], "unknown")
        incomplete_checks = _apply_checkpoint_assertions(
            scenario,
            [dict(initial_checks[0])],
            [{"prompt_index": 0, "status": "completed", "workspace_checkpoint": incomplete}],
        )
        self.assertEqual(incomplete_checks[0]["status"], "pending-runtime")
        self.assertEqual(incomplete_checks[0]["actual_status"], "unknown")

        complete = capture([])
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["inventory_status"], "complete")
        self.assertEqual(complete["deleted_files"], [relative])
        self.assertEqual(complete["unknown_paths"], [])
        self.assertEqual(complete["fixture_hashes"][0]["status"], "deleted")
        complete_checks = _apply_checkpoint_assertions(
            scenario,
            [dict(initial_checks[0])],
            [{"prompt_index": 0, "status": "completed", "workspace_checkpoint": complete}],
        )
        self.assertEqual(complete_checks[0]["status"], "failed")
        self.assertEqual(complete_checks[0]["actual_status"], "deleted")

    def test_json_pointer_supports_root_and_empty_key(self) -> None:
        value = {"": "empty", "nested": {"key": 3}}
        self.assertEqual(_json_pointer(value, ""), (True, value))
        self.assertEqual(_json_pointer(value, "/"), (True, "empty"))
        self.assertEqual(_json_pointer(value, "/nested/key"), (True, 3))
        self.assertEqual(_json_pointer(value, "/missing"), (False, None))

    def test_spec_scenarios_cover_ten_requested_workflows(self) -> None:
        path = REPOSITORY / "tests" / "fixtures" / "legacy-scenarios" / "scenarios.json"
        from hve_codex_porter.host_runtime import _scenario_data

        data = _scenario_data(path)
        self.assertEqual(len(data["scenarios"]), 10)
        expected_ids = [f"golden-path-{index:02}" for index in range(1, 11)]
        ids = [scenario["id"] for scenario in data["scenarios"]]
        self.assertEqual(ids, expected_ids)
        for scenario in data["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self.assertTrue(scenario["key"])
                self.assertTrue(scenario["prompts"])
                self.assertTrue(scenario["rubric"])
                self.assertTrue(scenario["deterministic_checks"])
                self.assertTrue(scenario["fixture_files"])
                self.assertTrue(scenario["entrypoints"])
                self.assertTrue(scenario["source_artifacts"])
                self.assertTrue(any("$actual-entrypoint" in prompt for prompt in scenario["prompts"]))
                self.assertTrue(any(check["kind"] == "source_artifact_recovery" for check in scenario["deterministic_checks"]))
        rpi = data["scenarios"][0]
        self.assertTrue(any("MANUAL variant" in prompt for prompt in rpi["prompts"]))
        self.assertTrue(any("FULL-AUTO variant" in prompt for prompt in rpi["prompts"]))
        all_fixture_paths = [path for scenario in data["scenarios"] for path in scenario["fixture_files"]]
        self.assertNotIn("work/STATE.json", all_fixture_paths)
        self.assertEqual(sum(len(scenario["prompts"]) for scenario in data["scenarios"]), 16)
        self.assertGreaterEqual(20, len(data["scenarios"]))
        self.assertEqual(
            {check["id"] for check in rpi["deterministic_checks"] if check["kind"] == "fixture_hash_at_turn"},
            {
                "manual-source-unchanged-after-resume",
                "manual-tests-unchanged-after-resume",
                "full-auto-source-modified-after-implementation-resume",
                "full-auto-tests-modified-after-implementation-resume",
            },
        )
        rpi_check_ids = {check["id"] for check in rpi["deterministic_checks"]}
        self.assertNotIn("canonical-change-log", rpi_check_ids)
        self.assertIn("full-auto-change-log", rpi_check_ids)
        canonical_globs = [
            check["path"]
            for check in rpi["deterministic_checks"]
            if check["kind"] == "workspace_glob_exists"
        ]
        self.assertTrue(canonical_globs)
        self.assertTrue(all("[0-9][0-9][0-9][0-9]-" in pattern for pattern in canonical_globs))
        requirements = data["scenarios"][3]
        review_check = next(
            check for check in requirements["deterministic_checks"] if check["id"] == "independent-review"
        )
        self.assertEqual(review_check["kind"], "workspace_glob_exists")
        self.assertEqual(
            review_check["path"],
            ".copilot-tracking/reviews/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/import-retry*-review.md",
        )
        stable_handoff = data["scenarios"][7]
        edge_data = _scenario_data(REPOSITORY / "tests" / "fixtures" / "legacy-scenarios" / "edge-scenarios.json")
        edge_handoff = edge_data["scenarios"][7]
        for handoff in (stable_handoff, edge_handoff):
            handoff_artifacts = [
                check for check in handoff["deterministic_checks"]
                if check.get("path", "").endswith(("handoff-summary.md", "rpi-handoff-problem-space.md"))
            ]
            self.assertTrue(handoff_artifacts)
            self.assertTrue(all(check["kind"] == "workspace_file_absent" for check in handoff_artifacts))
            self.assertTrue(any(check["kind"] == "approval_gate" for check in handoff["deterministic_checks"]))
        edge_review = edge_data["scenarios"][1]
        self.assertEqual(edge_review["title"], "Read-only review orientation and confirmation pause")
        self.assertEqual(
            edge_review["prompts"],
            [
                "Invoke $actual-entrypoint to review src/profile_preferences.py. Delegate to $entrypoint[privacy] and $entrypoint[accessibility] only as current review procedure requires. Use the source-defined branch review folder and pause at orientation confirmation before detailed findings or final review. Do not edit source or check human review."
            ],
        )
        edge_review_checks = {check["id"]: check for check in edge_review["deterministic_checks"]}
        expected_orientation_artifacts = {
            "orientation-walkthrough": "orientation-walkthrough.md",
            "diff-state": "diff-state.json",
        }
        for check_id, filename in expected_orientation_artifacts.items():
            with self.subTest(edge_review_check=check_id):
                check = edge_review_checks[check_id]
                self.assertEqual(check["kind"], "workspace_glob_exists")
                self.assertTrue(check["path"].startswith(".copilot-tracking/reviews/code-reviews/*/"))
                self.assertTrue(check["path"].endswith(f"/{filename}"))
        self.assertTrue(any(check["kind"] == "approval_gate" for check in edge_review["deterministic_checks"]))
        self.assertFalse(
            any(check.get("path", "").endswith("/profile-preferences-review.md") for check in edge_review["deterministic_checks"])
        )
        edge_review_rubric = " ".join(edge_review["rubric"])
        self.assertIn("without writing a final findings report", edge_review_rubric)
        self.assertIn("unconfirmed perspectives", edge_review_rubric)
        self.assertIn("Dispatch board", edge_review_rubric)
        self.assertIn("after confirmation", edge_review_rubric)
        self.assertNotIn("dispatch-board", edge_review_checks)
        self.assertNotIn("dispatch-state", edge_review_checks)
        change_fixture = data["scenarios"][9]["fixture_files"]
        baseline_metadata = change_fixture["upstream/stable/manifest.json"]["artifacts"][0]["frontmatter"]
        candidate_metadata = change_fixture["upstream/main/manifest.json"]["artifacts"][0]["frontmatter"]
        self.assertTrue(set(candidate_metadata) - set(baseline_metadata))

    def test_post_choice_dt_handoffs_preserve_synthetic_source_contracts(self) -> None:
        from hve_codex_porter.host_runtime import _scenario_data

        stable = _scenario_data(REPOSITORY / "tests" / "fixtures" / "legacy-scenarios" / "integration-scenarios.json")
        edge = _scenario_data(REPOSITORY / "tests" / "fixtures" / "legacy-scenarios" / "edge-integration-scenarios.json")
        stable_ids = [scenario["id"] for scenario in stable["scenarios"]]
        edge_ids = [scenario["id"] for scenario in edge["scenarios"]]
        self.assertEqual(stable_ids, [f"integration-{index:02}" for index in range(1, 6)])
        self.assertEqual(edge_ids, [f"edge-integration-{index:02}" for index in range(1, 7)])

        cases = (
            (
                stable["scenarios"][4],
                "e69486a5f809ede45c63c0a31358c12912bd5168",
                "researcher",
                "to: rpi-researcher",
            ),
            (
                edge["scenarios"][4],
                "49dfb73866f8c5f81bf734d6ca808dac25a2a330",
                "rpi-research",
                'to: "rpi-research"',
            ),
        )
        state_path = ".copilot-tracking/dt/profile-preferences/coaching-state.md"
        summary_path = ".copilot-tracking/dt/profile-preferences/handoff-summary.md"
        rpi_path = ".copilot-tracking/dt/profile-preferences/rpi-handoff-problem-space.md"
        for scenario, upstream_sha, summary_target, transition_fragment in cases:
            with self.subTest(scenario=scenario["id"]):
                self.assertEqual(scenario["invocation_mode"], "explicit")
                self.assertEqual(len(scenario["prompts"]), 1)
                prompt = scenario["prompts"][0]
                self.assertIn("I explicitly choose the lateral Problem Space handoff to RPI now", prompt)
                self.assertIn("Methods 1, 2, and 3 are complete", prompt)
                self.assertIn("downstream research, planning, or implementation", prompt)

                provenance = scenario["fixture_provenance"]
                self.assertEqual(provenance["classification"], "synthetic-evaluation-only")
                self.assertEqual(provenance["upstream_sha"], upstream_sha)
                self.assertEqual(provenance["source_artifacts"], scenario["source_artifacts"])
                self.assertIn("No real interviews", provenance["note"])

                state = scenario["fixture_files"][state_path]
                self.assertIn("methods_completed:\n  - 1\n  - 2\n  - 3", state)
                self.assertIn("no lateral handoff choice has been recorded before the current user request", state)
                self.assertTrue(all("Synthetic evaluation fixture only" in content for content in scenario["fixture_files"].values()))

                checks = {check["id"]: check for check in scenario["deterministic_checks"]}
                self.assertEqual(
                    checks["state-updated-after-explicit-choice"],
                    {
                        "id": "state-updated-after-explicit-choice",
                        "kind": "fixture_hash_at_turn",
                        "path": state_path,
                        "after_prompt": 1,
                        "expected_status": "modified",
                    },
                )
                self.assertEqual(checks["handoff-summary-created"]["path"], summary_path)
                self.assertEqual(checks["handoff-summary-created"]["kind"], "workspace_file_exists")
                self.assertEqual(checks["rpi-entry-created"]["path"], rpi_path)
                self.assertEqual(checks["rpi-entry-created"]["kind"], "workspace_file_exists")
                self.assertEqual(checks["source-provenance-recovered"]["kind"], "source_artifact_recovery")
                self.assertEqual(checks["source-provenance-recovered"]["sources"], scenario["source_artifacts"])
                self.assertIn(f'handoff_target: "{summary_target}"', checks["summary-target"]["text"])
                self.assertIn(transition_fragment, checks["lateral-transition"]["text"])

        stable_case = stable["scenarios"][4]
        edge_case = edge["scenarios"][4]
        self.assertIn(".github/agents/hve-core/task-researcher.agent.md", stable_case["source_artifacts"])
        self.assertIn("Task Researcher", stable_case["prompts"][0])
        self.assertIn(".github/agents/hve-core/rpi-agent.agent.md", edge_case["source_artifacts"])
        self.assertIn("dt-artifact-paths-section", {check["id"] for check in edge_case["deterministic_checks"]})

if __name__ == "__main__":
    unittest.main()
