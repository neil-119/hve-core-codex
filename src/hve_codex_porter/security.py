"""Conservative credential detection; diagnostics never include matched values."""
from __future__ import annotations
import re
from pathlib import Path
from ruamel.yaml import YAML
from .common import PortError, digest

PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,})\b"),
    "stripe-live-key": re.compile(rb"\bsk_live_[A-Za-z0-9]{24,}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[0-9]{10,}-[A-Za-z0-9-]{20,}\b"),
}


def load_reviewed_fixtures() -> dict[str, set[str]]:
    package = Path(__file__).parent
    repo = package.parents[1]
    mappings = repo / "mappings" if (repo / "pyproject.toml").is_file() else package / "data/mappings"
    path = mappings / "security-fixtures.yaml"
    if not path.is_file():
        return {}
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    reviewed = {}
    for item in data["fixtures"]:
        file_hash = item.get("sha256", "")
        kinds = item.get("patterns", [])
        if not re.fullmatch(r"[0-9a-f]{64}", file_hash) or not isinstance(kinds, list) or any(kind not in PATTERNS for kind in kinds):
            raise PortError("Malformed reviewed synthetic credential fixture policy")
        reviewed[file_hash] = set(kinds)
    return reviewed


def credential_findings(root: Path, files: list[Path]) -> list[str]:
    findings = []
    reviewed = load_reviewed_fixtures()
    for path in files:
        data = path.read_bytes()
        allowed = reviewed.get(digest(data), set())
        for kind, pattern in PATTERNS.items():
            if kind not in allowed and pattern.search(data):
                findings.append(f"Potential credential ({kind}) in {path.relative_to(root).as_posix()}; inspect and remove before distribution")
    return findings
