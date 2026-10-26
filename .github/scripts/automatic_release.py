"""Pin, package and publish migration-validated releases without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from hve_codex_porter.release import (
    ReleaseError, _trusted_scenario_hash, build_release_manifest, discover_sources,
    validate_git_sha, validate_source_ref, weekly_publication_decision,
)


def gh(*args: str):
    """Read GitHub JSON, failing on transport and authentication errors."""
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def published_assets_valid(release: dict) -> None:
    """Do not silently skip a published release with missing installer assets."""
    names = {asset["name"] for asset in release.get("assets", [])}
    wheels = [name for name in names if re.fullmatch(r"hve_core_codex-[A-Za-z0-9_.]+-py3-none-any\.whl", name)]
    required = {"dist.zip", "dist.zip.sha256", "dist.tar.gz", "dist.tar.gz.sha256"}
    if len(wheels) != 1 or not required.issubset(names) or wheels[0] + ".sha256" not in names:
        raise ReleaseError("Published release is missing required installer assets; intervention required.")


def published_source_valid(release: dict, source_sha: str, channel: str) -> None:
    """Detect a moved stable upstream tag rather than silently skipping it."""
    published_assets_valid(release)
    if channel == "stable":
        assets = [asset for asset in release["assets"] if asset["name"] == "release-manifest.json"]
        if len(assets) != 1:
            raise ReleaseError("Published stable release lacks immutable source provenance.")
        repo = os.environ["GITHUB_REPOSITORY"]
        candidate = gh("api", f"repos/{repo}/releases/assets/{assets[0]['id']}", "-H", "Accept: application/octet-stream")
        if candidate.get("source", {}).get("sha") != source_sha:
            raise ReleaseError("Published stable upstream tag moved; refusing to rewrite its release.")


def tag_for(channel: str, source: dict, revision: int = 1) -> str:
    """Derive an immutable tag from a validated upstream source."""
    sha = validate_git_sha(source["source_sha"], field="upstream SHA")
    if not isinstance(revision, int) or isinstance(revision, bool) or not 1 <= revision <= 10000:
        raise ReleaseError("Port revision must be a positive bounded integer.")
    if channel == "edge":
        return f"hve-core-edge-{sha}-codex.{revision}"
    version = source["upstream_manifest_version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\+[0-9A-Za-z.-]+)?", version):
        raise ReleaseError("Stable source must have a non-prerelease semantic version.")
    return f"hve-core-v{version}-codex.{revision}"


def choose_revision(channel: str, source: dict, refs: list[dict], port_commit: str) -> int:
    """Reuse same-commit attempts or allocate a new revision without moving tags."""
    existing = {ref["ref"]: ref["object"] for ref in refs}
    for revision in range(1, 10001):
        target = existing.get("refs/tags/" + tag_for(channel, source, revision))
        if target is None or (target.get("type") == "commit" and target.get("sha") == port_commit):
            return revision
    raise ReleaseError("No available immutable port revision.")


def output(**values) -> None:
    """Write trusted job outputs without allowing injected output lines."""
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in str(value) or "\r" in str(value):
                raise ReleaseError("Unsafe job output.")
            stream.write(f"{key}={value}\n")


def select(channel: str) -> None:
    """Skip already published sources and pin the next immutable source."""
    repo = os.environ["GITHUB_REPOSITORY"]
    head = gh("api", f"repos/{repo}/branches/main")
    if os.environ["GITHUB_REF"] != "refs/heads/main" or not head.get("protected"):
        raise ReleaseError("Automatic release requires protected main.")
    if head["commit"]["sha"] != os.environ["GITHUB_SHA"]:
        raise ReleaseError("Main advanced; rerun from its current commit.")
    sources = discover_sources(channel, token=os.environ.get("GH_TOKEN"), limit=1)
    if not sources:
        output(changed="false")
        return
    source = sources[0]
    validate_source_ref(source["source_ref"])
    sha = validate_git_sha(source["source_sha"], field="upstream SHA")
    # Stable version is acquired from the immutable upstream manifest during build.
    releases = [item for page in gh("api", f"repos/{repo}/releases", "--paginate", "--slurp") for item in page]
    for release in releases:
        if release.get("draft"):
            continue
        if channel == "edge":
            matches = bool(re.fullmatch(f"hve-core-edge-{sha}-codex\\.[1-9][0-9]*", release.get("tag_name", "")))
        else:
            version = re.fullmatch(r"(?:hve-core-)?v?([0-9]+\.[0-9]+\.[0-9]+(?:\+[0-9A-Za-z.-]+)?)", source["source_ref"])
            if not version:
                raise ReleaseError("Latest stable upstream tag has an unsupported version format.")
            prefix = re.escape(f"hve-core-v{version.group(1)}-codex.")
            matches = bool(re.fullmatch(prefix + r"[1-9][0-9]*", release.get("tag_name", "")))
        if matches:
            if release.get("prerelease") is not (channel == "edge"):
                raise ReleaseError("Existing source release has the wrong channel status.")
            published_source_valid(release, sha, channel)
            output(changed="false")
            return
    version = re.fullmatch(r"(?:hve-core-)?v?([0-9]+\.[0-9]+\.[0-9]+(?:\+[0-9A-Za-z.-]+)?)", source["source_ref"])
    if channel == "stable" and not version:
        raise ReleaseError("Latest stable upstream tag has an unsupported version format.")
    tag_source = {"source_sha": sha, "upstream_manifest_version": version.group(1) if version else ""}
    prefix = tag_for(channel, tag_source).rsplit(".", 1)[0] + "."
    refs = gh("api", f"repos/{repo}/git/matching-refs/tags/{prefix}")
    source["port_revision"] = choose_revision(channel, tag_source, refs, os.environ["GITHUB_SHA"])
    Path(os.environ["RUNNER_TEMP"], "selected-source.json").write_text(json.dumps(source), encoding="utf-8")
    output(changed="true", source_sha=sha, source_ref=source["source_ref"])


def manifest(channel: str, distribution: Path) -> None:
    """Bind the distribution to the actual upstream and port commits."""
    provenance = json.loads((distribution / "generated/provenance.json").read_text())
    selected = json.loads(Path(os.environ["RUNNER_TEMP"], "selected-source.json").read_text())
    if (provenance["upstream_sha"], provenance["upstream_ref"]) != (selected["source_sha"], selected["source_ref"]):
        raise ReleaseError("Build provenance disagrees with selected source.")
    build_release_manifest(
        root=distribution, channel=channel, source_ref=selected["source_ref"],
        source_sha=selected["source_sha"], upstream_manifest_version=provenance["upstream_manifest_version"],
        adapter_sha=os.environ["GITHUB_SHA"], converter_sha=os.environ["GITHUB_SHA"],
        converter_version=provenance["converter_version"], mapping_sha256=provenance["mapping_hash"],
        scenario_definition_sha256=_trusted_scenario_hash(Path.cwd()),
        generated_at=subprocess.check_output(["git", "show", "-s", "--format=%cI", os.environ["GITHUB_SHA"]], text=True).strip(),
    )


def asset_digest(directory: Path) -> str:
    """Bind every asset name and byte to the read-only build job's output."""
    inventory = {}
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", path.name):
            raise ReleaseError("Release assets must be safely named regular files.")
        inventory[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not inventory:
        raise ReleaseError("Empty release assets.")
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def seal(channel: str, assets: Path) -> None:
    """Record honest validation scope and seal the exact uploaded assets."""
    candidate = json.loads((assets / "release-manifest.json").read_text())
    source = {
        "source_sha": candidate["source"]["sha"],
        "upstream_manifest_version": candidate["upstream_manifest_version"],
    }
    selected = json.loads(Path(os.environ["RUNNER_TEMP"], "selected-source.json").read_text())
    tag = tag_for(channel, source, selected["port_revision"])
    notes = (
        f"HVE Core Codex {channel}: {tag}\n\n"
        f"Upstream: microsoft/hve-core@{source['source_sha']}\n"
        f"Migrator: {os.environ['GITHUB_SHA']}\n\n"
        "Published automatically after deterministic migration tests, dependency audit, structural validation, "
        "inventory/reference coverage, install smoke checks and SHA-256 checks. "
        "Stable follows Microsoft's stable releases; edge follows upstream main.\n\n"
        "Validation scope: migration-only. No fresh live CLI/Desktop or Vally model acceptance is claimed. "
        "Existing compatibility limitations remain in compatibility-report.json. "
        "No paid model calls, GPG signatures or artifact attestations were used.\n"
    )
    (assets / "release-notes.md").write_text(notes, encoding="utf-8")
    output(tag=tag, digest=asset_digest(assets))


def publish(channel: str, assets: Path, expected_digest: str, tag: str) -> None:
    """Recheck sealed assets and publish without rewriting existing tags."""
    if asset_digest(assets) != expected_digest:
        raise ReleaseError("Downloaded assets do not match the validated build job.")
    repo = os.environ["GITHUB_REPOSITORY"]
    head = gh("api", f"repos/{repo}/branches/main")
    if not head.get("protected") or head["commit"]["sha"] != os.environ["GITHUB_SHA"]:
        raise ReleaseError("Protected main advanced before publication.")
    candidate = json.loads((assets / "release-manifest.json").read_text())
    revision_match = re.search(r"-codex\.([1-9][0-9]*)$", tag)
    if not revision_match:
        raise ReleaseError("Invalid immutable release revision.")
    expected_tag = tag_for(channel, {"source_sha": candidate["source"]["sha"],
                                   "upstream_manifest_version": candidate["upstream_manifest_version"]},
                           int(revision_match.group(1)))
    if tag != expected_tag or candidate["channel"] != channel or candidate["converter"]["sha"] != os.environ["GITHUB_SHA"]:
        raise ReleaseError("Release tag or provenance does not match the validated build.")
    latest = discover_sources(channel, token=os.environ.get("GH_TOKEN"), limit=1)
    releases = [item for page in gh("api", f"repos/{repo}/releases", "--paginate", "--slurp") for item in page]
    if not latest:
        raise ReleaseError("Upstream source disappeared before publication.")
    decision = weekly_publication_decision(channel, candidate, latest[0], releases)
    if not decision["publish"]:
        if "already published" in decision["reason"]:
            published_source_valid(next(item for item in releases if item.get("tag_name") == decision["release_tag"]),
                                   candidate["source"]["sha"], channel)
            return
        raise ReleaseError(decision["reason"])
    refs = gh("api", f"repos/{repo}/git/matching-refs/tags/{tag}")
    exact = [ref for ref in refs if ref["ref"] == f"refs/tags/{tag}"]
    if exact:
        if exact[0]["object"].get("type") != "commit" or exact[0]["object"]["sha"] != os.environ["GITHUB_SHA"]:
            raise ReleaseError("Existing immutable tag points to another commit; refusing to overwrite it.")
    else:
        gh("api", f"repos/{repo}/git/refs", "-f", f"ref=refs/tags/{tag}", "-f", f"sha={os.environ['GITHUB_SHA']}")
    drafts = [item for item in releases if item.get("tag_name") == tag and item.get("draft")]
    if drafts:
        if drafts[0].get("target_commitish") != os.environ["GITHUB_SHA"]:
            raise ReleaseError("Existing draft belongs to another port commit.")
    else:
        args = ["gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--draft", "--title", tag,
                "--target", os.environ["GITHUB_SHA"], "--notes-file", str(assets / "release-notes.md")]
        if channel == "edge":
            args += ["--prerelease"]
        subprocess.run(args, check=True)
    subprocess.run(["gh", "release", "upload", tag, "--repo", repo, "--clobber",
                    *(str(path) for path in sorted(assets.iterdir()))], check=True)
    # The tag lookup endpoint excludes drafts; list drafts then address the ID.
    refreshed = [item for page in gh("api", f"repos/{repo}/releases", "--paginate", "--slurp") for item in page]
    matches = [item for item in refreshed if item.get("tag_name") == tag and item.get("draft")]
    if len(matches) != 1:
        raise ReleaseError("Could not identify the exact unpublished release.")
    draft = gh("api", f"repos/{repo}/releases/{matches[0]['id']}")
    expected = {path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in assets.iterdir()}
    actual = {item["name"]: item.get("digest") for item in draft["assets"]}
    if actual != expected or not draft.get("draft") or draft.get("prerelease") is not (channel == "edge"):
        raise ReleaseError("Draft asset inventory, server checksums or release channel failed verification.")
    head = gh("api", f"repos/{repo}/branches/main")
    if not head.get("protected") or head["commit"]["sha"] != os.environ["GITHUB_SHA"]:
        raise ReleaseError("Main advanced during asset upload; draft remains unpublished.")
    # Release visibility changes only after every upload is independently verified.
    published = gh("api", f"repos/{repo}/releases/{draft['id']}", "--method", "PATCH",
                   "-F", "draft=false", "-f", f"make_latest={'false' if channel == 'edge' else 'true'}")
    if published.get("draft") is not False:
        raise ReleaseError("GitHub did not confirm release publication.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "manifest", "seal", "publish"))
    parser.add_argument("--channel", choices=("stable", "edge"), required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--digest")
    parser.add_argument("--tag")
    args = parser.parse_args()
    if args.command == "select":
        select(args.channel)
    elif args.command == "manifest":
        manifest(args.channel, args.directory)
    elif args.command == "seal":
        seal(args.channel, args.directory)
    else:
        publish(args.channel, args.directory, args.digest, args.tag)


if __name__ == "__main__":
    main()
