"""Independently verify portable Vally captures against a release candidate.

An envelope only locates evidence. Its claimed verdict is never used as proof.
The caller selects approved adapter code from its protected checkout.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .common import PortError, digest, safe_path

_PREFIX = "generated/runtime-evidence/"
_MAX_RECEIPT_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 64 * 1024 * 1024


def _regular_root(path: Path, label: str) -> Path:
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise PortError(f"{label} must not use symlinks")
    if not absolute.is_dir():
        raise PortError(f"{label} must be an existing directory")
    return absolute.resolve()


def _capture_directory(candidate: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        raise PortError(f"{label} must be beneath {_PREFIX}")
    target = safe_path(candidate, value)
    return _regular_root(target, label)


def _file_hash(root: Path, relative: str) -> str:
    path = safe_path(root, relative)
    if not path.is_file() or path.stat().st_size > _MAX_SOURCE_BYTES:
        raise PortError(f"Missing or oversized source evidence: {relative}")
    return digest(path.read_bytes())


def verify_candidate_vally(
    *,
    candidate_root: Path,
    approved_adapter_root: Path,
    evidence: Mapping[str, Any],
    expected_source_sha: str,
    node_command: str = "node",
    expected_custom_root: Path | None = None,
) -> dict[str, Any]:
    """Verify actual files, with fail-closed receipts and no model execution.

    ``evidence.vally.bundle_path`` and ``run_path`` are portable paths relative
    to the candidate. The approved code path is deliberately not in evidence.
    """
    try:
        if not isinstance(expected_source_sha, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", expected_source_sha
        ):
            raise PortError("Vally acceptance requires an exact upstream commit SHA")
        candidate = _regular_root(candidate_root, "Release candidate")
        adapter = _regular_root(approved_adapter_root, "Approved Vally adapter")
        launcher = safe_path(adapter, "verify.mjs")
        if not launcher.is_file():
            raise PortError("Approved Vally adapter is missing verify.mjs")
        detail = evidence.get("vally")
        if not isinstance(detail, dict):
            raise PortError("Runtime evidence must locate the Vally bundle and run")
        bundle = _capture_directory(candidate, detail.get("bundle_path"), "Vally bundle")
        run = _capture_directory(candidate, detail.get("run_path"), "Vally run")
        if bundle == run or bundle.is_relative_to(run) or run.is_relative_to(bundle):
            raise PortError("Vally bundle and run must be separate capture directories")
        source_hashes = {}
        for relative in ("generated/source-to-output.json", "generated/source-inventory.json"):
            candidate_hash = _file_hash(candidate, relative)
            if _file_hash(bundle, relative) != candidate_hash:
                raise PortError(f"Vally capture source does not match the release candidate: {relative}")
            source_hashes[relative] = candidate_hash
        if expected_custom_root is not None:
            custom = _regular_root(expected_custom_root, "Approved custom Vally sources")
            expected_files = []
            for path in sorted(custom.rglob("*")):
                if path.is_symlink():
                    raise PortError("Approved custom Vally sources contain a symlink")
                if path.is_file():
                    relative = path.relative_to(custom).as_posix()
                    expected_hash = _file_hash(custom, relative)
                    if _file_hash(bundle, "custom/" + relative) != expected_hash:
                        raise PortError(f"Custom Vally original does not match approved source: {relative}")
                    expected_files.append({"path": relative, "sha256": expected_hash})
            if not expected_files:
                raise PortError("Approved custom Vally sources are empty")
            report_path = "generated/vally-bundle-report.json"
            _file_hash(bundle, report_path)
            report = json.loads(safe_path(bundle, report_path).read_bytes())
            source = report.get("source") if isinstance(report, dict) else None
            custom_source = source.get("custom") if isinstance(source, dict) else None
            actual_files = custom_source.get("files") if isinstance(custom_source, dict) else None
            if actual_files != expected_files:
                raise PortError("Vally capture does not include the exact approved custom source set")
        command = [
            node_command, str(launcher), "--bundle", str(bundle), "--run", str(run),
            "--expected-upstream-sha", expected_source_sha.lower(),
            "--approved-adapter-root", str(adapter),
        ]
        # Spool potentially untrusted process output instead of accumulating it
        # in memory. All receipt bytes remain bounded before parsing.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=120, check=False)
            stdout.seek(0)
            raw = stdout.read(_MAX_RECEIPT_BYTES + 1)
        if len(raw) > _MAX_RECEIPT_BYTES:
            raise PortError("Vally verifier returned an oversized receipt")
        receipt = json.loads(raw)
        if not isinstance(receipt, dict) or receipt.get("mode") != "vally-evidence-verification":
            raise PortError("Vally verifier returned an invalid receipt")
        verified = (
            completed.returncode == 0
            and receipt.get("verified") is True
            and receipt.get("fullSuiteExecution") is True
        )
        return {
            "mode": "candidate-vally-verification", "verified": verified,
            "source_sha": expected_source_sha.lower(), "source_hashes": source_hashes,
            "receipt_sha256": digest(raw), "receipt": receipt,
            "blockers": [] if verified else ["Independent Vally full-suite verification did not pass"],
        }
    except (PortError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {
            "mode": "candidate-vally-verification", "verified": False,
            "blockers": [str(error)],
        }
