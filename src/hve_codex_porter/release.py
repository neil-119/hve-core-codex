"""Safe upstream release discovery and publication evidence helpers.

This module deliberately does not create GitHub releases, tags, commits, or PRs.
It discovers immutable source revisions, validates release evidence, and stages
the local ledger/metadata that a separately permissioned workflow may publish.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping


DEFAULT_OWNER = "microsoft"
DEFAULT_REPOSITORY = "hve-core"
DEFAULT_API_BASE = "https://api.github.com"
DEFAULT_LEDGER = Path("generated/processed-sources.json")
LEDGER_VERSION = 1
RELEASE_MANIFEST_PATH = "generated/release-manifest.json"
CHECKSUMS_PATH = "generated/checksums.txt"
RUNTIME_EVIDENCE_PATH = "generated/runtime-evidence.json"
RUNTIME_SCENARIOS = (
    "implicit-research-selection", "explicit-only-negative-routing",
    "trusted-hooks-native-positive-negative-resume", "delegated-native-role-and-hook-context",
    "child-read-only-permission",
)
RUNTIME_SURFACES = ("cli", "desktop")

# Paths produced by the converter and eligible for a bot PR. Keep this list
# deliberately narrow: source files, workflows, converter code, and arbitrary
# root files must never be staged from generated or upstream-controlled paths.
GENERATED_PATH_ALLOWLIST = (
    "plugins/hve-core-codex/",
    "codex-overlay/agents/",
    "codex-overlay/rules/",
    "codex-overlay/hooks/",
    "codex-overlay/AGENTS.hve-core.md",
    ".agents/plugins/marketplace.json",
    "generated/",
)
HASH_EXCLUDED_PATHS = {
    RELEASE_MANIFEST_PATH,
    CHECKSUMS_PATH,
    str(DEFAULT_LEDGER).replace("\\", "/"),
    RUNTIME_EVIDENCE_PATH,
}
CONVERTER_RESERVED_PATHS = (
    "generated/runtime-evidence/",
)
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REF_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
CHANNELS = ("stable", "prerelease", "edge")


class ReleaseError(ValueError):
    """An actionable discovery, evidence, or publication-gate failure."""


def _reject_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    cursor = absolute
    while True:
        if cursor.is_symlink():
            raise ReleaseError(f"Refusing to read or write through a symlink: {cursor}.")
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def _validate_channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise ReleaseError(f"Unsupported release channel {channel!r}; choose one of {', '.join(CHANNELS)}.")
    return channel


def validate_git_sha(value: str, *, field: str = "commit SHA") -> str:
    sha = str(value).lower()
    if not HEX_SHA_RE.fullmatch(sha):
        raise ReleaseError(f"{field} must be a full 40-character hexadecimal Git SHA; got {value!r}.")
    return sha


def validate_source_ref(value: str, *, field: str = "source ref") -> str:
    """Accept conservative, unambiguous Git branch/tag names for provenance."""
    if not isinstance(value, str) or not value or value.startswith("/") or value.endswith(("/", ".")):
        raise ReleaseError(f"{field} is not a safe Git ref: {value!r}.")
    if ".." in value or "@{" in value or "//" in value or "\\" in value or "\x00" in value:
        raise ReleaseError(f"{field} is not a safe Git ref: {value!r}.")
    components = value.split("/")
    if any(
        not _REF_COMPONENT_RE.fullmatch(component)
        or component.startswith(".")
        or component.endswith(".lock")
        for component in components
    ):
        raise ReleaseError(f"{field} is not a safe Git ref: {value!r}.")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> tuple[str, float]:
    if not value:
        return "", 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ReleaseError(f"GitHub returned an invalid published_at timestamp: {value!r}.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(), parsed.timestamp()


def empty_ledger() -> dict[str, Any]:
    return {"version": LEDGER_VERSION, "processed": [], "tag_shas": {}}


def load_ledger(path: Path | str | None = None) -> dict[str, Any]:
    ledger_path = Path(path) if path is not None else DEFAULT_LEDGER
    _reject_symlink_ancestors(ledger_path)
    if not ledger_path.exists():
        return empty_ledger()
    if ledger_path.is_symlink():
        raise ReleaseError(f"Refusing to read a symlink as the upstream ledger: {ledger_path}.")
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Cannot read upstream ledger {ledger_path}: {exc}.") from exc
    if not isinstance(data, dict) or data.get("version") != LEDGER_VERSION:
        raise ReleaseError(f"Unsupported or malformed upstream ledger at {ledger_path}.")
    processed = data.get("processed")
    tag_shas = data.get("tag_shas")
    if not isinstance(processed, list) or not isinstance(tag_shas, dict):
        raise ReleaseError(f"Malformed upstream ledger at {ledger_path}: expected processed[] and tag_shas{{}}.")
    for item in processed:
        if not isinstance(item, dict) or item.get("channel") not in CHANNELS:
            raise ReleaseError(f"Malformed processed-source ledger entry: {item!r}.")
        validate_git_sha(item.get("source_sha", ""), field="ledger source SHA")
        validate_source_ref(item.get("source_ref", ""), field="ledger source ref")
        if item.get("tag") is not None:
            validate_source_ref(item["tag"], field="ledger tag")
        if item.get("outcome") not in {"pr-opened", "merged", "published"}:
            raise ReleaseError(f"Malformed processed-source ledger outcome: {item.get('outcome')!r}.")
    for tag, sha in tag_shas.items():
        if not isinstance(tag, str) or not tag:
            raise ReleaseError(f"Malformed tag key in upstream ledger: {tag!r}.")
        validate_source_ref(tag, field="ledger tag")
        validate_git_sha(sha, field=f"ledger SHA for tag {tag!r}")
    return data


def write_ledger(path: Path | str, ledger: Mapping[str, Any]) -> None:
    ledger_path = Path(path)
    _reject_symlink_ancestors(ledger_path)
    if ledger_path.is_symlink():
        raise ReleaseError(f"Refusing to overwrite a symlink as the upstream ledger: {ledger_path}.")
    data = dict(ledger)
    if data.get("version") != LEDGER_VERSION:
        raise ReleaseError(f"Cannot write unsupported ledger version {data.get('version')!r}.")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path.with_name(ledger_path.name + ".tmp")
    if tmp.is_symlink():
        raise ReleaseError(f"Refusing to replace a symlink temporary ledger: {tmp}.")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, ledger_path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReleaseError(f"Cannot atomically write upstream ledger {ledger_path}: {exc}.") from exc


def record_processed(
    ledger: Mapping[str, Any],
    *,
    channel: str,
    source_ref: str,
    source_sha: str,
    outcome: str,
    tag: str | None = None,
    timestamp: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return an updated ledger; callers should invoke only after a successful step.

    ``pr-opened`` records that the sync bot PR exists. ``merged`` and
    ``published`` are retained in the record as lifecycle evidence. A repeated
    source SHA is a no-op, which also makes retried jobs idempotent.
    """
    _validate_channel(channel)
    source_sha = validate_git_sha(source_sha, field="source SHA")
    if outcome not in {"pr-opened", "merged", "published"}:
        raise ReleaseError("Ledger outcome must be pr-opened, merged, or published.")
    validate_source_ref(source_ref)
    if tag:
        validate_source_ref(tag, field="upstream tag")
    updated = json.loads(json.dumps(ledger))
    if updated.get("version") != LEDGER_VERSION:
        raise ReleaseError("Cannot record a source in an unsupported ledger version.")
    updated.setdefault("processed", [])
    updated.setdefault("tag_shas", {})
    for entry in updated["processed"]:
        if entry.get("channel") == channel and entry.get("source_sha") == source_sha:
            # Upgrade lifecycle state monotonically while preserving idempotence.
            rank = {"pr-opened": 1, "merged": 2, "published": 3}
            changed = False
            if rank[outcome] > rank.get(entry.get("outcome", "pr-opened"), 0):
                entry["outcome"] = outcome
                changed = True
            if tag:
                previous = updated["tag_shas"].get(tag)
                if previous and previous != source_sha:
                    raise ReleaseError(f"Upstream tag {tag!r} moved from {previous} to {source_sha}; refusing to rewrite history.")
                if previous is None:
                    changed = True
                updated["tag_shas"][tag] = source_sha
            return updated, changed
    if tag:
        previous = updated["tag_shas"].get(tag)
        if previous and previous != source_sha:
            raise ReleaseError(f"Upstream tag {tag!r} moved from {previous} to {source_sha}; refusing to rewrite history.")
        updated["tag_shas"][tag] = source_sha
    updated["processed"].append(
        {
            "channel": channel,
            "source_ref": source_ref,
            "tag": tag,
            "source_sha": source_sha,
            "outcome": outcome,
            "processed_at": timestamp or _utc_now(),
        }
    )
    updated["processed"].sort(key=lambda item: (item["channel"], item["processed_at"], item["source_sha"]))
    updated["tag_shas"] = dict(sorted(updated["tag_shas"].items()))
    return updated, True


class GitHubApi:
    """Small read-only GitHub REST client with pagination and actionable failures."""

    def __init__(
        self,
        *,
        api_base: str = DEFAULT_API_BASE,
        token: str | None = None,
        timeout: float = 30.0,
        opener: Callable[..., Any] | None = None,
        retries: int = 2,
    ) -> None:
        parsed = urllib.parse.urlsplit(api_base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ReleaseError("GitHub API base must be an HTTPS URL without embedded credentials.")
        if parsed.query or parsed.fragment:
            raise ReleaseError("GitHub API base cannot include a query or fragment.")
        if token and (parsed.hostname != "api.github.com" or parsed.port not in {None, 443}):
            raise ReleaseError("A GitHub token may only be sent to https://api.github.com.")
        self.api_base = api_base.rstrip("/")
        self.api_origin = f"{parsed.scheme}://{parsed.netloc}"
        self.token = token
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.retries = max(0, retries)

    def get_json(self, path: str) -> Any:
        if not path.startswith("/") or ".." in PurePosixPath(path).parts:
            raise ReleaseError(f"Refusing unsafe GitHub API path {path!r}.")
        url = self.api_base + path
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "hve-core-codex-release-discovery",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        attempt = 0
        while True:
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    geturl = getattr(response, "geturl", None)
                    if callable(geturl):
                        final_url = urllib.parse.urlsplit(geturl())
                        requested_url = urllib.parse.urlsplit(url)
                        if (
                            final_url.scheme != "https"
                            or final_url.hostname != urllib.parse.urlsplit(url).hostname
                            or final_url.port not in {None, 443}
                            or (requested_url.port or 443) != (final_url.port or 443)
                        ):
                            raise ReleaseError("Refusing a GitHub API redirect to a different or insecure origin.")
                    body = response.read()
                return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = self._http_error_detail(exc)
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    delay = min(2**attempt, 4)
                    if retry_after and retry_after.isdigit():
                        delay = min(max(1, int(retry_after)), 4)
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise ReleaseError(f"GitHub API request failed ({exc.code}) for {path}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 4))
                    attempt += 1
                    continue
                raise ReleaseError(f"Cannot reach GitHub API for {path}: {exc.reason}.") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReleaseError(f"GitHub API returned invalid JSON for {path}: {exc}.") from exc
            except TimeoutError as exc:
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 4))
                    attempt += 1
                    continue
                raise ReleaseError(f"GitHub API request timed out for {path}.") from exc

    @staticmethod
    def _http_error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            message = data.get("message") if isinstance(data, dict) else None
        except Exception:
            message = None
        if exc.code in {403, 429}:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
            if retry_after:
                return f"{message or 'rate limited'}; retry after {retry_after} seconds."
            if reset:
                try:
                    reset_at = datetime.fromtimestamp(int(reset), timezone.utc).isoformat()
                except (TypeError, ValueError, OverflowError):
                    reset_at = reset
                return f"{message or 'rate limited'}; rate limit resets at {reset_at}."
        return f"{message or exc.reason}."

    def list_all(self, path: str, *, per_page: int = 100, max_pages: int = 10000) -> list[Any]:
        if not path.startswith("/") or "?" in path:
            raise ReleaseError(f"Pagination requires a plain API path, got {path!r}.")
        records: list[Any] = []
        page = 1
        while page <= max_pages:
            response = self.get_json(f"{path}?per_page={per_page}&page={page}")
            if not isinstance(response, list):
                raise ReleaseError(f"Expected a paginated list from GitHub endpoint {path}.")
            records.extend(response)
            if len(response) < per_page:
                return records
            page += 1
        raise ReleaseError(f"GitHub pagination exceeded {max_pages} pages for {path}; stopping safely.")


def _repository_api_path(owner: str, repository: str, suffix: str) -> str:
    token = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not token.fullmatch(owner) or not token.fullmatch(repository):
        raise ReleaseError("GitHub owner and repository must contain only letters, digits, dot, underscore, or hyphen.")
    return f"/repos/{owner}/{repository}{suffix}"


def _resolve_tag_commit(api: GitHubApi, owner: str, repository: str, tag: str) -> str:
    encoded_tag = urllib.parse.quote(tag, safe="")
    path = _repository_api_path(owner, repository, f"/git/ref/tags/{encoded_tag}")
    ref = api.get_json(path)
    if not isinstance(ref, dict) or not isinstance(ref.get("object"), dict):
        raise ReleaseError(f"GitHub did not return a Git object for upstream tag {tag!r}.")
    obj = ref["object"]
    seen: set[str] = set()
    for _ in range(8):
        sha = validate_git_sha(str(obj.get("sha", "")), field=f"object SHA for upstream tag {tag!r}")
        if sha in seen:
            raise ReleaseError(f"Upstream tag {tag!r} contains a cyclic annotated-tag chain.")
        seen.add(sha)
        kind = obj.get("type")
        if kind == "commit":
            return sha
        if kind != "tag":
            raise ReleaseError(f"Upstream tag {tag!r} resolves to a {kind!r}, not a commit.")
        tag_object = api.get_json(_repository_api_path(owner, repository, f"/git/tags/{sha}"))
        nested = tag_object.get("object") if isinstance(tag_object, dict) else None
        if not isinstance(nested, dict):
            raise ReleaseError(f"GitHub returned a malformed annotated tag object for {tag!r}.")
        obj = nested
    raise ReleaseError(f"Upstream tag {tag!r} has more than eight nested annotated-tag objects.")


def _ledger_has_source(ledger: Mapping[str, Any], channel: str, source_sha: str) -> bool:
    return any(
        entry.get("channel") == channel and entry.get("source_sha") == source_sha
        for entry in ledger.get("processed", [])
    )


def discover_sources(
    channel: str,
    *,
    api: GitHubApi | None = None,
    api_base: str = DEFAULT_API_BASE,
    token: str | None = None,
    owner: str = DEFAULT_OWNER,
    repository: str = DEFAULT_REPOSITORY,
    ledger: Mapping[str, Any] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return channel-specific immutable source revisions for safe sync.

    The REST tags and releases endpoints are both fully paginated. Tag names
    are resolved through Git reference objects so annotated tags are pinned to
    their target commit, not to the tag-object SHA. Stable discovery considers
    only the newest published stable release, so an older unseen release is
    never selected as backlog after the latest release was processed.
    """
    channel = _validate_channel(channel)
    if limit is not None and limit < 1:
        raise ReleaseError("Discovery limit must be a positive integer.")
    api = api or GitHubApi(api_base=api_base, token=token)
    ledger_data = ledger if ledger is not None else empty_ledger()
    if ledger_data.get("version") != LEDGER_VERSION:
        raise ReleaseError("Cannot discover against an unsupported processed-source ledger.")
    tag_path = _repository_api_path(owner, repository, "/tags")
    tags_raw = api.list_all(tag_path)
    tags: dict[str, Any] = {}
    for item in tags_raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ReleaseError("GitHub returned a malformed tag record.")
        name = item["name"]
        validate_source_ref(name, field="upstream tag")
        if name in tags:
            raise ReleaseError(f"GitHub returned duplicate upstream tag {name!r}.")
        tags[name] = item

    known_tag_shas = ledger_data.get("tag_shas", {})
    missing_tags = sorted(set(known_tag_shas) - set(tags))
    if missing_tags:
        raise ReleaseError("Previously processed upstream tag(s) disappeared: " + ", ".join(missing_tags))
    for tag_name in tags:
        expected = known_tag_shas.get(tag_name)
        if expected:
            current = _resolve_tag_commit(api, owner, repository, tag_name)
            if current != expected:
                raise ReleaseError(
                    f"Upstream tag {tag_name!r} moved from {expected} to {current}; refusing to process a reissued tag."
                )

    if channel == "edge":
        commit = api.get_json(_repository_api_path(owner, repository, "/commits/main"))
        if not isinstance(commit, dict):
            raise ReleaseError("GitHub returned a malformed main commit response.")
        source_sha = validate_git_sha(str(commit.get("sha", "")), field="upstream main SHA")
        if _ledger_has_source(ledger_data, channel, source_sha):
            return []
        return [
            {
                "channel": channel,
                "source_ref": "main",
                "source_sha": source_sha,
                "tag": None,
                "published_at": None,
                "upstream_release_id": None,
                "upstream_version": None,
                "prerelease": False,
            }
        ]

    release_path = _repository_api_path(owner, repository, "/releases")
    releases = api.list_all(release_path)
    tag_names = set(tags)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for release in releases:
        if not isinstance(release, dict):
            raise ReleaseError("GitHub returned a malformed release record.")
        tag_name = release.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            raise ReleaseError("GitHub release record has no tag_name.")
        if release.get("draft") is True:
            continue
        prerelease = release.get("prerelease") is True
        if channel == "stable" and prerelease:
            continue
        if channel == "prerelease" and not prerelease:
            continue
        if tag_name not in tag_names:
            raise ReleaseError(f"Published upstream release {tag_name!r} has no matching tag in the paginated tag inventory.")
        published_at, stamp = _parse_time(release.get("published_at"))
        source_sha = _resolve_tag_commit(api, owner, repository, tag_name)
        previous = known_tag_shas.get(tag_name)
        if previous and previous != source_sha:
            raise ReleaseError(f"Upstream tag {tag_name!r} moved from {previous} to {source_sha}; refusing to rewrite history.")
        candidates.append(
            (
                stamp,
                {
                    "channel": channel,
                    "source_ref": tag_name,
                    "source_sha": source_sha,
                    "tag": tag_name,
                    "published_at": published_at or None,
                    "upstream_release_id": release.get("id"),
                    "upstream_version": None,
                    "prerelease": prerelease,
                },
            )
        )
    candidates.sort(key=lambda pair: (pair[0], pair[1]["tag"], pair[1]["source_sha"]))
    if channel == "stable":
        if not candidates:
            return []
        latest = candidates[-1][1]
        if _ledger_has_source(ledger_data, channel, latest["source_sha"]):
            return []
        results = [latest]
    else:
        results = [
            candidate
            for _, candidate in candidates
            if not _ledger_has_source(ledger_data, channel, candidate["source_sha"])
        ]
    if limit is not None:
        results = results[:limit]
    return results


def weekly_publication_decision(
    channel: str,
    manifest: Mapping[str, Any] | None,
    latest: Mapping[str, Any],
    published_releases: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Read-only latest-source/no-op check; never replaces release eligibility."""
    if channel not in {"stable", "edge"}:
        raise ReleaseError("Weekly publication supports stable and edge only.")
    if latest.get("channel") != channel:
        raise ReleaseError("Latest upstream discovery returned the wrong channel.")
    latest_sha = validate_git_sha(latest.get("source_sha"), field="latest upstream SHA")
    latest_ref = validate_source_ref(latest.get("source_ref"), field="latest upstream ref")
    if manifest is None:
        return {"publish": False, "reason": "No reviewed release manifest on the channel branch."}
    if manifest.get("channel") != channel or not isinstance(manifest.get("source"), Mapping):
        raise ReleaseError("Channel branch contains a malformed or wrong-channel release manifest.")
    source = manifest["source"]
    candidate_sha = validate_git_sha(source.get("sha"), field="candidate upstream SHA")
    candidate_ref = validate_source_ref(source.get("ref"), field="candidate upstream ref")
    if candidate_sha != latest_sha or candidate_ref != latest_ref:
        return {"publish": False, "reason": "Latest upstream source is awaiting conversion or review."}
    if channel == "stable":
        version = str(manifest.get("upstream_manifest_version", ""))
        match = re.fullmatch(r"v?([0-9]+\.[0-9]+\.[0-9]+(?:\+[0-9A-Za-z.-]+)?)", version)
        if not match or manifest.get("upstream_prerelease") is True:
            raise ReleaseError("Weekly stable publication requires a non-prerelease semantic version.")
        prefix = f"hve-core-v{match.group(1)}-codex."
    else:
        if latest_ref != "main":
            raise ReleaseError("Weekly edge publication must follow upstream main.")
        prefix = f"hve-core-edge-{latest_sha}-codex."
    for release in published_releases:
        if release.get("draft") is True:
            continue
        tag = release.get("tag_name")
        if isinstance(tag, str) and re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", tag):
            if release.get("prerelease") is not (channel == "edge"):
                raise ReleaseError("Existing published source has the wrong release channel status.")
            return {"publish": False, "reason": "This upstream source is already published.", "release_tag": tag}
    return {"publish": True, "reason": "Latest upstream source has not been published; eligibility still required."}


def normalize_generated_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ReleaseError(f"Invalid generated path: {path!r}.")
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        raise ReleaseError(f"Generated paths cannot contain control characters: {path!r}.")
    if "\\" in path or ":" in path:
        raise ReleaseError(f"Generated paths must use safe repository-relative POSIX names: {path!r}.")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ReleaseError(f"Generated path escapes the repository: {path!r}.")
    normalized = pure.as_posix()
    if normalized.startswith(".git/") or normalized == ".git":
        raise ReleaseError(f"Generated path cannot target Git metadata: {path!r}.")
    return normalized


def is_generated_path_allowed(path: str) -> bool:
    try:
        normalized = normalize_generated_path(path)
    except ReleaseError:
        return False
    return any(
        normalized.startswith(prefix) if prefix.endswith("/") else normalized == prefix
        for prefix in GENERATED_PATH_ALLOWLIST
    )


def validate_generated_paths(paths: Iterable[str], *, root: Path | str | None = None) -> list[str]:
    """Validate allowlist membership, duplicate names, and symlink escapes."""
    normalized = [normalize_generated_path(path) for path in paths]
    if len(normalized) != len(set(normalized)):
        raise ReleaseError("Generated path list contains duplicate paths.")
    rejected = [path for path in normalized if not is_generated_path_allowed(path)]
    if rejected:
        raise ReleaseError("Generated path allowlist rejected: " + ", ".join(rejected))
    if root is not None:
        root_path = Path(root).resolve()
        for relative in normalized:
            target = root_path.joinpath(*PurePosixPath(relative).parts)
            cursor = target
            while cursor != root_path:
                if cursor.is_symlink():
                    raise ReleaseError(f"Generated output path crosses a symlink: {relative!r} at {cursor}.")
                if cursor.parent == cursor:
                    break
                cursor = cursor.parent
            try:
                target.resolve(strict=False).relative_to(root_path)
            except ValueError as exc:
                raise ReleaseError(f"Generated output path escapes repository root: {relative!r}.") from exc
    return sorted(normalized)


def hash_generated_outputs(root: Path | str, paths: Iterable[str] | None = None) -> dict[str, str]:
    root_path = Path(root).resolve()
    if paths is None:
        candidates: list[str] = []
        for current_root, directories, filenames in os.walk(root_path, followlinks=False):
            directories[:] = sorted(name for name in directories if name != ".git")
            current = Path(current_root)
            for directory in list(directories):
                candidate = current / directory
                relative = candidate.relative_to(root_path).as_posix()
                if candidate.is_symlink():
                    if is_generated_path_allowed(relative + "/placeholder"):
                        raise ReleaseError(f"Generated output contains a symlink directory: {relative}.")
                    directories.remove(directory)
            for filename in filenames:
                absolute = current / filename
                relative = absolute.relative_to(root_path).as_posix()
                if absolute.is_symlink():
                    if is_generated_path_allowed(relative):
                        raise ReleaseError(f"Generated output contains a symlink file: {relative}.")
                    continue
                if is_generated_path_allowed(relative) and relative not in HASH_EXCLUDED_PATHS:
                    candidates.append(relative)
        paths = candidates
    safe_paths = validate_generated_paths(paths, root=root_path)
    result: dict[str, str] = {}
    for relative in safe_paths:
        if relative in HASH_EXCLUDED_PATHS:
            continue
        source = root_path.joinpath(*PurePosixPath(relative).parts)
        if not source.is_file():
            raise ReleaseError(f"Generated output does not exist or is not a regular file: {relative}.")
        result[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    return result


def render_checksums(output_hashes: Mapping[str, str]) -> str:
    lines: list[str] = []
    for path, digest in sorted(output_hashes.items()):
        normalized = normalize_generated_path(path)
        if not HEX_SHA256_RE.fullmatch(digest):
            raise ReleaseError(f"Invalid SHA-256 digest for generated output {path!r}.")
        if not is_generated_path_allowed(normalized):
            raise ReleaseError(f"Checksums cannot include a path outside the generated allowlist: {normalized}.")
        lines.append(f"{digest}  {normalized}")
    return "\n".join(lines) + ("\n" if lines else "")


def _checksums_text_blockers(checksums_text: str | None, output_hashes: Mapping[str, str]) -> list[str]:
    if checksums_text is None:
        return ["generated checksums file is missing or could not be read safely"]
    try:
        expected = render_checksums(output_hashes)
    except ReleaseError as exc:
        return [f"generated checksums cannot be verified: {exc}"]
    if checksums_text != expected:
        return ["generated checksums file does not exactly match the current output hashes"]
    return []


def _checksums_file_blockers(root: Path | str, output_hashes: Mapping[str, str]) -> list[str]:
    target = Path(root).joinpath(*PurePosixPath(CHECKSUMS_PATH).parts)
    try:
        _reject_symlink_ancestors(target)
        if target.is_symlink():
            return ["generated checksums file is a symlink and cannot be verified"]
        checksums_text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ReleaseError):
        return _checksums_text_blockers(None, output_hashes)
    return _checksums_text_blockers(checksums_text, output_hashes)


def _trusted_mapping_hash(root: Path | str) -> str:
    mapping_root = Path(root).resolve() / "mappings"
    if mapping_root.is_symlink() or not mapping_root.is_dir():
        raise ReleaseError("Protected baseline is missing the regular mappings directory.")
    parts: list[bytes] = []
    files = sorted(mapping_root.rglob("*"))
    for path in files:
        if path.is_symlink():
            raise ReleaseError(f"Protected baseline mapping tree contains a symlink: {path}.")
        if path.is_file():
            parts.extend((path.relative_to(mapping_root).as_posix().encode(), path.read_bytes()))
    if not parts:
        raise ReleaseError("Protected baseline mappings directory contains no regular files.")
    return hashlib.sha256(b"".join(parts)).hexdigest()


def _trusted_scenario_hash(root: Path | str) -> str:
    """Bind the acceptance contract and both channel-specific host definitions."""
    parts = []
    for relative in (
        "schemas/runtime-acceptance.json", "tests/codex-host/stable.json", "tests/codex-host/edge.json",
    ):
        path = Path(root).resolve() / relative
        _reject_symlink_ancestors(path)
        if not path.is_file():
            raise ReleaseError(f"Protected baseline is missing the regular {relative} file.")
        parts.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def write_checksums(root: Path | str, *, output_path: str = CHECKSUMS_PATH) -> dict[str, str]:
    root_path = Path(root).resolve()
    output_hashes = hash_generated_outputs(root_path)
    normalized = normalize_generated_path(output_path)
    if normalized != CHECKSUMS_PATH:
        raise ReleaseError(f"Checksums output path is fixed at {CHECKSUMS_PATH!r}.")
    target = root_path.joinpath(*PurePosixPath(normalized).parts)
    _reject_symlink_ancestors(target)
    if target.is_symlink():
        raise ReleaseError(f"Refusing to overwrite a symlink as the checksums file: {target}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_checksums(output_hashes), encoding="utf-8", newline="\n")
    return output_hashes


def copy_generated_outputs(source: Path | str, root: Path | str, *, prune: bool = False) -> dict[str, Any]:
    """Copy only allowlisted regular files from a converter output directory.

    This function never follows symlinks. With ``prune=True`` it removes stale
    files only from allowlisted output locations in the target repository.
    """
    source_input = Path(source)
    if source_input.is_symlink():
        raise ReleaseError(f"Converter output must not be a symlink: {source}.")
    source_root = source_input.resolve()
    target_root = Path(root).resolve()
    if not source_root.is_dir():
        raise ReleaseError(f"Converter output must be a regular directory: {source}.")
    found: list[str] = []
    for current_root, directories, filenames in os.walk(source_root, topdown=True, followlinks=False):
        current = Path(current_root)
        for directory in list(directories):
            candidate = current / directory
            if candidate.is_symlink():
                raise ReleaseError(f"Converter output contains a symlink directory: {candidate}.")
        directories[:] = sorted(directories)
        for filename in filenames:
            candidate = current / filename
            if candidate.is_symlink() or not candidate.is_file():
                raise ReleaseError(f"Converter output contains a symlink or non-regular file: {candidate}.")
            relative = candidate.relative_to(source_root).as_posix()
            found.append(relative)
    safe_found = validate_generated_paths(found, root=source_root)
    reserved = sorted(set(safe_found) & HASH_EXCLUDED_PATHS)
    reserved.extend(
        path
        for path in safe_found
        if any(path.startswith(prefix) for prefix in CONVERTER_RESERVED_PATHS)
    )
    reserved = sorted(set(reserved))
    if reserved:
        raise ReleaseError("Converter output cannot create reserved release-state files: " + ", ".join(reserved))
    if not safe_found:
        return {"copied": [], "removed": []}

    old_files: list[str] = []
    if prune and target_root.exists():
        for current_root, directories, filenames in os.walk(target_root, topdown=True, followlinks=False):
            current = Path(current_root)
            directories[:] = [
                directory
                for directory in sorted(directories)
                if directory != ".git" and not (current / directory).is_symlink()
            ]
            for filename in filenames:
                candidate = current / filename
                if candidate.is_symlink():
                    continue
                relative = candidate.relative_to(target_root).as_posix()
                if (
                    is_generated_path_allowed(relative)
                    and relative not in safe_found
                    and relative != str(DEFAULT_LEDGER).replace("\\", "/")
                ):
                    old_files.append(relative)
    for relative in safe_found:
        from_path = source_root.joinpath(*PurePosixPath(relative).parts)
        to_path = target_root.joinpath(*PurePosixPath(relative).parts)
        validate_generated_paths([relative], root=target_root)
        to_path.parent.mkdir(parents=True, exist_ok=True)
        to_path.write_bytes(from_path.read_bytes())
    removed: list[str] = []
    for relative in sorted(old_files):
        target = target_root.joinpath(*PurePosixPath(relative).parts)
        if target.is_file() and not target.is_symlink():
            target.unlink()
            removed.append(relative)
    return {"copied": safe_found, "removed": removed}


def _read_json(path: Path | str, *, label: str) -> dict[str, Any]:
    file_path = Path(path)
    _reject_symlink_ancestors(file_path)
    if file_path.is_symlink():
        raise ReleaseError(f"Refusing to read {label} through a symlink: {file_path}.")
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"Cannot read {label} {file_path}: {exc}.") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} {file_path} must contain a JSON object.")
    return value


def _static_blockers(report: Mapping[str, Any], *, channel: str, runtime_evidence_supplied: bool) -> list[str]:
    blockers: list[str] = []
    for item in report.get("release_blockers", []) or []:
        code = item.get("code") if isinstance(item, dict) else None
        if code == "runtime-evidence-missing" and (channel != "stable" or runtime_evidence_supplied):
            continue
        blockers.append(f"compatibility report: {item}")
    if report.get("unknown_fields"):
        blockers.append("compatibility report contains unknown behavior-bearing fields requiring manual review")
    if report.get("unresolved_references"):
        blockers.append("compatibility report contains unresolved references")
    for artifact in report.get("artifacts", []) or []:
        if isinstance(artifact, dict) and artifact.get("status") == "unresolved":
            blockers.append(f"unresolved artifact: {artifact.get('source', '<unknown>')}")
    counts = report.get("counts", {})
    coverage = report.get("coverage", {})
    if not isinstance(coverage, dict) or coverage.get("inventory") != "complete":
        blockers.append("compatibility report inventory coverage is not complete")
    if coverage.get("structural") != "passed":
        blockers.append("compatibility report structural validation is not passed")
    for kind in ("skills", "agents", "instructions", "prompts"):
        entry = counts.get(kind, {}) if isinstance(counts, dict) else {}
        if isinstance(entry, dict) and "discovered" in entry and "represented" in entry:
            if entry.get("discovered") != entry.get("represented"):
                blockers.append(
                    f"{kind} inventory mismatch: discovered {entry.get('discovered')}, represented {entry.get('represented')}"
                )
    return blockers


def validate_runtime_evidence(
    evidence: Mapping[str, Any],
    *,
    channel: str,
    source_sha: str,
    adapter_sha: str,
    converter_sha: str,
    mapping_sha256: str,
    scenario_definition_sha256: str,
    output_hashes: Mapping[str, str],
    vally_verification: Mapping[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if channel != "stable":
        return blockers
    if evidence.get("schema_version") != 2:
        blockers.append("runtime evidence must use acceptance contract version 2")
    if not vally_verification or vally_verification.get("verified") is not True:
        blockers.append("runtime evidence requires independent file-backed full-suite Vally verification")
    if evidence.get("explicitly_invoked") is not True:
        blockers.append("runtime evaluation was not explicitly invoked")
    if evidence.get("result") != "passed":
        blockers.append("runtime evaluation result is not passed")
    if not isinstance(evidence.get("codex_version"), str) or not evidence.get("codex_version", "").strip():
        blockers.append("runtime evidence must record the Codex CLI or app version")
    for name, expected in (
        ("source_sha", source_sha),
        ("adapter_sha", adapter_sha),
        ("converter_sha", converter_sha),
    ):
        try:
            actual = validate_git_sha(str(evidence.get(name, "")), field=f"runtime evidence {name}")
        except ReleaseError as exc:
            blockers.append(str(exc))
            continue
        if actual != expected:
            blockers.append(f"runtime evidence {name} does not match the candidate build")
    for name, expected in (
        ("mapping_sha256", mapping_sha256),
        ("scenario_definition_sha256", scenario_definition_sha256),
    ):
        actual = evidence.get(name)
        if not isinstance(actual, str) or not HEX_SHA256_RE.fullmatch(actual):
            blockers.append(f"runtime evidence {name} must be a SHA-256 digest")
        elif actual != expected:
            blockers.append(f"runtime evidence {name} does not match the candidate build")
    surfaces = evidence.get("surfaces")
    if not isinstance(surfaces, dict):
        blockers.append("runtime evidence must include Codex CLI and Desktop host surface results")
        surfaces = {}
    for surface in RUNTIME_SURFACES:
        detail = surfaces.get(surface)
        if not isinstance(detail, dict):
            blockers.append(f"runtime evidence is missing the {surface.upper()} surface")
            continue
        if detail.get("result") != "passed":
            blockers.append(f"runtime evidence {surface.upper()} surface did not pass")
        if not isinstance(detail.get("version"), str) or not detail.get("version", "").strip():
            blockers.append(f"runtime evidence must record the {surface.upper()} version")
        evidence_path = detail.get("evidence_path")
        evidence_hash = detail.get("evidence_sha256")
        try:
            normalized_evidence_path = normalize_generated_path(evidence_path)
        except ReleaseError:
            normalized_evidence_path = ""
        if (
            not normalized_evidence_path
            or not is_generated_path_allowed(normalized_evidence_path)
            or not normalized_evidence_path.startswith("generated/runtime-evidence/")
        ):
            blockers.append(f"runtime evidence {surface.upper()} report path is missing or unsafe")
        if not isinstance(evidence_hash, str) or not HEX_SHA256_RE.fullmatch(evidence_hash):
            blockers.append(f"runtime evidence {surface.upper()} report hash must be a SHA-256 digest")
        elif output_hashes.get(normalized_evidence_path) != evidence_hash:
            blockers.append(f"runtime evidence {surface.upper()} report hash does not match release output hashes")
        human_review = detail.get("human_review")
        if not isinstance(human_review, dict):
            blockers.append(f"runtime evidence is missing reviewer-authored {surface.upper()} rubric approval")
            continue
        if not isinstance(human_review.get("reviewer"), str) or not human_review.get("reviewer", "").strip():
            blockers.append(f"runtime evidence {surface.upper()} human review must name the reviewer")
        if human_review.get("result") != "passed":
            blockers.append(f"runtime evidence {surface.upper()} human rubric review is not passed")
        reviewed_at = human_review.get("reviewed_at")
        if not isinstance(reviewed_at, str) or not reviewed_at.strip():
            blockers.append(f"runtime evidence {surface.upper()} human review must record reviewed_at")
        else:
            try:
                _parse_time(reviewed_at)
            except ReleaseError:
                blockers.append(f"runtime evidence {surface.upper()} human review has an invalid reviewed_at timestamp")
        reviewed_scenarios = human_review.get("scenario_ids")
        if (
            not isinstance(reviewed_scenarios, list)
            or len(reviewed_scenarios) != len(RUNTIME_SCENARIOS)
            or any(not isinstance(item, str) for item in reviewed_scenarios)
            or set(reviewed_scenarios) != set(RUNTIME_SCENARIOS)
        ):
            blockers.append(f"runtime evidence {surface.upper()} human review must attest every required host check")
        if human_review.get("scenario_definition_sha256") != scenario_definition_sha256:
            blockers.append(f"runtime evidence {surface.upper()} human review scenario-definition hash does not match")
        if human_review.get("evidence_sha256") != evidence_hash:
            blockers.append(f"runtime evidence {surface.upper()} human review is not bound to its report hash")
    scenarios = evidence.get("scenarios", [])
    if not isinstance(scenarios, list):
        blockers.append("runtime evidence scenarios must be a list")
        scenarios = []
    by_id: dict[str, dict[str, Any]] = {}
    for item in scenarios:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            blockers.append("runtime evidence contains a malformed scenario record")
            continue
        scenario_id = item["id"]
        if scenario_id in by_id:
            blockers.append(f"runtime evidence contains a duplicate scenario: {scenario_id}")
        by_id[scenario_id] = item
    missing = [scenario for scenario in RUNTIME_SCENARIOS if scenario not in by_id]
    if missing:
        blockers.append("runtime evidence is missing scenarios: " + ", ".join(missing))
    for surface in RUNTIME_SURFACES:
        failed = [
            scenario
            for scenario in RUNTIME_SCENARIOS
            if scenario in by_id
            and (not isinstance(by_id[scenario].get("surfaces"), dict)
                 or by_id[scenario]["surfaces"].get(surface) != "passed")
        ]
        if failed:
            blockers.append(f"{surface.upper()} runtime scenarios did not pass: " + ", ".join(failed))
    evidence_hashes = evidence.get("output_hashes")
    if not isinstance(evidence_hashes, dict) or dict(sorted(evidence_hashes.items())) != dict(sorted(output_hashes.items())):
        blockers.append("runtime evidence output hashes do not match the release candidate outputs")
    return blockers


def publication_eligibility(
    *,
    channel: str,
    compatibility_report: Mapping[str, Any],
    source_sha: str,
    adapter_sha: str,
    converter_sha: str,
    output_hashes: Mapping[str, str],
    mapping_sha256: str | None = None,
    scenario_definition_sha256: str | None = None,
    runtime_evidence: Mapping[str, Any] | None = None,
    runtime_evidence_sha256: str | None = None,
    explicitly_opted_in: bool = False,
    upstream_prerelease: bool = False,
    merged_commit: str | None = None,
    protected_branch: bool = False,
    vally_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute an explicit channel policy result without performing publication."""
    channel = _validate_channel(channel)
    source_sha = validate_git_sha(source_sha, field="source SHA")
    adapter_sha = validate_git_sha(adapter_sha, field="adapter SHA")
    converter_sha = validate_git_sha(converter_sha, field="converter SHA")
    blockers = _static_blockers(
        compatibility_report,
        channel=channel,
        runtime_evidence_supplied=runtime_evidence is not None,
    )
    if compatibility_report.get("upstream_sha") != source_sha:
        blockers.append("compatibility report upstream SHA does not match the candidate source SHA")
    if compatibility_report.get("adapter_revision") != adapter_sha:
        blockers.append("compatibility report adapter revision does not match the candidate adapter SHA")
    report_mapping_hash = compatibility_report.get("mapping_hash")
    if not isinstance(mapping_sha256, str) or not HEX_SHA256_RE.fullmatch(mapping_sha256):
        blockers.append("release candidate requires a SHA-256 mapping digest")
    elif report_mapping_hash != mapping_sha256:
        blockers.append("compatibility report mapping hash does not match the release manifest")
    if not isinstance(scenario_definition_sha256, str) or not HEX_SHA256_RE.fullmatch(scenario_definition_sha256):
        blockers.append("release candidate requires a SHA-256 scenario-definition digest")
    if not output_hashes:
        blockers.append("no generated output hashes were provided")
    for path, digest in output_hashes.items():
        if not is_generated_path_allowed(path):
            blockers.append(f"generated path is outside the allowlist: {path}")
        if not HEX_SHA256_RE.fullmatch(str(digest)):
            blockers.append(f"generated output has an invalid SHA-256 digest: {path}")
    if channel == "stable":
        if not runtime_evidence:
            blockers.append("stable publication requires runtime evaluation evidence")
        else:
            if not runtime_evidence_sha256 or not HEX_SHA256_RE.fullmatch(runtime_evidence_sha256):
                blockers.append("stable publication requires a SHA-256 hash of the runtime evidence file")
            blockers.extend(
                validate_runtime_evidence(
                    runtime_evidence,
                    channel=channel,
                    source_sha=source_sha,
                    adapter_sha=adapter_sha,
                    converter_sha=converter_sha,
                    mapping_sha256=mapping_sha256 or "",
                    scenario_definition_sha256=scenario_definition_sha256 or "",
                    output_hashes=output_hashes,
                    vally_verification=vally_verification,
                )
            )
        if not merged_commit:
            blockers.append("stable publication is only allowed after a validated merge")
        else:
            try:
                validate_git_sha(merged_commit, field="merged commit")
            except ReleaseError as exc:
                blockers.append(str(exc))
        if not protected_branch:
            blockers.append("stable publication must run from a protected release branch")
        eligible = not blockers
        return {
            "channel": channel,
            "eligible": eligible,
            "status": "eligible" if eligible else "blocked",
            "blockers": blockers,
            "publication": "stable-release" if eligible else None,
        }
    if channel == "prerelease":
        if not upstream_prerelease:
            blockers.append("prerelease channel requires an upstream prerelease tag")
        if not explicitly_opted_in:
            blockers.append("prerelease publication requires explicit opt-in")
        if not merged_commit:
            blockers.append("prerelease publication is only allowed after a validated merge")
        else:
            try:
                validate_git_sha(merged_commit, field="merged commit")
            except ReleaseError as exc:
                blockers.append(str(exc))
        if not protected_branch:
            blockers.append("prerelease publication must run from a protected release branch")
        eligible = not blockers
        return {
            "channel": channel,
            "eligible": eligible,
            "status": "eligible" if eligible else "blocked",
            "blockers": blockers,
            "publication": "opt-in-prerelease" if eligible else None,
        }
    if not explicitly_opted_in:
        blockers.append("edge snapshot publication requires explicit opt-in")
    if not merged_commit:
        blockers.append("edge snapshot publication is only allowed after a validated merge")
    else:
        try:
            validate_git_sha(merged_commit, field="merged commit")
        except ReleaseError as exc:
            blockers.append(str(exc))
    if not protected_branch:
        blockers.append("edge snapshot publication must run from a protected release branch")
    eligible = not blockers
    return {
        "channel": channel,
        "eligible": eligible,
        "status": "development-snapshot-only" if eligible else "blocked",
        "blockers": blockers,
        "publication": "edge-development-snapshot" if eligible else None,
    }


def create_release_manifest(
    *,
    channel: str,
    source_ref: str,
    source_sha: str,
    upstream_manifest_version: str,
    adapter_sha: str,
    converter_sha: str,
    converter_version: str,
    output_hashes: Mapping[str, str],
    mapping_sha256: str | None = None,
    scenario_definition_sha256: str | None = None,
    upstream_prerelease: bool = False,
    runtime_evidence_path: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    channel = _validate_channel(channel)
    source_sha = validate_git_sha(source_sha, field="source SHA")
    adapter_sha = validate_git_sha(adapter_sha, field="adapter SHA")
    converter_sha = validate_git_sha(converter_sha, field="converter SHA")
    source_ref = validate_source_ref(source_ref)
    if mapping_sha256 is not None and not HEX_SHA256_RE.fullmatch(mapping_sha256):
        raise ReleaseError("Mapping hash must be a 64-character SHA-256 digest.")
    if scenario_definition_sha256 is not None and not HEX_SHA256_RE.fullmatch(scenario_definition_sha256):
        raise ReleaseError("Scenario-definition hash must be a 64-character SHA-256 digest.")
    normalized_hashes: dict[str, str] = {}
    for path, digest in output_hashes.items():
        normalized = normalize_generated_path(path)
        if not is_generated_path_allowed(normalized) or normalized in HASH_EXCLUDED_PATHS:
            raise ReleaseError(f"Release output hash path is not an eligible generated artifact: {path!r}.")
        if not HEX_SHA256_RE.fullmatch(str(digest)):
            raise ReleaseError(f"Release output hash is not a SHA-256 digest: {path!r}.")
        normalized_hashes[normalized] = str(digest)
    runtime_hash = None
    if runtime_evidence_path:
        path = Path(runtime_evidence_path)
        if path.is_symlink() or not path.is_file():
            raise ReleaseError(f"Runtime evidence must be a regular file, not a symlink: {path}.")
        runtime_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "channel": channel,
        "source": {"repository": f"{DEFAULT_OWNER}/{DEFAULT_REPOSITORY}", "ref": source_ref, "sha": source_sha},
        "upstream_manifest_version": upstream_manifest_version,
        "upstream_prerelease": bool(upstream_prerelease),
        "converter": {"version": converter_version, "sha": converter_sha},
        "adapter_sha": adapter_sha,
        "mapping_sha256": mapping_sha256,
        "scenario_definition_sha256": scenario_definition_sha256,
        "generated_at": generated_at or _utc_now(),
        "output_hashes": dict(sorted(normalized_hashes.items())),
        "runtime_evidence_sha256": runtime_hash,
    }


def write_release_manifest(root: Path | str, manifest: Mapping[str, Any]) -> None:
    root_path = Path(root).resolve()
    path = root_path.joinpath(*PurePosixPath(RELEASE_MANIFEST_PATH).parts)
    _reject_symlink_ancestors(path)
    if path.is_symlink():
        raise ReleaseError(f"Refusing to overwrite a symlink as the release manifest: {path}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def build_release_manifest(
    *,
    root: Path | str,
    channel: str,
    source_ref: str,
    source_sha: str,
    upstream_manifest_version: str,
    adapter_sha: str,
    converter_sha: str,
    converter_version: str,
    mapping_sha256: str | None = None,
    scenario_definition_sha256: str | None = None,
    upstream_prerelease: bool = False,
    runtime_evidence_path: Path | str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    output_hashes = hash_generated_outputs(root_path)
    manifest = create_release_manifest(
        channel=channel,
        source_ref=source_ref,
        source_sha=source_sha,
        upstream_manifest_version=upstream_manifest_version,
        adapter_sha=adapter_sha,
        converter_sha=converter_sha,
        converter_version=converter_version,
        output_hashes=output_hashes,
        mapping_sha256=mapping_sha256,
        scenario_definition_sha256=scenario_definition_sha256,
        upstream_prerelease=upstream_prerelease,
        runtime_evidence_path=runtime_evidence_path,
        generated_at=generated_at,
    )
    write_release_manifest(root_path, manifest)
    write_checksums(root_path)
    return manifest


def refresh_release_manifest(
    *,
    root: Path | str,
    runtime_evidence_path: Path | str,
) -> dict[str, Any]:
    """Refresh hashes and explicit runtime-evidence provenance before review.

    This command is intended for the reviewed evidence change after a human has
    run model-based evaluation. It preserves the original source, converter,
    and generation timestamp while recalculating output hashes from the tree.
    """
    root_path = Path(root).resolve()
    manifest_path = root_path.joinpath(*PurePosixPath(RELEASE_MANIFEST_PATH).parts)
    current = _read_json(manifest_path, label="release manifest")
    try:
        source = current["source"]
        converter = current["converter"]
        if not isinstance(source, dict) or not isinstance(converter, dict):
            raise TypeError("source and converter must be objects")
        manifest = build_release_manifest(
            root=root_path,
            channel=current["channel"],
            source_ref=source["ref"],
            source_sha=source["sha"],
            upstream_manifest_version=current["upstream_manifest_version"],
            adapter_sha=current["adapter_sha"],
            converter_sha=converter["sha"],
            converter_version=converter["version"],
            mapping_sha256=current["mapping_sha256"],
            scenario_definition_sha256=current["scenario_definition_sha256"],
            upstream_prerelease=current.get("upstream_prerelease") is True,
            runtime_evidence_path=runtime_evidence_path,
            generated_at=current["generated_at"],
        )
    except (KeyError, TypeError) as exc:
        raise ReleaseError(f"Existing release manifest is malformed and cannot be refreshed: {exc}.") from exc
    return manifest


def _cmd_discover(args: argparse.Namespace) -> dict[str, Any]:
    ledger = load_ledger(args.ledger)
    token = os.environ.get(args.token_env) if args.token_env else None
    sources = discover_sources(
        args.channel,
        api_base=args.api_base,
        token=token,
        owner=args.owner,
        repository=args.repository,
        ledger=ledger,
        limit=args.limit,
    )
    return {"channel": args.channel, "count": len(sources), "sources": sources}


def _cmd_record(args: argparse.Namespace) -> dict[str, Any]:
    ledger = load_ledger(args.ledger)
    updated, changed = record_processed(
        ledger,
        channel=args.channel,
        source_ref=args.source_ref,
        source_sha=args.source_sha,
        tag=args.tag,
        outcome=args.outcome,
    )
    write_ledger(args.ledger, updated)
    return {"ledger": str(args.ledger), "changed": changed, "entry_count": len(updated["processed"])}


def _cmd_paths(args: argparse.Namespace) -> dict[str, Any]:
    requested = list(args.paths or [])
    if args.paths_file:
        _reject_symlink_ancestors(Path(args.paths_file))
        contents = Path(args.paths_file).read_bytes()
        if b"\x00" in contents:
            requested.extend(part.decode("utf-8", errors="strict") for part in contents.split(b"\x00") if part)
        else:
            requested.extend(line for line in contents.decode("utf-8").splitlines() if line)
    if not requested:
        raise ReleaseError("Provide at least one path directly or through --paths-file.")
    paths = validate_generated_paths(requested, root=args.root)
    return {"allowed": True, "paths": paths}


def _cmd_checksums(args: argparse.Namespace) -> dict[str, Any]:
    hashes = write_checksums(args.root)
    return {"checksums": str(Path(args.root) / CHECKSUMS_PATH), "count": len(hashes), "output_hashes": hashes}


def _cmd_copy_outputs(args: argparse.Namespace) -> dict[str, Any]:
    return copy_generated_outputs(args.source, args.root, prune=args.prune)


def _cmd_manifest(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_release_manifest(
        root=args.root,
        channel=args.channel,
        source_ref=args.source_ref,
        source_sha=args.source_sha,
        upstream_manifest_version=args.upstream_manifest_version,
        adapter_sha=args.adapter_sha,
        converter_sha=args.converter_sha,
        converter_version=args.converter_version,
        mapping_sha256=args.mapping_sha256,
        scenario_definition_sha256=args.scenario_definition_sha256,
        upstream_prerelease=args.upstream_prerelease,
        runtime_evidence_path=args.runtime_evidence,
        generated_at=args.generated_at,
    )
    return manifest


def _cmd_refresh_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return refresh_release_manifest(root=args.root, runtime_evidence_path=args.runtime_evidence)


def _cmd_eligibility(args: argparse.Namespace) -> dict[str, Any]:
    report = _read_json(args.compatibility_report, label="compatibility report")
    manifest = _read_json(args.release_manifest, label="release manifest")
    evidence = _read_json(args.runtime_evidence, label="runtime evidence") if args.runtime_evidence else None
    evidence_sha = None
    if args.runtime_evidence:
        evidence_path = Path(args.runtime_evidence)
        evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    hash_blockers: list[str] = []
    source = manifest.get("source", {})
    converter = manifest.get("converter", {})
    if not isinstance(source, dict):
        source = {}
        hash_blockers.append("release manifest source metadata is malformed")
    if not isinstance(converter, dict):
        converter = {}
        hash_blockers.append("release manifest converter metadata is malformed")
    if manifest.get("channel") != args.channel:
        hash_blockers.append("release manifest channel does not match the requested publication channel")
    if args.channel == "prerelease":
        if not args.upstream_prerelease:
            hash_blockers.append("prerelease eligibility requires an explicit upstream-prerelease assertion")
        if manifest.get("upstream_prerelease") is not True:
            hash_blockers.append("release manifest does not record an upstream prerelease source")
    if source.get("repository") != f"{DEFAULT_OWNER}/{DEFAULT_REPOSITORY}":
        hash_blockers.append("release manifest does not identify the expected upstream repository")
    expected_hashes = manifest.get("output_hashes", {})
    current_hashes = hash_generated_outputs(args.root)
    if not isinstance(expected_hashes, dict) or dict(sorted(expected_hashes.items())) != dict(sorted(current_hashes.items())):
        hash_blockers.append("release manifest output hashes do not match the current generated files")
    hash_blockers.extend(_checksums_file_blockers(args.root, current_hashes))
    if args.channel == "stable" and manifest.get("runtime_evidence_sha256") != evidence_sha:
        hash_blockers.append("release manifest runtime evidence hash does not match the supplied evidence file")
    vally_verification = None
    if args.channel == "stable" and evidence is not None:
        from .runtime_evidence import verify_candidate_vally
        approved_adapter = Path(__file__).resolve().parents[2] / "vally-executor"
        if not approved_adapter.is_dir():
            approved_adapter = Path(__file__).parent / "data/vally-executor"
        vally_verification = verify_candidate_vally(
            candidate_root=args.root, approved_adapter_root=approved_adapter,
            evidence=evidence, expected_source_sha=source.get("sha", ""),
            expected_custom_root=approved_adapter.parent / "tests/vally-custom" / args.channel,
        )
        hash_blockers.extend(vally_verification.get("blockers", []))
    decision = publication_eligibility(
        channel=args.channel,
        compatibility_report=report,
        source_sha=source.get("sha", ""),
        adapter_sha=manifest.get("adapter_sha", ""),
        converter_sha=converter.get("sha", ""),
        output_hashes=expected_hashes if isinstance(expected_hashes, dict) else {},
        mapping_sha256=manifest.get("mapping_sha256"),
        scenario_definition_sha256=manifest.get("scenario_definition_sha256"),
        runtime_evidence=evidence,
        runtime_evidence_sha256=evidence_sha,
        explicitly_opted_in=args.opt_in,
        upstream_prerelease=(args.upstream_prerelease and manifest.get("upstream_prerelease") is True),
        merged_commit=args.merged_commit,
        protected_branch=args.protected_branch,
        vally_verification=vally_verification,
    )
    decision["blockers"].extend(hash_blockers)
    if hash_blockers:
        decision["eligible"] = False
        decision["status"] = "blocked"
        decision["publication"] = None
    if not decision["eligible"]:
        # Return the complete decision to scripts while preserving a failing exit.
        decision["exit_failure"] = True
    return decision


def _inventory_artifacts_by_id(inventory: Mapping[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    artifacts = inventory.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReleaseError(f"{label} source inventory must contain an artifacts list.")
    indexed: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("id"), str) or not artifact["id"]:
            raise ReleaseError(f"{label} source inventory contains a malformed artifact record.")
        artifact_id = artifact["id"]
        if artifact_id in indexed:
            raise ReleaseError(f"{label} source inventory contains duplicate artifact ID {artifact_id!r}.")
        indexed[artifact_id] = artifact
    return indexed


_LICENSE_PATH_MARKERS = (
    "license",
    "licence",
    "notice",
    "copying",
    "copyright",
)
_GOVERNANCE_TEXT_MARKERS = (
    "security",
    "privacy",
    "license",
    "licence",
    "copyright",
    "attribution",
    "secret",
    "credential",
    "permission",
    "authorization",
    "authorisation",
    "approval",
    "authorize",
    "authorise",
    "human review",
    "manual review",
    "handoff",
    "read-only",
    "read only",
    "write access",
    "explicit approval",
    "ask the user",
    "must be authorized",
    "must be authorised",
)


def _license_file_signature(inventory: Mapping[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    files = inventory.get("files")
    if not isinstance(files, list):
        raise ReleaseError(f"{label} source inventory must contain a files list for license checks.")
    signature: dict[str, dict[str, Any]] = {}
    for record in files:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ReleaseError(f"{label} source inventory contains a malformed file record.")
        path = record["path"]
        if any(marker in path.casefold() for marker in _LICENSE_PATH_MARKERS):
            # Keep the entire source-file record so newly introduced license or
            # notice metadata also requires review instead of slipping through.
            signature[path] = dict(record)
    return signature


def _artifact_risk_text(artifact: Mapping[str, Any], root: Path) -> tuple[str, list[str]]:
    pieces = [
        str(artifact.get("source", "")),
        str(artifact.get("name", "")),
        json.dumps(artifact.get("frontmatter", {}), ensure_ascii=False, sort_keys=True),
        json.dumps(artifact.get("required_tools", []), ensure_ascii=False, sort_keys=True),
        json.dumps(artifact.get("required_capabilities", []), ensure_ascii=False, sort_keys=True),
    ]
    errors: list[str] = []
    outputs = artifact.get("outputs", [])
    if not isinstance(outputs, list):
        return "\n".join(pieces), ["artifact output list is malformed"]
    for output in outputs:
        if not isinstance(output, str):
            errors.append("artifact output path is malformed")
            continue
        try:
            normalized = validate_generated_paths([output], root=root)[0]
        except ReleaseError as exc:
            errors.append(str(exc))
            continue
        path = root.joinpath(*PurePosixPath(normalized).parts)
        if not path.is_file():
            errors.append(f"generated artifact output is missing: {normalized}")
            continue
        try:
            pieces.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            errors.append(f"changed artifact output is not readable UTF-8 text: {normalized}")
    return "\n".join(pieces).casefold(), errors


def _supported_transformation_blockers(
    baseline_inventory: Mapping[str, Any],
    candidate_inventory: Mapping[str, Any],
    *,
    baseline_root: Path | None = None,
    candidate_root: Path | None = None,
) -> list[str]:
    """Require the candidate to use previously known artifact shapes and adapters.

    Upstream content hashes may change, but changes to inventory membership,
    conversion status, metadata schema, tools/capabilities, licensing, links,
    or output paths require human review before merge.
    """
    blockers: list[str] = []
    baseline = _inventory_artifacts_by_id(baseline_inventory, label="baseline")
    candidate = _inventory_artifacts_by_id(candidate_inventory, label="candidate")
    missing = sorted(set(baseline) - set(candidate))
    added = sorted(set(candidate) - set(baseline))
    if missing:
        blockers.append("upstream removed previously represented artifact(s): " + ", ".join(missing))
    if added:
        blockers.append("upstream added artifact(s) requiring manual review: " + ", ".join(added))

    try:
        baseline_licenses = _license_file_signature(baseline_inventory, label="baseline")
        candidate_licenses = _license_file_signature(candidate_inventory, label="candidate")
        if baseline_licenses != candidate_licenses:
            changed_license_paths = sorted(
                path
                for path in set(baseline_licenses) | set(candidate_licenses)
                if baseline_licenses.get(path) != candidate_licenses.get(path)
            )
            blockers.append(
                "upstream license, copyright, or third-party notice file changed: " + ", ".join(changed_license_paths)
            )
    except ReleaseError as exc:
        blockers.append(str(exc))

    for artifact_id in sorted(set(baseline) & set(candidate)):
        previous = baseline[artifact_id]
        current = candidate[artifact_id]
        # Ignore only source-content hashes: all known and future metadata fields
        # are reviewed semantics, including license metadata added by a newer
        # upstream schema. A changed or unknown field therefore requires a human.
        changed_fields = sorted(
            key
            for key in set(previous) | set(current)
            if key != "hash" and previous.get(key) != current.get(key)
        )
        if changed_fields:
            blockers.append(
                f"artifact {artifact_id!r} changed previously reviewed fields: " + ", ".join(changed_fields)
            )
        if previous.get("hash") != current.get("hash") and baseline_root is not None and candidate_root is not None:
            old_text, old_errors = _artifact_risk_text(previous, baseline_root)
            new_text, new_errors = _artifact_risk_text(current, candidate_root)
            errors = old_errors + new_errors
            if errors:
                blockers.append(f"artifact {artifact_id!r} changed with unreadable safety context: " + "; ".join(errors))
            elif any(marker in old_text or marker in new_text for marker in _GOVERNANCE_TEXT_MARKERS):
                blockers.append(
                    f"artifact {artifact_id!r} content changed in security, licensing, tool-scope, or human-gate context"
                )
        if current.get("status") not in {
            "converted",
            "adapted",
            "host-specific-alternative",
            "requires-external-service",
        }:
            blockers.append(f"artifact {artifact_id!r} has a non-automatable status {current.get('status')!r}")
    return blockers


def sync_auto_merge_eligibility(
    *,
    channel: str,
    compatibility_report: Mapping[str, Any],
    release_manifest: Mapping[str, Any],
    runtime_evidence: Mapping[str, Any] | None,
    runtime_evidence_sha256: str | None,
    output_hashes: Mapping[str, str],
    checksums_text: str | None,
    trusted_mapping_sha256: str | None,
    trusted_scenario_definition_sha256: str | None,
    baseline_inventory: Mapping[str, Any],
    candidate_inventory: Mapping[str, Any],
    baseline_root: Path | None = None,
    candidate_root: Path | None = None,
    expected_source_sha: str,
    expected_adapter_revision: str,
    source_recorded: bool,
    vally_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate sync auto-merge on explicit runtime evidence and known transformations.

    This does not merge a PR. It only returns an eligibility decision for a
    separately permissioned workflow. Any uncertainty is a manual-review veto.
    """
    channel = _validate_channel(channel)
    expected_source_sha = validate_git_sha(expected_source_sha, field="expected source SHA")
    expected_adapter_revision = validate_git_sha(
        expected_adapter_revision, field="expected adapter revision"
    )
    blockers = _static_blockers(
        compatibility_report,
        channel=channel,
        runtime_evidence_supplied=runtime_evidence is not None,
    )
    if not source_recorded:
        blockers.append("processed-source ledger does not record this exact source as a successfully opened PR")

    source = release_manifest.get("source")
    converter = release_manifest.get("converter")
    if not isinstance(source, dict) or not isinstance(converter, dict):
        blockers.append("release manifest source or converter metadata is malformed")
        source = source if isinstance(source, dict) else {}
        converter = converter if isinstance(converter, dict) else {}
    if release_manifest.get("schema_version") != 1:
        blockers.append("release manifest schema version is unsupported")
    if release_manifest.get("channel") != channel:
        blockers.append("release manifest channel does not match the sync channel")
    if source.get("repository") != f"{DEFAULT_OWNER}/{DEFAULT_REPOSITORY}":
        blockers.append("release manifest does not identify the expected upstream repository")
    if source.get("sha") != expected_source_sha or compatibility_report.get("upstream_sha") != expected_source_sha:
        blockers.append("candidate source SHA does not match the immutable sync branch identity")
    if compatibility_report.get("upstream_ref") != source.get("ref"):
        blockers.append("compatibility report upstream ref does not match the release manifest")
    if (
        compatibility_report.get("adapter_revision") != expected_adapter_revision
        or release_manifest.get("adapter_sha") != expected_adapter_revision
        or converter.get("sha") != expected_adapter_revision
    ):
        blockers.append("candidate adapter/converter revision does not match the immutable sync branch identity")

    manifest_hashes = release_manifest.get("output_hashes")
    if not isinstance(manifest_hashes, dict):
        blockers.append("release manifest output hashes are missing or malformed")
        manifest_hashes = {}
    normalized_manifest_hashes: dict[str, str] = {}
    for path, digest in manifest_hashes.items():
        if not isinstance(path, str) or not isinstance(digest, str):
            blockers.append("release manifest contains a malformed output hash record")
            continue
        if not is_generated_path_allowed(path) or not HEX_SHA256_RE.fullmatch(digest):
            blockers.append(f"release manifest contains an unsafe output hash record for {path!r}")
            continue
        normalized_manifest_hashes[path] = digest
    if not normalized_manifest_hashes:
        blockers.append("release manifest has no generated output hashes")
    if dict(sorted(normalized_manifest_hashes.items())) != dict(sorted(output_hashes.items())):
        blockers.append("release manifest output hashes do not match the candidate generated files")
    blockers.extend(_checksums_text_blockers(checksums_text, output_hashes))

    mapping_hash = release_manifest.get("mapping_sha256")
    scenario_hash = release_manifest.get("scenario_definition_sha256")
    if not isinstance(mapping_hash, str) or not HEX_SHA256_RE.fullmatch(mapping_hash):
        blockers.append("release manifest requires a SHA-256 mapping digest")
    elif compatibility_report.get("mapping_hash") != mapping_hash:
        blockers.append("compatibility report mapping hash does not match the release manifest")
    if not isinstance(trusted_mapping_sha256, str) or not HEX_SHA256_RE.fullmatch(trusted_mapping_sha256):
        blockers.append("protected baseline mapping inputs could not be hashed safely")
    elif mapping_hash != trusted_mapping_sha256:
        blockers.append("candidate mapping hash does not match the protected baseline mapping inputs")
    if not isinstance(scenario_hash, str) or not HEX_SHA256_RE.fullmatch(scenario_hash):
        blockers.append("release manifest requires a SHA-256 scenario-definition digest")
    if (
        not isinstance(trusted_scenario_definition_sha256, str)
        or not HEX_SHA256_RE.fullmatch(trusted_scenario_definition_sha256)
    ):
        blockers.append("protected baseline scenario definition could not be hashed safely")
    elif scenario_hash != trusted_scenario_definition_sha256:
        blockers.append("candidate scenario-definition hash does not match the protected baseline scenarios")

    if runtime_evidence is None:
        blockers.append("sync auto-merge requires explicitly recorded Codex CLI and Desktop runtime evidence")
    else:
        if not isinstance(runtime_evidence_sha256, str) or not HEX_SHA256_RE.fullmatch(runtime_evidence_sha256):
            blockers.append("sync auto-merge requires a SHA-256 hash of the runtime evidence file")
        elif release_manifest.get("runtime_evidence_sha256") != runtime_evidence_sha256:
            blockers.append("release manifest runtime evidence hash does not match the evidence envelope")
        blockers.extend(
            validate_runtime_evidence(
                runtime_evidence,
                channel="stable",
                source_sha=expected_source_sha,
                adapter_sha=expected_adapter_revision,
                converter_sha=expected_adapter_revision,
                mapping_sha256=mapping_hash if isinstance(mapping_hash, str) else "",
                scenario_definition_sha256=scenario_hash if isinstance(scenario_hash, str) else "",
                output_hashes=normalized_manifest_hashes,
                vally_verification=vally_verification,
            )
        )

    if baseline_inventory.get("channel") != channel or candidate_inventory.get("channel") != channel:
        blockers.append("baseline and candidate source inventories must match the sync channel")
    if candidate_inventory.get("upstream_sha") != expected_source_sha:
        blockers.append("candidate source inventory SHA does not match the immutable sync branch identity")
    if candidate_inventory.get("upstream_ref") != source.get("ref"):
        blockers.append("candidate source inventory ref does not match the release manifest")
    blockers.extend(
        _supported_transformation_blockers(
            baseline_inventory,
            candidate_inventory,
            baseline_root=baseline_root,
            candidate_root=candidate_root,
        )
    )

    eligible = not blockers
    return {
        "channel": channel,
        "eligible": eligible,
        "status": "eligible" if eligible else "manual-review-required",
        "blockers": blockers,
        "action": "schedule-auto-merge" if eligible else None,
    }


def _cmd_sync_eligibility(args: argparse.Namespace) -> dict[str, Any]:
    candidate_root = args.root.resolve()
    baseline_root = args.baseline_root.resolve()
    report = _read_json(candidate_root / "generated/compatibility-report.json", label="compatibility report")
    manifest = _read_json(candidate_root / RELEASE_MANIFEST_PATH, label="release manifest")
    evidence_path = candidate_root / RUNTIME_EVIDENCE_PATH
    evidence = _read_json(evidence_path, label="runtime evidence") if evidence_path.is_file() else None
    evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest() if evidence is not None else None
    current_hashes = hash_generated_outputs(candidate_root)
    checksums_path = candidate_root.joinpath(*PurePosixPath(CHECKSUMS_PATH).parts)
    try:
        _reject_symlink_ancestors(checksums_path)
        if checksums_path.is_symlink():
            checksums_text = None
        else:
            checksums_text = checksums_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ReleaseError):
        checksums_text = None
    try:
        trusted_mapping_sha256 = _trusted_mapping_hash(baseline_root)
    except (OSError, ReleaseError):
        trusted_mapping_sha256 = None
    try:
        trusted_scenario_definition_sha256 = _trusted_scenario_hash(baseline_root)
    except (OSError, ReleaseError):
        trusted_scenario_definition_sha256 = None
    baseline_inventory = _read_json(baseline_root / "generated/source-inventory.json", label="baseline source inventory")
    candidate_inventory = _read_json(candidate_root / "generated/source-inventory.json", label="candidate source inventory")
    source = manifest.get("source")
    source_ref = source.get("ref") if isinstance(source, dict) else None
    ledger = load_ledger(candidate_root / DEFAULT_LEDGER)
    source_recorded = any(
        entry.get("channel") == args.channel
        and entry.get("source_sha") == args.expected_source_sha
        and entry.get("source_ref") == source_ref
        and entry.get("outcome") == "pr-opened"
        for entry in ledger.get("processed", [])
    )
    vally_verification = None
    if evidence is not None:
        from .runtime_evidence import verify_candidate_vally
        vally_verification = verify_candidate_vally(
            candidate_root=candidate_root, approved_adapter_root=baseline_root / "vally-executor",
            evidence=evidence, expected_source_sha=args.expected_source_sha,
            expected_custom_root=baseline_root / "tests/vally-custom" / args.channel,
        )
    decision = sync_auto_merge_eligibility(
        channel=args.channel,
        compatibility_report=report,
        release_manifest=manifest,
        runtime_evidence=evidence,
        runtime_evidence_sha256=evidence_hash,
        output_hashes=current_hashes,
        checksums_text=checksums_text,
        trusted_mapping_sha256=trusted_mapping_sha256,
        trusted_scenario_definition_sha256=trusted_scenario_definition_sha256,
        baseline_inventory=baseline_inventory,
        candidate_inventory=candidate_inventory,
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        expected_source_sha=args.expected_source_sha,
        expected_adapter_revision=args.expected_adapter_revision,
        source_recorded=source_recorded,
        vally_verification=vally_verification,
    )
    if vally_verification and not vally_verification.get("verified"):
        decision["blockers"].extend(vally_verification.get("blockers", []))
        decision["eligible"] = False
        decision["status"] = "manual-review-required"
        decision["action"] = None
    if not decision["eligible"]:
        decision["exit_failure"] = True
    return decision


def add_release_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``release`` subcommands on the root CLI's argparse subparsers."""
    parser = subparsers.add_parser("release", help="Discover and gate immutable upstream release sources")
    commands = parser.add_subparsers(dest="release_command", required=True)

    discover = commands.add_parser("discover", help="List new immutable upstream sources")
    discover.add_argument("--channel", choices=CHANNELS, required=True)
    discover.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    discover.add_argument("--owner", default=DEFAULT_OWNER)
    discover.add_argument("--repository", default=DEFAULT_REPOSITORY)
    discover.add_argument("--api-base", default=DEFAULT_API_BASE)
    discover.add_argument("--token-env", default="GITHUB_TOKEN", help="Environment variable name for a read-only API token")
    discover.add_argument("--limit", type=int)
    discover.set_defaults(release_handler=_cmd_discover)

    record = commands.add_parser("record", help="Record a successfully opened sync PR or later release stage")
    record.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    record.add_argument("--channel", choices=CHANNELS, required=True)
    record.add_argument("--source-ref", required=True)
    record.add_argument("--source-sha", required=True)
    record.add_argument("--tag")
    record.add_argument("--outcome", choices=("pr-opened", "merged", "published"), required=True)
    record.set_defaults(release_handler=_cmd_record)

    paths = commands.add_parser("verify-paths", help="Validate converter output paths before bot staging")
    paths.add_argument("--root", type=Path)
    paths.add_argument("--paths-file", type=Path, help="Read newline- or NUL-separated paths from a file")
    paths.add_argument("paths", nargs="*")
    paths.set_defaults(release_handler=_cmd_paths)

    checksums = commands.add_parser("checksums", help="Hash allowlisted generated output and write checksums.txt")
    checksums.add_argument("--root", type=Path, default=Path.cwd())
    checksums.set_defaults(release_handler=_cmd_checksums)

    copy_outputs = commands.add_parser("copy-outputs", help="Copy converter output using the safe generated-path allowlist")
    copy_outputs.add_argument("--source", type=Path, required=True)
    copy_outputs.add_argument("--root", type=Path, default=Path.cwd())
    copy_outputs.add_argument("--prune", action="store_true", help="Remove stale files only under allowlisted generated paths")
    copy_outputs.set_defaults(release_handler=_cmd_copy_outputs)

    manifest = commands.add_parser("manifest", help="Write release metadata and deterministic SHA-256 checksums")
    manifest.add_argument("--root", type=Path, default=Path.cwd())
    manifest.add_argument("--channel", choices=CHANNELS, required=True)
    manifest.add_argument("--source-ref", required=True)
    manifest.add_argument("--source-sha", required=True)
    manifest.add_argument("--upstream-manifest-version", required=True)
    manifest.add_argument("--adapter-sha", required=True)
    manifest.add_argument("--converter-sha", required=True)
    manifest.add_argument("--converter-version", required=True)
    manifest.add_argument("--mapping-sha256")
    manifest.add_argument("--scenario-definition-sha256")
    manifest.add_argument("--upstream-prerelease", action="store_true")
    manifest.add_argument("--runtime-evidence", type=Path)
    manifest.add_argument("--generated-at")
    manifest.set_defaults(release_handler=_cmd_manifest)

    refresh = commands.add_parser(
        "refresh-manifest",
        help="Refresh the reviewed release manifest and checksums with explicit runtime evidence",
    )
    refresh.add_argument("--root", type=Path, default=Path.cwd())
    refresh.add_argument("--runtime-evidence", type=Path, required=True)
    refresh.set_defaults(release_handler=_cmd_refresh_manifest)

    eligibility = commands.add_parser("eligibility", help="Evaluate publication readiness from release evidence")
    eligibility.add_argument("--channel", choices=CHANNELS, required=True)
    eligibility.add_argument("--compatibility-report", type=Path, required=True)
    eligibility.add_argument("--release-manifest", type=Path, required=True)
    eligibility.add_argument("--runtime-evidence", type=Path)
    eligibility.add_argument("--upstream-prerelease", action="store_true")
    eligibility.add_argument("--opt-in", action="store_true")
    eligibility.add_argument("--merged-commit")
    eligibility.add_argument("--protected-branch", action="store_true")
    eligibility.add_argument("--root", type=Path, default=Path.cwd())
    eligibility.set_defaults(release_handler=_cmd_eligibility)

    sync_gate = commands.add_parser(
        "sync-eligibility",
        help="Evaluate strict evidence and compatibility gates before scheduling sync auto-merge",
    )
    sync_gate.add_argument("--channel", choices=CHANNELS, required=True)
    sync_gate.add_argument("--root", type=Path, default=Path.cwd(), help="Candidate PR checkout")
    sync_gate.add_argument("--baseline-root", type=Path, required=True, help="Protected target-branch checkout")
    sync_gate.add_argument("--expected-source-sha", required=True)
    sync_gate.add_argument("--expected-adapter-revision", required=True)
    sync_gate.set_defaults(release_handler=_cmd_sync_eligibility)
    return parser


def handle_release(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch a parsed release command; the root CLI serializes this result."""
    handler = getattr(args, "release_handler", None)
    if handler is None:
        raise ReleaseError("No release command was selected.")
    return handler(args)
