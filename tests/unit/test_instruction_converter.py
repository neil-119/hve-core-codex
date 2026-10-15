from __future__ import annotations

import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from hve_codex_porter.instruction_converter import rule_record  # noqa: E402


def test_rule_record_splits_top_level_commas_but_preserves_brace_alternatives() -> None:
    artifact = {
        "id": "scoped-rule",
        "source": ".github/instructions/scoped.instructions.md",
        "frontmatter": {
            "applyTo": "src/**/*.{py,ts}, docs/{product,engineering}/**/*.md, !src/generated/**"
        },
        "references": [],
    }

    record = rule_record(artifact, "scoped.md", {})

    assert record["apply_to"] == [
        "src/**/*.{py,ts}",
        "docs/{product,engineering}/**/*.md",
        "!src/generated/**",
    ]


def test_rule_record_preserves_nested_braces_and_list_frontmatter() -> None:
    artifact = {
        "id": "nested",
        "source": "nested.instructions.md",
        "frontmatter": {"applyTo": ["src/**/*.{py,{tsx,jsx}}", "tests/**/*.py"]},
        "references": [],
    }

    record = rule_record(artifact, "nested.md", {})

    assert record["apply_to"] == ["src/**/*.{py,{tsx,jsx}}", "tests/**/*.py"]
