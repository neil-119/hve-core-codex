"""Read-only weekly candidate selection. No signing credentials or model calls."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from hve_codex_porter.release import (
    discover_sources,
    validate_git_sha,
    validate_source_ref,
    weekly_publication_decision,
)


def command(*args: str) -> str:
    return subprocess.check_output(list(args), text=True).strip()


def read_channel_manifest(branch_sha: str, channel: str) -> dict | None:
    """Read and bind source-only release metadata from a protected branch commit."""
    prefix = f"{branch_sha}:release/{channel}"
    lock_result = subprocess.run(
        ["git", "show", f"{prefix}/source-lock.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    manifest_result = subprocess.run(
        ["git", "show", f"{prefix}/release-manifest.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if lock_result.returncode != 0 and manifest_result.returncode != 0:
        return None
    if lock_result.returncode != 0 or manifest_result.returncode != 0:
        raise SystemExit("Protected branch has an incomplete source-only release record.")

    try:
        lock = json.loads(lock_result.stdout)
        manifest = json.loads(manifest_result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Protected branch release metadata is malformed JSON: {exc}.") from exc
    if not isinstance(lock, dict) or not isinstance(manifest, dict):
        raise SystemExit("Protected branch release metadata must contain JSON objects.")
    if lock.get("schema_version") != 1 or lock.get("channel") != channel:
        raise SystemExit("Protected branch source lock has an unsupported schema or wrong channel.")
    if manifest.get("schema_version") != 1 or manifest.get("channel") != channel:
        raise SystemExit("Protected branch release manifest has an unsupported schema or wrong channel.")

    source = manifest.get("source")
    converter = manifest.get("converter")
    if not isinstance(source, dict) or not isinstance(converter, dict):
        raise SystemExit("Protected branch release manifest has malformed source or converter metadata.")
    try:
        lock_ref = validate_source_ref(lock.get("upstream_ref"), field="source-lock upstream ref")
        lock_sha = validate_git_sha(lock.get("upstream_sha"), field="source-lock upstream SHA")
        adapter_sha = validate_git_sha(lock.get("adapter_revision"), field="source-lock adapter revision")
        builder_sha = validate_git_sha(lock.get("builder_commit"), field="source-lock builder commit")
        manifest_ref = validate_source_ref(source.get("ref"), field="release manifest source ref")
        manifest_sha = validate_git_sha(source.get("sha"), field="release manifest source SHA")
        manifest_adapter_sha = validate_git_sha(manifest.get("adapter_sha"), field="release manifest adapter SHA")
        manifest_builder_sha = validate_git_sha(converter.get("sha"), field="release manifest builder SHA")
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Protected branch release metadata contains an invalid source pin: {exc}.") from exc

    if (lock_ref, lock_sha) != (manifest_ref, manifest_sha):
        raise SystemExit("Source lock does not match the reviewed release manifest source pin.")
    if adapter_sha != manifest_adapter_sha or builder_sha != manifest_builder_sha:
        raise SystemExit("Source lock does not match the reviewed adapter and builder revisions.")
    return manifest


def main() -> None:
    channel = os.environ["CHANNEL"]
    branches = {"stable": "main", "edge": "edge"}
    if channel not in branches:
        raise SystemExit("Weekly publication supports stable and edge only.")
    branch = branches[channel]
    event = os.environ["EVENT_NAME"]
    event_ref = os.environ["EVENT_REF"]
    if event == "schedule":
        if event_ref != "refs/heads/main":
            raise SystemExit("The weekly caller must run from reviewed main.")
    elif event != "workflow_dispatch" or event_ref != f"refs/heads/{branch}":
        raise SystemExit("Manual publication must run from its protected channel branch.")
    repo = os.environ["GITHUB_REPOSITORY"]
    protection = json.loads(command("gh", "api", f"repos/{repo}/branches/{branch}"))
    if protection.get("protected") is not True:
        raise SystemExit("Publication requires a protected channel branch.")
    command("gh", "auth", "setup-git")
    command("git", "fetch", "--no-tags", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}")
    sha = validate_git_sha(command("git", "rev-parse", f"origin/{branch}"), field="protected branch SHA")
    if event == "workflow_dispatch" and sha != os.environ["EVENT_SHA"]:
        raise SystemExit("Manual publication is stale; select the current protected branch commit.")
    manifest = read_channel_manifest(sha, channel)
    latest = discover_sources(channel, token=os.environ.get("GH_TOKEN"), limit=1)
    if not latest:
        decision = {"publish": False, "reason": "No published upstream source exists for this channel."}
    else:
        pages = json.loads(command("gh", "api", f"repos/{repo}/releases", "--paginate", "--slurp"))
        releases = [release for page in pages for release in page]
        decision = weekly_publication_decision(channel, manifest, latest[0], releases)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"publish={'true' if decision['publish'] else 'false'}\n")
        stream.write(f"source_sha={sha}\n")
        stream.write(f"target_branch={branch}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write(f"{channel}: {decision['reason']}\n")


if __name__ == "__main__":
    main()
