"""Parameterized prompt contracts without invented host UI."""
from __future__ import annotations
import re
from .common import slug

def prompt_guidance(artifact: dict, body: str, agent_names: dict[str, str]) -> tuple[str, list[dict]]:
    variables = sorted(set(re.findall(r"\$\{([^}]+)\}", body)))
    lines = ["\n## Invocation arguments\n", "Accept the user's arguments as supplied. Preserve defaults and variables described below. Ask for missing required inputs in chat and wait; never invent an input value.\n"]
    if artifact["frontmatter"].get("argument-hint"):
        lines.append(str(artifact["frontmatter"]["argument-hint"]) + "\n")
    if variables:
        lines.append("Source variables: " + ", ".join("`" + v + "`" for v in variables) + ".\n")
    findings = []
    target = artifact["frontmatter"].get("agent", artifact["frontmatter"].get("mode"))
    resolved_target = agent_names.get(target, agent_names.get(slug(target))) if target else None
    if resolved_target:
        lines.append(f"Run this workflow in custom role `{resolved_target}`; pass the original arguments, approvals, and state pointers.\n")
    elif target and target not in {"agent", "ask", "edit"}:
        findings.append({"source": artifact["source"], "code": "unresolved-prompt-agent", "target": target})
    return "".join(lines), findings
