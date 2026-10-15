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

from hve_codex_porter.rules import explain_rules, load_registry, match_rules  # noqa: E402


class RulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.rules_dir = self.root / "rules"
        self.rules_dir.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_registry(self, entries: list[dict], contents: dict[str, str] | None = None) -> None:
        for relative, content in (contents or {}).items():
            target = self.rules_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (self.rules_dir / "index.json").write_text(
            json.dumps({"version": 1, "rules": entries}), encoding="utf-8"
        )

    @staticmethod
    def rule(
        rule_id: str,
        path: str,
        apply_to: list[str],
        precedence: int = 0,
        imports: list[str] | None = None,
    ) -> dict:
        return {
            "id": rule_id,
            "source": f"source/{rule_id}.instructions.md",
            "path": path,
            "apply_to": apply_to,
            "precedence": precedence,
            "imports": imports or [],
        }

    def test_inline_double_star_preserves_required_path_segments(self) -> None:
        from hve_codex_porter.hook_runtime import _fallback_match_rules

        self.write_registry(
            [self.rule("rai", "rai.md", ["**/skills/rai**/**"])],
            {"rai.md": "RAI folder guidance."},
        )
        for resolver in (match_rules, _fallback_match_rules):
            for path in ("skills/rai/demo.md", "repo/skills/rai-planning/references/standards.md"):
                with self.subTest(resolver=resolver.__name__, path=path):
                    self.assertEqual([r["id"] for r in resolver(self.root, [path])], ["rai"])
            for path in ("skills/raisample", "skills/rai-planning-other.md", "skills/other/rai/demo.md"):
                with self.subTest(resolver=resolver.__name__, path=path):
                    self.assertEqual(resolver(self.root, [path]), [])

    def test_inline_double_star_cannot_cross_directory_boundaries(self) -> None:
        from hve_codex_porter.hook_runtime import _fallback_match_rules

        self.write_registry(
            [self.rule("markdown", "markdown.md", ["docs/**.md"])],
            {"markdown.md": "Markdown guidance."},
        )
        for resolver in (match_rules, _fallback_match_rules):
            self.assertEqual([r["id"] for r in resolver(self.root, ["docs/readme.md"])], ["markdown"])
            self.assertEqual(resolver(self.root, ["docs/nested/readme.md"]), [])

    def test_cli_preserves_unicode_rules_on_legacy_windows_text_pipe(self) -> None:
        content = "Use the project formatter. \U0001f916 \u65e5\u672c\u8a9e"
        self.write_registry([self.rule("python", "python.md", ["**/*.py"])], {"python.md": content})
        result = subprocess.run(
            [sys.executable, "-m", "hve_codex_porter.cli", "rules", "--root", str(self.root), "match", "src/app.py"],
            cwd=self.root, env=dict(os.environ, PYTHONPATH=str(SOURCE_ROOT), PYTHONIOENCODING="cp1252"),
            capture_output=True, text=True, encoding="ascii", check=True,
        )
        self.assertIn(content, json.loads(result.stdout)[0]["content"])
        self.assertEqual(result.stderr, "")

    def test_load_registry_accepts_overlay_root_and_registry_directory(self) -> None:
        self.write_registry(
            [self.rule("python", "python.md", ["**/*.py"])],
            {"python.md": "Use the project formatter."},
        )

        from_overlay = load_registry(self.root)
        from_registry = load_registry(self.rules_dir)

        self.assertEqual(from_overlay, from_registry)
        self.assertEqual(from_overlay[0]["content"], "Use the project formatter.")

    def test_on_demand_rules_only_load_through_an_explicit_import(self) -> None:
        manual = self.rule("manual", "manual.md", [])
        manual["auto_attach"] = False
        self.write_registry(
            [manual, self.rule("python", "python.md", ["**/*.py"], imports=["manual"])],
            {"manual.md": "On-demand detail.", "python.md": "Python guidance."},
        )
        self.assertEqual(match_rules(self.root, ["docs/notes.md"]), [])
        self.assertEqual([r["id"] for r in match_rules(self.root, ["src/app.py"])], ["manual", "python"])
        from hve_codex_porter.hook_runtime import _fallback_match_rules
        self.assertEqual(_fallback_match_rules(self.root, ["docs/notes.md"]), [])
        self.assertEqual([r["id"] for r in _fallback_match_rules(self.root, ["src/app.py"])], ["manual", "python"])

    def test_match_supports_recursive_globs_multiple_patterns_and_negation(self) -> None:
        self.write_registry(
            [
                self.rule(
                    "source-rules",
                    "source.md",
                    ["src/**/*.py", "tests/*.py", "!src/generated/**", "src/generated/keep.py"],
                )
            ],
            {"source.md": "Rule body"},
        )

        self.assertEqual([rule["id"] for rule in match_rules(self.root, ["src/app.py"])], ["source-rules"])
        self.assertEqual([rule["id"] for rule in match_rules(self.root, ["src/nested/app.py"])], ["source-rules"])
        self.assertEqual([rule["id"] for rule in match_rules(self.root, ["tests/app.py"])], ["source-rules"])
        self.assertEqual(match_rules(self.root, ["src/generated/skip.py"]), [])
        self.assertEqual([rule["id"] for rule in match_rules(self.root, ["src/generated/keep.py"])], ["source-rules"])
        self.assertEqual(match_rules(self.root, ["docs/readme.md"]), [])

    def test_comma_separated_globs_and_brace_alternatives_match(self) -> None:
        self.write_registry(
            [self.rule("web", "web.md", ["web/{client,server}/**/*.{js,ts}, !**/*.test.ts"])],
            {"web.md": "Keep types consistent."},
        )

        self.assertEqual(len(match_rules(self.root, ["web/client/app.ts"])), 1)
        self.assertEqual(len(match_rules(self.root, ["web/server/api.js"])), 1)
        self.assertEqual(match_rules(self.root, ["web/client/app.test.ts"]), [])

    def test_imports_are_transitive_and_ordered_by_precedence_then_id(self) -> None:
        self.write_registry(
            [
                self.rule(
                    "app",
                    "app.md",
                    ["src/**/*.py"],
                    20,
                    ["source/base.instructions.md", "shared.md"],
                ),
                self.rule("base", "base.md", ["**/*"], 5, ["common"]),
                self.rule("shared", "shared.md", ["elsewhere/**"], 10),
                self.rule("common", "common.md", ["none/**"], 0),
                self.rule("other", "other.md", ["src/**/*.py"], 10),
            ],
            {
                "app.md": "Application rules.",
                "base.md": "Base rules.",
                "shared.md": "Shared rules.",
                "common.md": "Common rules.",
                "other.md": "Other rules.",
            },
        )

        ids = [rule["id"] for rule in match_rules(self.root, ["src/app.py"])]
        self.assertEqual(ids, ["common", "base", "other", "shared", "app"])

        explanation = explain_rules(self.root, "src/app.py")
        self.assertTrue(explanation["matched"])
        self.assertEqual(explanation["path"], "src/app.py")
        self.assertEqual({rule["id"] for rule in explanation["matched_rules"]}, {"app", "other", "base"})
        self.assertEqual({rule["id"] for rule in explanation["imported_rules"]}, {"shared", "common"})

    def test_import_cycle_is_safe_and_unmatched_explain_is_empty(self) -> None:
        self.write_registry(
            [
                self.rule("a", "a.md", ["src/**"], 1, ["b"]),
                self.rule("b", "b.md", ["none/**"], 2, ["a"]),
            ],
            {"a.md": "A", "b.md": "B"},
        )

        self.assertEqual([rule["id"] for rule in match_rules(self.root, ["src/x.c"])], ["a", "b"])
        explanation = explain_rules(self.root, "docs/readme.md")
        self.assertFalse(explanation["matched"])
        self.assertEqual(explanation["rules"], [])

    def test_rejects_unsafe_registry_content_and_user_paths(self) -> None:
        self.write_registry(
            [self.rule("escape", "../outside.md", ["**/*"])],
            {"outside.md": "Should never load."},
        )
        with self.assertRaisesRegex(ValueError, "Unsafe path component"):
            load_registry(self.root)

        self.write_registry(
            [self.rule("safe", "safe.md", ["**/*"])],
            {"safe.md": "Safe."},
        )
        for unsafe in ("../outside.py", "C:\\outside.py", "/outside.py", "src/../../outside.py"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                match_rules(self.root, [unsafe])

    def test_rejects_unknown_import_and_duplicate_ids(self) -> None:
        self.write_registry(
            [self.rule("a", "a.md", ["**/*"], imports=["missing"])],
            {"a.md": "A"},
        )
        with self.assertRaisesRegex(ValueError, "unknown rule reference"):
            load_registry(self.root)

        self.write_registry(
            [
                self.rule("same", "a.md", ["**/*"]),
                self.rule("same", "b.md", ["**/*"]),
            ],
            {"a.md": "A", "b.md": "B"},
        )
        with self.assertRaisesRegex(ValueError, "Duplicate rule id"):
            load_registry(self.root)


if __name__ == "__main__":
    unittest.main()
