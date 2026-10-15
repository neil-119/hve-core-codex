from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ruamel.yaml import YAML


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.cli import main as cli_main, parser  # noqa: E402
from hve_codex_porter.common import PortError  # noqa: E402
from hve_codex_porter.locked_fixture_dependencies import (  # noqa: E402
    _archive_members,
    package_roots_from_references,
    prepare_locked_fixture_dependencies,
    verify_prepared_fixture_dependencies,
)
from hve_codex_porter.vally_bundle import prepare_vally_bundle  # noqa: E402


def _package_tarball(name: str, version: str, files: dict[str, bytes]) -> bytes:
    payload = {"package.json": json.dumps({"name": name, "version": version}, separators=(",", ":")).encode(), **files}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative, raw in sorted(payload.items()):
            info = tarfile.TarInfo(f"package/{relative}")
            info.size = len(raw)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


def _sri(raw: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode("ascii")


class LockedFixtureDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="locked-fixture-deps-test-")
        self.root = Path(self.temporary.name)
        self.upstream = self.root / "upstream"
        self.installed = self.root / "installed"
        self.cache = self.root / "npm-cache"
        self.distribution = self.root / "distribution"
        self.prepared = self.root / "prepared"
        self.upstream.mkdir()
        self.installed.mkdir()
        self.cache.mkdir()
        self.distribution.mkdir()
        (self.upstream / "evals" / "behavior-conformance").mkdir(parents=True)
        (self.distribution / "generated").mkdir()
        (self.distribution / "generated" / "source-to-output.json").write_text("{}\n", encoding="utf-8")

        package_specs = {
            "@modelcontextprotocol/server": (
                "node_modules/@modelcontextprotocol/server",
                "2.3.0",
                {"@modelcontextprotocol/core": "2.3.0", "zod": "^4.2.0"},
                {"index.js": b"export const server = true;\n"},
            ),
            "@modelcontextprotocol/core": (
                "node_modules/@modelcontextprotocol/core",
                "2.3.0",
                {"zod": "^4.2.0"},
                {"index.js": b"export const core = true;\n"},
            ),
            "zod": (
                "node_modules/zod",
                "4.6.5",
                {},
                {"index.js": b"export const zod = true;\n"},
            ),
            "@microsoft/vally-cli": (
                "node_modules/@microsoft/vally-cli",
                "0.17.0",
                {"@microsoft/vally-core": "0.17.0"},
                {"bin/vally.js": b"export const cli = true;\n"},
            ),
            "@microsoft/vally-core": (
                "node_modules/@microsoft/vally-core",
                "0.17.0",
                {"commander": "15.0.0"},
                {"index.js": b"export const core = true;\n"},
            ),
            "commander": (
                "node_modules/commander",
                "15.0.0",
                {},
                {"index.js": b"export const commander = true;\n"},
            ),
        }
        entries: dict[str, dict] = {
            "": {"name": "fixture-upstream", "version": "1.0.0"},
        }
        for name, (lock_path, version, dependencies, files) in package_specs.items():
            raw_tarball = _package_tarball(name, version, files)
            integrity = _sri(raw_tarball)
            entries[lock_path] = {
                "version": version,
                "resolved": f"https://registry.npmjs.org/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz",
                "integrity": integrity,
                **({"dependencies": dependencies} if dependencies else {}),
            }
            algorithm, encoded = integrity.split("-", 1)
            digest_bytes = base64.b64decode(encoded)
            blob = self.cache / "_cacache" / "content-v2" / algorithm / digest_bytes.hex()[:2] / digest_bytes.hex()[2:4] / digest_bytes.hex()[4:]
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_bytes(raw_tarball)
            package_root = self.installed / lock_path
            package_root.mkdir(parents=True)
            (package_root / "package.json").write_bytes(
                json.dumps({"name": name, "version": version}, separators=(",", ":")).encode()
            )
            for relative, raw in files.items():
                target = package_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)

        root_dependencies = {
            "@microsoft/vally-cli": "0.17.0",
            "@modelcontextprotocol/server": "2.3.0",
        }
        entries[""]["devDependencies"] = root_dependencies
        package_bytes = json.dumps(
            {"name": "fixture-upstream", "version": "1.0.0", "devDependencies": root_dependencies},
            separators=(",", ":"),
        ).encode()
        lock_bytes = json.dumps({"name": "fixture-upstream", "version": "1.0.0", "lockfileVersion": 3, "packages": entries}, separators=(",", ":")).encode()
        (self.upstream / "package.json").write_bytes(package_bytes)
        (self.upstream / "package-lock.json").write_bytes(lock_bytes)
        (self.installed / "package.json").write_bytes(package_bytes)
        (self.installed / "package-lock.json").write_bytes(lock_bytes)

        self.eval_path = self.upstream / "evals" / "behavior-conformance" / "skill-behavior.eval.yaml"
        self.eval_bytes = (
            "name: fixture-behavior\n"
            "executor: copilot-sdk\n"
            "environment:\n"
            "  files:\n"
            "    - src: ../../node_modules/@modelcontextprotocol/server\n"
            "      dest: node_modules/@modelcontextprotocol/server\n"
            "    - src: ../../node_modules/@modelcontextprotocol/core\n"
            "      dest: node_modules/@modelcontextprotocol/core\n"
            "    - src: ../../node_modules/zod\n"
            "      dest: node_modules/zod\n"
            "stimuli:\n"
            "  - name: fixture-case\n"
            "    prompt: Preserve the original MCP fixture behavior.\n"
            "    graders:\n"
            "      - type: output-matches\n"
            "        pattern: exact-tool-result\n"
        ).encode()
        self.eval_path.write_bytes(self.eval_bytes)
        (self.upstream / ".vally.yaml").write_bytes(b"paths:\n  evals: evals\n  results: results\n")

        subprocess.run(["git", "init", "-q", str(self.upstream)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.upstream), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.upstream), "config", "user.name", "Fixture Test"], check=True)
        subprocess.run(["git", "-C", str(self.upstream), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.upstream), "commit", "-q", "-m", "fixture"], check=True, capture_output=True)
        self.upstream_sha = subprocess.check_output(["git", "-C", str(self.upstream), "rev-parse", "HEAD"], text=True).strip()

    def tearDown(self) -> None:
        # Windows occasionally retains a transient sharing lock on this test's
        # temporary directory. Retry only that error; cleanup must still finish.
        deadline = time.monotonic() + 2.0
        while True:
            try:
                self.temporary.cleanup()
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) != 32 or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def test_dependency_closure_uses_locked_tarballs_and_bundle_explicit_specs(self) -> None:
        receipt = prepare_locked_fixture_dependencies(
            self.upstream, self.upstream_sha, self.installed, self.cache, self.prepared
        )
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["summary"]["package_count"], 3)
        self.assertFalse(receipt["summary"]["scripts_executed"])
        verified, _ = verify_prepared_fixture_dependencies(self.upstream, self.prepared)
        self.assertEqual(
            [item["lock_path"] for item in verified["packages"]],
            ["node_modules/zod", "node_modules/@modelcontextprotocol/core", "node_modules/@modelcontextprotocol/server"],
        )

        output = self.root / "bundle"
        report = prepare_vally_bundle(
            self.upstream,
            self.distribution,
            output,
            fixture_dependencies=self.prepared,
            eval_filename_patterns=["skill-behavior.eval.yaml"],
        )
        self.assertTrue(report["success"])
        converted = YAML(typ="safe").load(
            (output / "evals/behavior-conformance/skill-behavior.eval.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(converted["executor"], "codex")
        self.assertEqual(converted["environment"]["files"][0]["src"], "node_modules/@modelcontextprotocol/server")
        self.assertEqual(converted["stimuli"][0]["prompt"], "Preserve the original MCP fixture behavior.")
        self.assertEqual(converted["stimuli"][0]["graders"][0]["pattern"], "exact-tool-result")
        self.assertEqual((output / "upstream/.vally.yaml").read_bytes(), (self.upstream / ".vally.yaml").read_bytes())
        self.assertEqual(
            (output / "evals/behavior-conformance/node_modules/@modelcontextprotocol/server/index.js").read_bytes(),
            b"export const server = true;\n",
        )
        self.assertEqual(
            (output / "evals/behavior-conformance/node_modules/@modelcontextprotocol/core/index.js").read_bytes(),
            b"export const core = true;\n",
        )
        bundle_receipt = json.loads((output / "generated/vally-fixture-dependencies.json").read_text(encoding="utf-8"))
        self.assertEqual(bundle_receipt["source"]["upstream_sha"], self.upstream_sha)
        self.assertEqual(bundle_receipt["summary"]["package_count"], 3)
        source_map = json.loads((output / "generated/vally-source-to-output.json").read_text(encoding="utf-8"))
        server_outputs = source_map["node_modules/@modelcontextprotocol/server"]["outputs"]
        self.assertTrue(any(item["status"].endswith("-directory") for item in server_outputs))
        self.assertTrue(any(
            item["path"].endswith("/node_modules/@modelcontextprotocol/server/index.js")
            and len(item.get("sha256", "")) == 64
            for mapping in source_map.values()
            for item in mapping["outputs"]
        ))
        self.assertTrue(any(
            item["status"] == "fixture-dependency-sri-proof"
            for mapping in source_map.values()
            for item in mapping["outputs"]
        ))

    def test_default_selection_stays_unchanged_and_cli_override_is_repeatable(self) -> None:
        output = self.root / "default-bundle"
        report = prepare_vally_bundle(self.upstream, self.distribution, output)
        self.assertTrue(report["success"])
        self.assertEqual(report["summary"]["eval_specs_converted"], 0)
        self.assertEqual((output / "evals/behavior-conformance/skill-behavior.eval.yaml").read_bytes(), self.eval_bytes)
        config = YAML(typ="safe").load((output / ".vally.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["paths"]["evalFilenames"], ["eval.yaml", "eval.yml"])
        parsed = parser().parse_args([
            "eval", "vally-prepare", "--upstream-dir", "upstream", "--output", "bundle",
            "--eval-filename", "eval.yaml", "--eval-filename", "skill-behavior.eval.yaml",
        ])
        self.assertEqual(parsed.eval_filename_patterns, ["eval.yaml", "skill-behavior.eval.yaml"])

    def test_offline_dependency_prepare_cli_emits_verified_receipt(self) -> None:
        output = self.root / "cli-prepared"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main([
                "eval", "vally-fixture-deps-prepare",
                "--upstream-dir", str(self.upstream),
                "--expected-upstream-sha", self.upstream_sha,
                "--installed-root", str(self.installed),
                "--npm-cache", str(self.cache),
                "--output", str(output),
            ])
        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["success"])
        self.assertEqual(result["source"]["upstream_sha"], self.upstream_sha)
        self.assertTrue((output / "fixture-dependencies.json").is_file())

    def test_compare_cli_opt_in_adds_only_its_locked_dependency_closure(self) -> None:
        default_output = self.root / "default-cli-prepared"
        default = prepare_locked_fixture_dependencies(
            self.upstream, self.upstream_sha, self.installed, self.cache, default_output
        )
        self.assertEqual(default["summary"]["package_count"], 3)
        default_receipt = json.loads((default_output / "fixture-dependencies.json").read_text(encoding="utf-8"))
        self.assertEqual(default_receipt["schema_version"], 1)
        self.assertNotIn("extra_roots", default_receipt)

        output = self.root / "compare-cli-prepared"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main([
                "eval", "vally-fixture-deps-prepare",
                "--upstream-dir", str(self.upstream),
                "--expected-upstream-sha", self.upstream_sha,
                "--installed-root", str(self.installed),
                "--npm-cache", str(self.cache),
                "--output", str(output),
                "--include-vally-cli",
            ])
        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["package_count"], 6)
        self.assertFalse(result["summary"]["scripts_executed"])
        self.assertFalse(result["summary"]["network_accessed"])

        receipt_path = output / "fixture-dependencies.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema_version"], 2)
        self.assertEqual(receipt["extra_roots"], ["node_modules/@microsoft/vally-cli"])
        self.assertEqual(receipt["selected_optional_roots"], [])
        verified, _ = verify_prepared_fixture_dependencies(self.upstream, output)
        paths = {item["lock_path"] for item in verified["packages"]}
        self.assertTrue({
            "node_modules/@microsoft/vally-cli",
            "node_modules/@microsoft/vally-core",
            "node_modules/commander",
        }.issubset(paths))
        self.assertEqual(
            (output / "node_modules/@microsoft/vally-cli/bin/vally.js").read_bytes(),
            b"export const cli = true;\n",
        )

        bundle_output = self.root / "compare-bundle"
        bundled = prepare_vally_bundle(
            self.upstream,
            self.distribution,
            bundle_output,
            fixture_dependencies=output,
            eval_filename_patterns=["skill-behavior.eval.yaml"],
        )
        self.assertTrue(bundled["success"])
        bundle_receipt = json.loads(
            (bundle_output / "generated/vally-fixture-dependencies.json").read_text(encoding="utf-8")
        )
        self.assertEqual(bundle_receipt["extra_roots"], ["node_modules/@microsoft/vally-cli"])
        self.assertEqual(bundle_receipt["selected_optional_roots"], [])

        receipt["extra_roots"] = []
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(PortError, "does not describe the pinned Vally CLI closure exactly"):
            verify_prepared_fixture_dependencies(self.upstream, output)

    def test_invalid_override_fails_closed(self) -> None:
        with self.assertRaisesRegex(PortError, "safe basename patterns"):
            prepare_vally_bundle(
                self.upstream,
                self.distribution,
                self.root / "invalid-pattern-bundle",
                eval_filename_patterns=["../eval.yaml"],
            )

    def test_installed_extra_file_and_tampered_proof_are_rejected(self) -> None:
        prepare_locked_fixture_dependencies(self.upstream, self.upstream_sha, self.installed, self.cache, self.prepared)
        extra = self.installed / "node_modules/@modelcontextprotocol/server/extra.js"
        extra.write_text("not in the published package\n", encoding="utf-8")
        with self.assertRaisesRegex(PortError, "differs from its lock-SRI tarball"):
            prepare_locked_fixture_dependencies(
                self.upstream,
                self.upstream_sha,
                self.installed,
                self.cache,
                self.root / "extra-output",
            )
        extra.unlink()
        package_file = self.prepared / "node_modules/@modelcontextprotocol/server/index.js"
        package_file.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(PortError, "differs from its lock-SRI tarball"):
            verify_prepared_fixture_dependencies(self.upstream, self.prepared)

    def test_worktree_manifest_change_is_rejected_even_with_same_head(self) -> None:
        altered = self.upstream / "package-lock.json"
        altered.write_bytes(altered.read_bytes() + b"\n")
        with self.assertRaisesRegex(PortError, "differs from its pinned Git blob"):
            prepare_locked_fixture_dependencies(
                self.upstream,
                self.upstream_sha,
                self.installed,
                self.cache,
                self.prepared,
            )
        self.assertFalse(self.prepared.exists())

    def test_reference_roots_recognize_scoped_and_nested_package_paths(self) -> None:
        self.assertEqual(
            package_roots_from_references([
                "node_modules/@modelcontextprotocol/server",
                "node_modules/foo/node_modules/bar/lib/index.js",
                "evals/case/fixture.json",
            ]),
            ["node_modules/@modelcontextprotocol/server", "node_modules/foo/node_modules/bar"],
        )

    def test_tarball_member_paths_normalize_identical_dot_aliases_and_reject_escape(self) -> None:
        stream = io.BytesIO()
        payloads = [
            ("debug/./package.json", b'{"name":"@types/debug","version":"1.0.0"}'),
            ("debug/index.d.ts", b"export {};\n"),
            ("debug/./index.d.ts", b"export {};\n"),
        ]
        with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for name, raw in payloads:
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                info.mtime = 0
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(raw))
        self.assertEqual(
            _archive_members(stream.getvalue(), "test package"),
            [("package.json", payloads[0][1]), ("index.d.ts", payloads[1][1])],
        )

        escaped = io.BytesIO()
        with tarfile.open(fileobj=escaped, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo("debug/../escape.js")
            info.size = 1
            info.mtime = 0
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(PortError, "escapes its package root"):
            _archive_members(escaped.getvalue(), "malicious package")


if __name__ == "__main__":
    unittest.main()
