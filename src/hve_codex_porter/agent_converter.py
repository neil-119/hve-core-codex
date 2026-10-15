"""Role-aware custom agent and explicit entry-point conversion."""
from __future__ import annotations
import tomlkit
from .common import slug

def delegation_guidance(artifact: dict, names: dict[str, str], adapt) -> tuple[str, list[dict]]:
    meta = artifact["frontmatter"]
    lines = ["\n## Codex routing policy\n", "Preserve task identity, state paths, source review gates, and user decisions. Do not auto-check human-review checkboxes. Ordinary workflow progress never grants permission for external writes.\n"]
    lines.append("Record explicit human approval as a dated, user-attributed decision or event with its exact scope. Approval to advance a phase does not itself request editing a human-owned review, approval, or signature checkbox; leave those boxes unchecked. If the user specifically requests editing such a box, preserve its meaning and require evidence that the review or attestation it records actually occurred. Agent-owned progress and artifact self-check items are separate and may be marked when supported by evidence.\n")
    lines.append("For native custom-role delegation, select the installed role using the host's role-selection argument (for example `agent_type` when exposed), then await its result. A task name or nickname alone does not select a custom role. If the host exposes only generic subagents, report native role selection as unavailable; reading a role procedure in a generic child is a manual instruction fallback, not proof that its custom profile or sandbox was loaded. Preserve the role's write boundary and do not report native-role acceptance from that fallback.\n")
    findings = []
    if artifact["disable_model_invocation"]:
        lines.append("This workflow requires explicit user invocation. Do not launch it because another workflow is adjacent.\n")
    if not artifact["user_invocable"]:
        lines.append("Delegated role only: execute when explicitly requested by its parent orchestrator.\n")
    children = meta.get("agents", []) or []
    if isinstance(children, str):
        children = [children]
    for child in children:
        resolved_child = names.get(child, names.get(slug(child)))
        if child in {"*", "agent"}:
            lines.append("Select a cataloged delegated role appropriate to the task; do not broaden its write boundary.\n")
        elif resolved_child:
            lines.append(f"Delegate **{child}** to `{resolved_child}` with spawn, wait, and follow-up. Parallel work must have disjoint file ownership.\n")
        else:
            findings.append({"source": artifact["source"], "code": "unresolved-child-agent", "target": child})
    for action in meta.get("handoffs", []) or []:
        target = action.get("agent", "")
        resolved_target = names.get(target, names.get(slug(target)))
        label = action.get("label", "Continue")
        prompt = adapt(str(action.get("prompt", "")))
        lines.append(f"\n### Action: {label}\n\nWhen the user explicitly selects this action, preserve its arguments and state pointers and route to `{resolved_target or target}` with:\n\n{prompt}\n\nWait for any source-required approval before continuation. The source `send` flag is preserved as routing metadata; it does not provide a Codex UI button or authorize execution.\n")
        if target and not resolved_target and target not in {"agent", "ask", "edit"}:
            findings.append({"source": artifact["source"], "code": "unresolved-handoff-agent", "target": target})
        unknown = set(action) - {"label", "agent", "prompt", "send", "showContinueOn"}
        if unknown:
            findings.append({"source": artifact["source"], "code": "unknown-handoff-field", "fields": sorted(unknown)})
    return "".join(lines), findings

def source_tool_policy(capabilities: list[dict] | None) -> str:
    if capabilities is None:
        return ""
    lines = ["\n## Source tool scope\n", "The source role explicitly limits its tools. Use host equivalents only for the listed source operations and preserve their read/write, connector, delegation, and approval boundaries. A tool exposed by the host does not authorize an additional operation. If an equivalent cannot preserve the boundary, stop that operation and report the missing capability. This instruction policy does not establish native per-tool enforcement.\n"]
    if not capabilities:
        lines.append("The source tool allowlist is empty. Do not invoke tools for this role.\n")
    for item in capabilities:
        lines.append(f"- Source operation `{item['tool']}`: {item['instruction']}\n")
    return "".join(lines)

def render_agent(name: str, description: str, instructions: str, read_only: bool, source_tools: list[dict] | None = None) -> str:
    doc = tomlkit.document()
    doc["name"] = name
    doc["description"] = description
    if read_only:
        doc["sandbox_mode"] = "read-only"
    doc["developer_instructions"] = instructions + source_tool_policy(source_tools)
    return tomlkit.dumps(doc)
