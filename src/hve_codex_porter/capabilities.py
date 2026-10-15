"""Table-driven capability adapters, never credentials or implicit privilege grants."""
from __future__ import annotations
import fnmatch
import re
from pathlib import Path
from ruamel.yaml import YAML
from .references import operative_lines


TOOL_REFERENCE_REPLACEMENTS = {
    "agent/runSubagent": "native named-agent delegation",
    "runSubagent": "native named-agent delegation",
    "vscode/askQuestions": "the host question interface",
    "vscode_askQuestions": "the host question interface",
}
LEGACY_TOOL_NAMES = {
    "read_file",
    "create_file",
    "replace_string_in_file",
    "semantic_search",
    "grep_search",
    "file_search",
    "list_dir",
}
EXAMPLE_LINE = re.compile(r"^\s*(?:[-*+]\s*)?(?:example(?:\s+[\w-]+)?\s*[:.-]|for example\b|e\.g\.?\s)", re.IGNORECASE)
ACTION_VERB = re.compile(r"\b(?:use|using|call|calling|invoke|invoking|dispatch|dispatching|run|running|issue|issuing|launch|launching|select|selecting|execute|executing|submit|submitting|activate|activating)\b", re.IGNORECASE)

def load_adapters(path: Path) -> list[dict]:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))["adapters"]

def classify(tool: str, adapters: list[dict]) -> dict:
    # Specific mappings take precedence over broad capability families.
    matches = [a for a in adapters if fnmatch.fnmatchcase(tool, a["pattern"])]
    if not matches:
        return {"tool": tool, "classification": "unsupported", "capability": tool, "availability": "unavailable", "instruction": f"Stop: capability {tool!r} has no reviewed adapter."}
    best = max(matches, key=lambda a: len(a["pattern"].replace("*", "")))
    return {"tool": tool, **best}

def tools_in_artifact(artifact: dict, body: str) -> list[str]:
    declared = artifact["required_tools"] or []
    if isinstance(declared, str):
        declared = declared.split()
    result = set(declared)
    lines = body.splitlines()
    body = "\n".join(lines[index] for index in sorted(operative_lines(body)))
    # Detect explicit tool namespaces in instructions in addition to frontmatter.
    for match in re.finditer(r"`((?:read|search|edit|execute|agent|web|github|ado|jira|microsoft-docs|vscode)/[A-Za-z0-9_/*.-]+|vscode_askQuestions|agent/runSubagent|work-iq-mcp)`", body):
        result.add(match.group(1))
    for match in re.finditer(r"`(mcp_[a-zA-Z0-9_]+|runSubagent|replace_string_in_file|read_file|create_file|file_search|grep_search|semantic_search)`", body):
        result.add(match.group(1))
    return sorted(result)


def _actionable_tool_reference(line: str, tool: str, start: int, end: int) -> bool:
    """Recognize tool references used by an instruction, not descriptive docs."""
    if EXAMPLE_LINE.match(line):
        return False
    before = re.split(r"[.!?;]\s*", line[:start])[-1]
    after = re.split(r"[.!?;]\s*", line[end:], maxsplit=1)[0]
    # Native-adapter and invocation contexts used by source workflows.
    if re.search(r"\b(?:via|with|using)\s*$", before, re.IGNORECASE):
        return True
    if ACTION_VERB.search(before[-180:]):
        return True
    # Availability checks are executable gates even though they contain no verb
    # before the source tool name (for example, "When neither `runSubagent` ...").
    if re.search(r"\bwhen\s+(?:neither|a|an|the)?\s*$", before, re.IGNORECASE) and re.search(r"\bavailable\b", after, re.IGNORECASE):
        return True
    if re.search(r"\bneither\s*$", before, re.IGNORECASE) and re.search(r"\bavailable\b", after, re.IGNORECASE):
        return True
    return False


def rewrite_tool_tokens(text: str, capabilities: list[dict]) -> str:
    """Rewrite actionable source-tool references while keeping examples intact.

    Only exact backtick-wrapped tool names are candidates. Fenced code and HTML
    blocks are excluded by operative_lines; explicit example lines and
    descriptive mentions are left untouched.
    """
    replacements = {}
    for item in capabilities:
        tool = item["tool"]
        replacement = TOOL_REFERENCE_REPLACEMENTS.get(tool)
        if replacement is None and ("/" in tool or tool in LEGACY_TOOL_NAMES):
            replacement = item["instruction"]
        if replacement:
            replacements[tool] = replacement
    if not replacements:
        return text

    lines = text.splitlines(keepends=True)
    for index in operative_lines(text):
        line = lines[index]
        for tool, replacement in replacements.items():
            pattern = re.compile(r"`" + re.escape(tool) + r"`")
            matches = list(pattern.finditer(line))
            for match in reversed(matches):
                if _actionable_tool_reference(line, tool, match.start(), match.end()):
                    line = line[:match.start()] + replacement + line[match.end():]
        lines[index] = line
    return "".join(lines)

def guidance(capabilities: list[dict]) -> str:
    if not capabilities:
        return ""
    lines = ["\n## Codex capability prerequisites\n", "Check each prerequisite before executing this workflow. Missing integrations are blockers for the affected operation; report setup steps without fake results or a substitute mutation. Preserve the source approval gates.\n"]
    for item in capabilities:
        lines.append(f"- **{item['capability']}** ({item['availability']}): {item['instruction']}\n")
    return "".join(lines)
