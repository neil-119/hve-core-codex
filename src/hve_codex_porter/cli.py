"""The hve-codex command-line interface."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import subprocess
from pathlib import Path
from .common import PortError, dump_json, digest
from .inventory import acquire, git, scan
from .conversion import convert
from .packaging import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(__file__).parent / "data"
DEFAULT_MAPPINGS = REPO_ROOT / "mappings" if (REPO_ROOT / "mappings").is_dir() else DATA_ROOT / "mappings"
DEFAULT_SCHEMA = REPO_ROOT / "schemas/plugin.schema.json" if (REPO_ROOT / "schemas").is_dir() else DATA_ROOT / "schemas/plugin.schema.json"

def default_distribution() -> Path:
    """Return the external distribution path selected for this CLI invocation."""
    configured = os.environ.get("HVE_CODEX_DISTRIBUTION")
    if configured and configured.strip():
        return Path(configured).expanduser()
    return Path.home() / ".codex" / "hve-core-codex-distribution"

class _ArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        if parsed.command in {"install", "update", "uninstall", "doctor"} and not hasattr(parsed, "scope"):
            parsed.scope = "project"
        return parsed


def parser() -> argparse.ArgumentParser:
    app = _ArgumentParser(prog="hve-codex", description="Convert, inspect, and safely install HVE Core for Codex")
    app.add_argument("--version", action="version", version="hve-codex 0.1.0")
    commands = app.add_subparsers(dest="command", required=True)
    for command in ("inventory", "convert"):
        sub = commands.add_parser(command)
        sub.add_argument("--upstream-url", default="https://github.com/microsoft/hve-core.git")
        sub.add_argument("--upstream-dir", type=Path)
        sub.add_argument("--ref")
        sub.add_argument("--sha")
        sub.add_argument("--channel", choices=("stable", "prerelease", "edge"), default="stable")
        sub.add_argument("--cache", type=Path, default=Path.home() / ".cache/hve-core-codex/sources")
        sub.add_argument("--output", type=Path, required=command == "convert")
        sub.add_argument("--mappings", type=Path, default=DEFAULT_MAPPINGS)
        sub.add_argument("--adapter-revision")
        sub.add_argument("--json", action="store_true")
    sub = commands.add_parser("validate")
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    sub.add_argument("--release-gate", action="store_true")
    sub.add_argument("--record", action="store_true", help="Record structural results and refresh report checksums")
    sub.add_argument("--json", action="store_true")
    for command in ("install", "update", "uninstall", "doctor"):
        sub = commands.add_parser(command)
        scope = sub.add_mutually_exclusive_group()
        scope.add_argument("--scope", choices=("project", "user"), default=argparse.SUPPRESS,
                           help="Install for one project or for your user across repositories")
        scope.add_argument("--global", dest="scope", action="store_const", const="user", default=argparse.SUPPRESS,
                           help="Use the shared user installation (alias for --scope user)")
        sub.add_argument("--target", type=Path)
        sub.add_argument("--codex-home", type=Path)
        sub.add_argument("--user-home", type=Path, help="Explicit personal discovery root for isolated user-scope installs")
        sub.add_argument("--json", action="store_true")
        if command != "doctor":
            sub.add_argument("--dry-run", action="store_true")
        if command in {"install", "update"}:
            sub.add_argument("--distribution", type=Path, default=default_distribution())
            sub.add_argument("--mode", choices=("full",), default="full")
            sub.add_argument("--no-hooks", action="store_true")
            sub.add_argument("--no-plugin", action="store_true")
    sub = commands.add_parser("rules")
    sub.add_argument("--root", type=Path)
    sub.add_argument("--json", action="store_true")
    rule_commands = sub.add_subparsers(dest="rules_command", required=True)
    rule_commands.add_parser("list")
    match = rule_commands.add_parser("match")
    match.add_argument("paths", nargs="+")
    explain = rule_commands.add_parser("explain")
    explain.add_argument("path")
    sub = commands.add_parser("eval")
    eval_commands = sub.add_subparsers(dest="eval_command", required=True)
    vally_prepare = eval_commands.add_parser(
        "vally-prepare", help="Deterministically convert upstream Vally specs and fixtures; starts no models"
    )
    vally_prepare.add_argument("--json", action="store_true")
    vally_prepare.add_argument("--upstream-dir", type=Path, required=True)
    vally_prepare.add_argument("--distribution", type=Path, default=default_distribution())
    vally_prepare.add_argument("--custom-dir", type=Path, help="Optional custom Vally source root under evals/codex-port; preserves upstream cases")
    vally_prepare.add_argument(
        "--fixture-dependencies",
        type=Path,
        help="Optional fresh root from vally-fixture-deps-prepare for exact locked node_modules fixtures",
    )
    vally_prepare.add_argument(
        "--eval-filename",
        action="append",
        dest="eval_filename_patterns",
        help="Select additional or alternate eval spec basenames (repeatable; defaults follow .vally.yaml)",
    )
    vally_prepare.add_argument("--output", type=Path, required=True)
    vally_dependencies = eval_commands.add_parser(
        "vally-fixture-deps-prepare",
        help="Verify offline npm tarballs against upstream package-lock SRI and prepare fixture dependencies",
    )
    vally_dependencies.add_argument("--json", action="store_true")
    vally_dependencies.add_argument("--upstream-dir", type=Path, required=True)
    vally_dependencies.add_argument("--expected-upstream-sha", required=True)
    vally_dependencies.add_argument("--installed-root", type=Path, required=True)
    vally_dependencies.add_argument("--npm-cache", type=Path, required=True)
    vally_dependencies.add_argument("--output", type=Path, required=True)
    vally_dependencies.add_argument(
        "--include-vally-cli",
        action="store_true",
        help="Also prepare exact lock-pinned @microsoft/vally-cli 0.17.0 and its full dependency closure for offline compare reuse",
    )
    vally_plan = eval_commands.add_parser("vally-plan", help="Validate a converted bundle with Vally's planner; starts no models")
    vally_plan.add_argument("--json", action="store_true")
    vally_plan.add_argument("--bundle", type=Path, required=True)
    vally_plan.add_argument("--output", type=Path, required=True)
    vally_plan.add_argument("--node-command", default="node")
    vally_run = eval_commands.add_parser("vally-run", help="Run selected upstream trials through Vally and Codex")
    vally_run.add_argument("--json", action="store_true")
    vally_run.add_argument("--bundle", type=Path, required=True)
    vally_run.add_argument("--output", type=Path, required=True, help="Fresh directory for Vally reports and workspaces")
    vally_run.add_argument("--eval", dest="eval_names", action="append", default=[], help="Exact upstream eval name; repeatable")
    vally_run.add_argument("--stimulus", dest="stimulus_names", action="append", default=[], help="Exact upstream stimulus name; repeatable")
    vally_run.add_argument("--dry-run", action="store_true", help="Plan and check capabilities without running trials")
    vally_run.add_argument("--node-command", default="node")
    vally_run.add_argument("--navigation-context", action="store_true", help="Enable the shipped generic HVE navigation guidance for task execution only")
    vally_run.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"), default=None)
    vally_run.add_argument("--codex-binary", help="Codex executable path; otherwise use the CLI on PATH")
    vally_run.add_argument("--judge-backend", choices=("none", "codex"), default="none",
                           help="LLM grader backend; codex uses the standard Codex app-server")
    vally_verify = eval_commands.add_parser("vally-verify", help="Independently verify saved Vally evidence; starts no models")
    vally_verify.add_argument("--json", action="store_true")
    vally_verify.add_argument("--bundle", type=Path, required=True)
    vally_verify.add_argument("--run", type=Path, required=True)
    vally_verify.add_argument("--source-sha", required=True)
    vally_verify.add_argument("--output", type=Path)
    vally_verify.add_argument("--node-command", default="node")
    host_tests = commands.add_parser("host-test", help="Custom Codex discovery, hook and role integration checks")
    host_commands = host_tests.add_subparsers(dest="host_command", required=True)
    coverage = host_commands.add_parser("coverage", help="Audit every artifact and declared scenario coverage without models")
    coverage.add_argument("--json", action="store_true")
    coverage.add_argument("--distribution", type=Path, default=default_distribution())
    coverage.add_argument("--scenarios", type=Path, action="append", required=True)
    coverage.add_argument("--output", type=Path, required=True)
    stage = host_commands.add_parser("stage", help="Persist a disposable scenario for human client validation; starts no models")
    stage.add_argument("--json", action="store_true")
    stage.add_argument("--distribution", type=Path, default=default_distribution())
    stage.add_argument("--scenarios", type=Path, required=True)
    stage.add_argument("--scenario-id", required=True)
    stage.add_argument("--output", type=Path, required=True)
    prepare = host_commands.add_parser("prepare", help="Stage fixtures and evaluate deterministic checks without model sessions")
    prepare.add_argument("--json", action="store_true")
    prepare.add_argument("--distribution", type=Path, default=default_distribution())
    prepare.add_argument("--scenarios", type=Path, default=REPO_ROOT / "tests/codex-host/edge.json" if (REPO_ROOT / "tests/codex-host").is_dir() else DATA_ROOT / "codex-host/edge.json")
    prepare.add_argument("--output", type=Path, required=True)
    run = host_commands.add_parser("run", help="Explicitly execute bounded real Codex tests (uses configured account access)")
    run.add_argument("--json", action="store_true")
    run.add_argument("--distribution", type=Path, default=default_distribution())
    run.add_argument("--scenarios", type=Path, default=REPO_ROOT / "tests/codex-host/edge.json" if (REPO_ROOT / "tests/codex-host").is_dir() else DATA_ROOT / "codex-host/edge.json")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--codex-command", default="codex")
    run.add_argument("--max-sessions", type=int, default=20)
    run.add_argument("--persistent-stage", type=Path, help="Execute one existing reviewed fixture without reinstalling or deleting it")
    run.add_argument("--isolate-computer-use", action="store_true", help="Disable unrelated computer-use plugins and node_repl MCP only for eval processes")
    run.add_argument("--timeout-seconds", type=int, default=1800)
    run.add_argument("--model", default="gpt-6-luna", help="Explicit model requested for each eval exec and resume")
    run.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="max",
        help="Explicit reasoning effort requested for the primary and delegated agents",
    )
    host = commands.add_parser("host-probe", help="Test real-client plugin registration in a disposable Codex home, without model sessions")
    host.add_argument("--distribution", type=Path, default=default_distribution())
    host.add_argument("--codex-command", required=True)
    host.add_argument("--output", type=Path)
    host.add_argument("--probe-kind", choices=("plugin", "discovery"), default="plugin")
    from .release import add_release_parser
    add_release_parser(commands)
    return app

def source(args) -> tuple[Path, dict]:
    if args.upstream_dir:
        root = args.upstream_dir.resolve()
        if not root.is_dir():
            raise PortError(f"Upstream directory missing: {root}")
        sha = git(root, "rev-parse", "HEAD") if (root / ".git").exists() else args.sha
        if not sha:
            raise PortError("Local sources require --sha or an immutable Git checkout")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise PortError("Local source SHA must be an exact 40-character commit")
        if args.sha and sha != args.sha:
            raise PortError("--sha disagrees with local checkout HEAD")
        # Inventory verifies staged changes, exact tracked paths and canonical Git
        # blob bytes. Git status EOL/stat caching is insufficient on Windows.
        result = {"upstream_url": args.upstream_url, "upstream_ref": args.ref or sha, "upstream_sha": sha, "channel": args.channel}
    else:
        root, result = acquire(args.upstream_url, args.ref, args.sha, args.channel, args.cache)
    if args.adapter_revision:
        result["adapter_revision"] = args.adapter_revision
    return root, result

def handle(args) -> tuple[object, int]:
    if args.command in {"inventory", "convert"}:
        if args.output is not None:
            output = args.output.expanduser().resolve()
            if output == REPO_ROOT or output.is_relative_to(REPO_ROOT):
                raise PortError("Generated output must be outside the migrator repository; use scripts/build.sh or an external --output")
        root, provenance = source(args)
        if args.command == "inventory":
            result = scan(root, provenance)
            if args.output:
                dump_json(args.output / "generated/source-inventory.json", result)
            return result, 0
        result = convert(root, args.output, provenance, args.mappings)
        return {"output": str(args.output.resolve()), "counts": result["counts"], "release_blockers": len(result["release_blockers"]), "coverage": result["coverage"]}, 0
    if args.command == "validate":
        result = validate(args.output, args.schema, record=args.record)
        return result, 0 if result["valid"] and (not args.release_gate or result["release_eligible"]) else 1
    if args.command in {"install", "update", "uninstall", "doctor"}:
        from . import installer
        if args.scope == "project" and not args.target:
            raise PortError("Project scope requires an explicitly selected --target")
        target = args.target or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        kwargs = {"target": target, "scope": args.scope, "codex_home": args.codex_home, "user_home": args.user_home}
        if args.command != "doctor":
            kwargs["dry_run"] = args.dry_run
        if args.command in {"install", "update"}:
            kwargs.update(distribution=args.distribution, mode=args.mode, no_hooks=args.no_hooks, no_plugin=args.no_plugin)
        result = getattr(installer, args.command)(**kwargs)
        return result, 1 if result.get("conflicts") else 0
    if args.command == "rules":
        from .rules import load_registry, match_rules, explain_rules
        root = args.root
        if root is None:
            cwd = Path.cwd()
            root = next((p / ".codex/rules" for p in [cwd, *cwd.parents] if (p / ".codex/rules/index.json").is_file()), default_distribution() / "codex-overlay/rules")
        if args.rules_command == "list":
            return load_registry(root), 0
        if args.rules_command == "match":
            return match_rules(root, args.paths), 0
        return explain_rules(root, args.path), 0
    if args.command == "eval":
        if args.eval_command == "vally-verify":
            if not re.fullmatch(r"[0-9a-fA-F]{40}", args.source_sha):
                raise PortError("Verification requires an exact 40-character upstream commit")
            adapter = REPO_ROOT / "vally-executor"
            if not adapter.is_dir():
                adapter = DATA_ROOT / "vally-executor"
            try:
                completed = subprocess.run(
                    [args.node_command, str(adapter / "verify.mjs"), "--bundle", str(args.bundle.resolve()),
                     "--run", str(args.run.resolve()), "--expected-upstream-sha", args.source_sha.lower(),
                     "--approved-adapter-root", str(adapter.resolve())],
                    capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
                )
                if len(completed.stdout) > 1024 * 1024:
                    raise PortError("Vally verifier returned an oversized receipt")
                result = json.loads(completed.stdout)
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
                raise PortError(f"Vally evidence verifier could not produce a receipt: {error}") from error
            if not isinstance(result, dict) or result.get("mode") != "vally-evidence-verification":
                raise PortError("Vally evidence verifier returned an invalid receipt")
            if args.output:
                dump_json(args.output, result)
            return result, 0 if completed.returncode == 0 and result.get("verified") is True else 1
        if args.eval_command == "vally-run":
            if args.output.exists():
                raise PortError("Vally run output must be a fresh directory")
            adapter = REPO_ROOT / "vally-executor"
            if not adapter.is_dir():
                adapter = DATA_ROOT / "vally-executor"
            command = [args.node_command, str(adapter / "run.mjs"), "--bundle", str(args.bundle.resolve()),
                       "--output", str(args.output.resolve())]
            for name in args.eval_names:
                command.extend(["--eval", name])
            for name in args.stimulus_names:
                command.extend(["--stimulus", name])
            if args.dry_run:
                command.append("--dry-run")
            if args.navigation_context:
                command.append("--navigation-context")
            if args.reasoning_effort:
                command.extend(["--reasoning-effort", args.reasoning_effort])
            if args.codex_binary:
                command.extend(["--codex-binary", args.codex_binary])
            if args.judge_backend != "none":
                command.extend(["--judge-backend", args.judge_backend])
            try:
                completed = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, check=False)
                report_raw = (args.output / "run-report.json").read_bytes()
                result = json.loads(report_raw)
            except (OSError, json.JSONDecodeError) as error:
                raise PortError(f"Vally launcher did not produce a readable run report: {error}") from error
            if not isinstance(result, dict) or result.get("mode") != "vally-run":
                raise PortError("Vally launcher returned an invalid report")
            return compact_vally_output(result, report_raw), completed.returncode
        if args.eval_command == "vally-plan":
            adapter = REPO_ROOT / "vally-executor"
            if not adapter.is_dir():
                adapter = DATA_ROOT / "vally-executor"
            command = [args.node_command, str(adapter / "plan.mjs"), str(args.bundle.resolve())]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise PortError(f"Vally offline planner could not start or finish: {error}") from error
            try:
                result = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise PortError(f"Vally offline planner failed: {completed.stderr[-8192:]}") from error
            if not isinstance(result, dict) or result.get("mode") != "vally-offline-plan" or result.get("modelCalls") != 0:
                raise PortError("Vally offline planner returned an invalid report")
            dump_json(args.output, result)
            return result, 0 if completed.returncode == 0 and result.get("valid") else 1
        if args.eval_command == "vally-prepare":
            from .vally_bundle import prepare_vally_bundle
            result = prepare_vally_bundle(
                args.upstream_dir,
                args.distribution,
                args.output,
                custom_dir=args.custom_dir,
                fixture_dependencies=args.fixture_dependencies,
                eval_filename_patterns=args.eval_filename_patterns,
            )
            return result, 0 if result.get("success") else 1
        if args.eval_command == "vally-fixture-deps-prepare":
            from .locked_fixture_dependencies import prepare_locked_fixture_dependencies

            result = prepare_locked_fixture_dependencies(
                args.upstream_dir,
                args.expected_upstream_sha,
                args.installed_root,
                args.npm_cache,
                args.output,
                include_vally_cli=args.include_vally_cli,
            )
            return result, 0 if result.get("success") else 1
    if args.command == "host-test":
        from .host_acceptance import _scenario_data
        scenario_paths = args.scenarios if isinstance(args.scenarios, list) else [args.scenarios]
        for path in scenario_paths:
            if _scenario_data(path).get("scope") != "codex-host":
                raise PortError("host-test accepts only scope=codex-host definitions; use Vally for workflow behavior")
        if args.host_command == "stage":
            from .coverage import stage_acceptance
            result = stage_acceptance(args.distribution, args.scenarios, args.scenario_id, args.output)
            failed = bool(result["fixture_errors"]) or result["entrypoint_resolution"]["status"] == "unresolved" or any(item["status"] in {"failed", "unsupported"} for item in result["deterministic_checks"])
            return evaluation_output(result, args), 1 if failed else 0
        if args.host_command == "coverage":
            from .coverage import acceptance_coverage
            result = acceptance_coverage(args.distribution, args.scenarios, args.output)
            failed = bool(result["unknown_scenario_sources"]) or any(item["structural_status"] == "failed" for item in result["artifacts"])
            return evaluation_output(result, args), 1 if failed else 0
        from .host_runtime import run_evaluations, prepare_evaluations
        if args.host_command == "prepare":
            result = prepare_evaluations(args.distribution, args.scenarios, args.output)
            summary = result.get("summary", {})
            failed = not result.get("distribution_exists") or any(summary.get(key, 0) for key in ("scenarios_with_fixture_errors", "deterministic_checks_failed", "unsupported_checks")) or any(b.get("code") == "scenario-entrypoint-resolution-failed" for b in result.get("stable_blockers", []))
            return evaluation_output(result, args), 1 if failed else 0
        result = run_evaluations(
            args.distribution,
            args.scenarios,
            args.output,
            args.codex_command,
            args.max_sessions,
            args.timeout_seconds,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            isolate_computer_use=args.isolate_computer_use,
            persistent_stage=args.persistent_stage,
        )
        return evaluation_output(result, args), 0 if result.get("passed", result.get("success", False)) else 1
    if args.command == "host-probe":
        from .host import probe_plugin, probe_discovery
        probe = probe_discovery if args.probe_kind == "discovery" else probe_plugin
        result = probe(args.distribution, args.codex_command, args.output)
        return result, 0 if result["passed"] else 1
    if args.command == "release":
        from .release import handle_release
        result = handle_release(args)
        return result, 1 if result.get("exit_failure") else 0
    raise PortError("Unknown command")

def compact_vally_output(result: dict, report_raw: bytes) -> dict:
    """Keep full source/trial proof in the saved report, not terminal output."""
    output = {key: value for key, value in result.items() if key != "runProvenance"}
    output["savedReport"] = {"sha256": digest(report_raw), "bytes": len(report_raw)}
    blockers = output.get("blockers")
    if isinstance(blockers, list):
        output["blockersTotal"] = len(blockers)
        output["blockers"] = blockers[:20]
        output["blockersTruncated"] = len(blockers) > 20
    provenance = result.get("runProvenance")
    if isinstance(provenance, dict):
        suite = provenance.get("suite", {})
        execution = provenance.get("execution", {})
        source = provenance.get("source", {})
        output["runProvenanceSummary"] = {
            "schema": provenance.get("schema"),
            "version": provenance.get("version"),
            "upstreamSha": source.get("declaredSourcePin", {}).get("sourceInventory", {}).get("upstreamSha"),
            "fullBundleTrials": suite.get("fullBundle", {}).get("plannedTrialCount"),
            "selectedTrials": suite.get("selected", {}).get("plannedTrialCount"),
            "selectionIsFullBundle": suite.get("selected", {}).get("selectionIsFullBundle"),
            "exactCoverage": execution.get("exactCoverage"),
            "fullSuiteExecution": execution.get("fullSuiteExecution"),
            "sourceUnchanged": source.get("integrity", {}).get("unchanged"),
        }
    return output


def evaluation_output(result: dict, args) -> dict:
    if args.json:
        return result
    return {"output": str(args.output.resolve()), **{key: result[key] for key in ("run_mode", "execution", "summary", "stable_blockers", "stable_ready", "success", "report_path") if key in result}}


def main(argv: list[str] | None = None) -> int:
    try:
        result, exit_code = handle(parser().parse_args(argv))
        print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        return exit_code
    except (PortError, RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
