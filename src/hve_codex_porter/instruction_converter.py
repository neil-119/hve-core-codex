"""Indexed path-scoped rule preservation."""
from __future__ import annotations


def _split_apply_to(value: str) -> list[str]:
    """Split comma-separated globs without breaking brace alternatives."""
    patterns: list[str] = []
    depth = 0
    start = 0
    escaped = False
    for index, char in enumerate(value):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if not escaped:
            if char == "{":
                depth += 1
            elif char == "}" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                pattern = value[start:index].strip()
                if pattern:
                    patterns.append(pattern)
                start = index + 1
        escaped = False
    pattern = value[start:].strip()
    if pattern:
        patterns.append(pattern)
    return patterns


def rule_record(artifact: dict, output_path: str, artifact_by_source: dict) -> dict:
    patterns = artifact["frontmatter"].get("applyTo", [])
    if isinstance(patterns, str):
        patterns = _split_apply_to(patterns)
    imports = [
        artifact_by_source[reference["target"]]["id"]
        for reference in artifact["references"]
        if reference["target"] in artifact_by_source
        and artifact_by_source[reference["target"]]["kind"] == "instruction"
    ]
    return {
        "id": artifact["id"],
        "source": artifact["source"],
        "path": output_path,
        "apply_to": patterns,
        "auto_attach": bool(patterns) or artifact["source"] == ".github/copilot-instructions.md",
        "precedence": 0,
        "imports": sorted(set(imports)),
    }
