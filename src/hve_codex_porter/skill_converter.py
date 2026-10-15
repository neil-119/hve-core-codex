"""Preserve skill metadata and resources while emitting Agent Skills frontmatter."""
from __future__ import annotations
from io import StringIO
from ruamel.yaml import YAML


def python_resource_guidance() -> str:
    """Bridge installed utility locations to the consumer project's paths."""
    return (
        "\n\n## Codex packaged Python utilities\n\n"
        "Use the documented entry point from the complete installed skill package, "
        "located relative to this loaded SKILL.md, so sibling helpers remain available. "
        "Keep the authorized project workspace as the working directory where supported; "
        "the utility's installation directory is not the project's output root. "
        "Before changing directories for a documented module invocation, resolve project "
        "input and output paths against the authorized workspace. Pass canonical absolute "
        "file paths without `..` segments. Respect the utility's permitted roots and "
        "all source safety gates; a path error is a blocker, not a passing scan. "
        "Use `python -B` for validation utilities to avoid writing bytecode into installed "
        "resources. Keep scratch files inside the authorized project and remove temporary "
        "files before completion when only final artifacts are authorized.\n"
    )


def render_skill(name: str, description: str, body: str, metadata: dict | None = None) -> str:
    fields = {"name": name, "description": description}
    for key in ("license", "compatibility", "metadata", "allowed-tools"):
        if metadata and key in metadata:
            # Source host tool tokens are not valid Codex allowlists.
            if key != "allowed-tools":
                fields[key] = metadata[key]
    stream = StringIO()
    YAML().dump(fields, stream)
    hint = metadata.get("argument-hint") if metadata else None
    if hint:
        body = body.rstrip("\n") + f"\n\n## Invocation arguments\n\n{hint}\n"
    return "---\n" + stream.getvalue() + "---\n\n" + body.lstrip("\n")
