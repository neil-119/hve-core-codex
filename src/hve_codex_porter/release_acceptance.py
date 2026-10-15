"""Verify a reviewed historical baseline without relabeling model failures."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .common import PortError, digest, safe_path


def verify_reviewed_acceptance(
    policy_path: Path, repository_root: Path, evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the runtime envelope to a committed, explicitly reviewed policy.

    This is historical behavioral acceptance, not a claim of fresh full-suite
    execution against the release candidate. Candidate host evidence is still
    checked independently by the release gate.
    """
    if policy_path.is_symlink() or not policy_path.is_file():
        raise PortError("Reviewed acceptance policy must be a regular file")
    raw = policy_path.read_bytes()
    if len(raw) > 128 * 1024:
        raise PortError("Reviewed acceptance policy exceeds 128 KiB")
    policy = json.loads(raw)
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise PortError("Unsupported reviewed acceptance policy")
    if (policy.get("decision") != "accept-with-limitations"
            or not isinstance(policy.get("reviewer"), str) or not policy["reviewer"].strip()):
        raise PortError("Behavioral acceptance requires an explicit reviewer decision")
    policy_hash = digest(raw)
    if evidence.get("acceptance_policy_sha256") != policy_hash:
        raise PortError("Runtime evidence must bind the exact reviewed acceptance policy hash")
    baseline = policy.get("historical_baseline")
    if not isinstance(baseline, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(baseline.get("source_sha", ""))):
        raise PortError("Historical baseline requires its immutable upstream source SHA")
    counts = baseline.get("counts", {})
    fields = ("passed", "failed", "blocked", "execution_errors", "unrun", "planned")
    if not isinstance(counts, dict) or any(type(counts.get(key)) is not int or counts[key] < 0 for key in fields):
        raise PortError("Historical baseline counts must be nonnegative integers")
    if sum(counts[key] for key in fields[:-1]) != counts["planned"] or counts["planned"] == 0:
        raise PortError("Historical baseline counts do not reconcile")
    if counts["execution_errors"] or counts["unrun"]:
        raise PortError("Incomplete or execution-error baselines require a new review policy")
    reports = baseline.get("reports")
    if not isinstance(reports, list) or not reports:
        raise PortError("Historical acceptance requires preserved report hashes")
    for item in reports:
        if not isinstance(item, dict):
            raise PortError("Malformed historical report binding")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative.startswith("docs/"):
            raise PortError("Historical report must be a documented repository path")
        report = safe_path(repository_root, relative)
        if not report.is_file() or digest(report.read_text(encoding="utf-8").encode("utf-8")) != item.get("sha256_utf8_lf"):
            raise PortError("Historical acceptance report hash mismatch")
    deferred = policy.get("deferred_host_checks", [])
    if deferred != ["child-read-only-permission"]:
        raise PortError("Only the explicitly deferred child permission check may be excluded")
    if not isinstance(policy.get("limitations"), list) or not policy["limitations"]:
        raise PortError("Accepted limitations must remain explicit")
    return {
        "accepted": True, "mode": "reviewed-historical-baseline",
        "policy_sha256": policy_hash, "historical_source_sha": baseline["source_sha"],
        "counts": counts, "deferred_host_checks": deferred,
        "fresh_candidate_full_suite": False,
    }
