"""Build release metadata for an unsigned, source-pinned edge snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


FULL_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REVISION_RE = re.compile(r"[1-9][0-9]*\Z")
WHEEL_RE = re.compile(r"hve_core_codex-[A-Za-z0-9_.]+-py3-none-any\.whl\Z")
VALIDATE_WORKFLOW = ".github/workflows/validate.yml"


class SnapshotError(ValueError):
    """Raised when an edge snapshot is not safe to publish."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"Could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"{label} must be a JSON object.")
    return value


def successful_validation_run(payload: dict[str, Any], source_commit: str) -> dict[str, Any]:
    """Select an exact-source successful run of the repository validation workflow."""
    if not FULL_SHA_RE.fullmatch(source_commit):
        raise SnapshotError("The port source commit must be a full lowercase Git SHA.")
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise SnapshotError("GitHub did not return a workflow_runs list.")
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (
            run.get("path") == VALIDATE_WORKFLOW
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("head_sha") == source_commit
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("id"), int)
            and isinstance(run.get("html_url"), str)
        ):
            return run
    raise SnapshotError(
        f"No successful {VALIDATE_WORKFLOW} run is recorded for exact commit {source_commit}."
    )


def derive_tag(source_sha: str, port_revision: str) -> str:
    if not FULL_SHA_RE.fullmatch(source_sha):
        raise SnapshotError("The upstream source must be a full lowercase Git SHA.")
    if not REVISION_RE.fullmatch(port_revision):
        raise SnapshotError("port_revision must be a positive integer.")
    return f"hve-core-edge-{source_sha}-codex.{port_revision}"


def _safe_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SnapshotError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _verify_checksum(asset: Path) -> None:
    data = _safe_file(asset, "Release asset")
    sidecar = asset.with_name(asset.name + ".sha256")
    text = _safe_file(sidecar, "SHA-256 sidecar").decode("ascii", errors="strict").strip()
    match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(.+)", text)
    if not match or match.group(2) != asset.name:
        raise SnapshotError(f"Malformed SHA-256 sidecar for {asset.name}.")
    actual = hashlib.sha256(data).hexdigest()
    if actual != match.group(1):
        raise SnapshotError(f"SHA-256 mismatch for {asset.name}.")


def verify_asset_directory(
    asset_dir: Path, *, port_commit: str, port_revision: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Validate names, provenance, and hashes consumed by the release installer."""
    if not FULL_SHA_RE.fullmatch(port_commit):
        raise SnapshotError("The port source commit must be a full lowercase Git SHA.")
    compatibility = _read_json(asset_dir / "compatibility-report.json", "compatibility report")
    source_sha = compatibility.get("upstream_sha")
    if compatibility.get("upstream_ref") != "main" or compatibility.get("channel") != "edge":
        raise SnapshotError("The compatibility report must identify the edge channel on upstream main.")
    if not isinstance(source_sha, str) or not FULL_SHA_RE.fullmatch(source_sha):
        raise SnapshotError("The compatibility report must identify a full immutable upstream SHA.")

    for name in ("dist.zip", "dist.tar.gz"):
        _verify_checksum(asset_dir / name)
    wheels = sorted(path for path in asset_dir.iterdir() if WHEEL_RE.fullmatch(path.name))
    if len(wheels) != 1:
        raise SnapshotError("Release must contain exactly one installer wheel with the expected name.")
    _verify_checksum(wheels[0])
    for name in ("checksums.txt", "release-notes.md", "validation-proof.json"):
        _safe_file(asset_dir / name, name)

    proof = _read_json(asset_dir / "validation-proof.json", "validation proof")
    if (
        proof.get("port_commit") != port_commit
        or proof.get("workflow_path") != VALIDATE_WORKFLOW
        or proof.get("status") != "completed"
        or proof.get("conclusion") != "success"
        or not isinstance(proof.get("run_id"), int)
        or not isinstance(proof.get("run_url"), str)
    ):
        raise SnapshotError("The validation proof is incomplete or does not match the port source commit.")
    if proof.get("tag") != derive_tag(source_sha, port_revision):
        raise SnapshotError("The validation proof release tag does not match the source SHA and port revision.")
    return derive_tag(source_sha, port_revision), compatibility, proof


def _notes(source_sha: str, port_commit: str, tag: str, run: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"# HVE Core Codex edge snapshot `{tag}`",
            "",
            "This is an unsigned development snapshot built from the latest immutable commit on upstream `main`.",
            "",
            "## Provenance and checks",
            "",
            f"- Upstream source: [`microsoft/hve-core@{source_sha}`](https://github.com/microsoft/hve-core/commit/{source_sha})",
            f"- Port source commit: `{port_commit}`",
            f"- Successful exact-commit validation: [Validate HVE Core Codex run {run['id']}]({run['html_url']})",
            "- The external distribution passed the repository's structural validator.",
            "- SHA-256 sidecars are included for the ZIP, tar archive, and installer wheel.",
            "",
            "## Development status and limitations",
            "",
            "- The known graded baseline is **58.6%** on an older pinned upstream source; it is not a score for this snapshot.",
            "- This snapshot has no live CLI or Codex Desktop acceptance evidence. Desktop validation remains manual.",
            "- Existing blocked or unverified cases remain listed in the attached compatibility report; this release does not clear them.",
            "- This path does not satisfy the existing strict stable-release runtime-evidence gates.",
            "- No GitHub artifact attestation, GPG signature, or release-app signature is attached.",
            "  SHA-256 files check download integrity; they do not establish publisher authenticity.",
            "- No paid model calls were used to build or validate this snapshot.",
            "",
            "Treat this as an edge development snapshot, not a stable release. Verify the SHA-256 sidecar before installation.",
            "",
        )
    )


def _prepare_assets(args: argparse.Namespace) -> str:
    payload = _read_json(args.validation_runs_json, "validation workflow runs")
    run = successful_validation_run(payload, args.port_commit)
    report = _read_json(args.distribution / "generated/compatibility-report.json", "compatibility report")
    source_sha = report.get("upstream_sha")
    if report.get("upstream_ref") != "main" or report.get("channel") != "edge" or not isinstance(source_sha, str):
        raise SnapshotError("Edge snapshots must use a compatibility report for upstream main.")
    tag = derive_tag(source_sha, args.port_revision)

    if args.asset_dir.exists():
        raise SnapshotError(f"Refusing to reuse an existing artifact directory: {args.asset_dir}")
    args.asset_dir.mkdir(parents=True)
    selected = [
        Path(str(args.distribution) + ".zip"),
        Path(str(args.distribution) + ".zip.sha256"),
        Path(str(args.distribution) + ".tar.gz"),
        Path(str(args.distribution) + ".tar.gz.sha256"),
        args.distribution / "generated/checksums.txt",
        args.distribution / "generated/compatibility-report.json",
    ]
    selected.extend(path for path in args.wheel_dir.iterdir() if WHEEL_RE.fullmatch(path.name))
    for path in selected:
        _safe_file(path, "Build output")
        target_name = {
            "checksums.txt": "checksums.txt",
            "compatibility-report.json": "compatibility-report.json",
        }.get(path.name, path.name)
        shutil.copyfile(path, args.asset_dir / target_name)
        if path.suffix == ".whl":
            sidecar = path.with_name(path.name + ".sha256")
            _safe_file(sidecar, "Installer wheel SHA-256 sidecar")
            shutil.copyfile(sidecar, args.asset_dir / sidecar.name)

    proof = {
        "port_commit": args.port_commit,
        "workflow_path": VALIDATE_WORKFLOW,
        "run_id": run["id"],
        "run_url": run["html_url"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "tag": tag,
    }
    (args.asset_dir / "validation-proof.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (args.asset_dir / "release-notes.md").write_text(
        _notes(source_sha, args.port_commit, tag, run), encoding="utf-8", newline="\n"
    )
    verify_asset_directory(args.asset_dir, port_commit=args.port_commit, port_revision=args.port_revision)
    return tag


def _ensure_publication_absent(repo: str, tag: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SnapshotError("GH_REPOSITORY is malformed.")
    if not re.fullmatch(r"hve-core-edge-[0-9a-f]{40}-codex\.[1-9][0-9]*", tag):
        raise SnapshotError("Release tag does not match the immutable edge snapshot format.")
    for endpoint, label in (
        (f"repos/{repo}/git/ref/tags/{tag}", "Git tag"),
        (f"repos/{repo}/releases/tags/{tag}", "GitHub release"),
    ):
        completed = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            raise SnapshotError(f"{label} {tag} already exists; choose a new port revision.")
        detail = f"{completed.stdout}\n{completed.stderr}"
        if "HTTP 404" not in detail and "Not Found" not in detail:
            raise SnapshotError(f"Could not safely check for an existing {label}: {detail.strip()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check-validation", help="Require a successful validation run for the exact commit")
    check.add_argument("--validation-runs-json", type=Path, required=True)
    check.add_argument("--port-commit", required=True)

    prepare = commands.add_parser("prepare-assets", help="Check and stage immutable edge release assets")
    prepare.add_argument("--distribution", type=Path, required=True)
    prepare.add_argument("--wheel-dir", type=Path, required=True)
    prepare.add_argument("--asset-dir", type=Path, required=True)
    prepare.add_argument("--validation-runs-json", type=Path, required=True)
    prepare.add_argument("--port-commit", required=True)
    prepare.add_argument("--port-revision", required=True)
    prepare.add_argument("--github-output", type=Path, required=True)

    verify = commands.add_parser("verify-assets", help="Recheck assets in the privileged publish job")
    verify.add_argument("--asset-dir", type=Path, required=True)
    verify.add_argument("--port-commit", required=True)
    verify.add_argument("--port-revision", required=True)
    verify.add_argument("--github-output", type=Path, required=True)

    absent = commands.add_parser("ensure-absent", help="Fail if an immutable tag or release already exists")
    absent.add_argument("--repo", required=True)
    absent.add_argument("--tag", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "check-validation":
            payload = _read_json(args.validation_runs_json, "validation workflow runs")
            run = successful_validation_run(payload, args.port_commit)
            print(f"Validated exact source commit in run {run['id']}: {run['html_url']}")
        elif args.command == "prepare-assets":
            tag = _prepare_assets(args)
            _, compatibility, proof = verify_asset_directory(
                args.asset_dir, port_commit=args.port_commit, port_revision=args.port_revision
            )
            with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"tag={tag}\n")
                output.write(f"upstream_sha={compatibility['upstream_sha']}\n")
                output.write(f"validation_run_url={proof['run_url']}\n")
        elif args.command == "verify-assets":
            tag, _, _ = verify_asset_directory(
                args.asset_dir, port_commit=args.port_commit, port_revision=args.port_revision
            )
            with args.github_output.open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"tag={tag}\n")
        else:
            _ensure_publication_absent(args.repo, args.tag)
        return 0
    except (OSError, SnapshotError, UnicodeDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
