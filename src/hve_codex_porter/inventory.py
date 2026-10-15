"""Immutable source acquisition and complete source-tree reconciliation."""
from __future__ import annotations
import json
import hashlib
import os
import posixpath
import re
import stat
import subprocess
import unicodedata
from pathlib import Path
from .common import PortError, digest, frontmatter, safe_path
from .references import references

KNOWN_FIELDS = {
    "skill": {"name", "description", "license", "compatibility", "metadata", "allowed-tools", "user-invocable", "disable-model-invocation", "argument-hint"},
    "agent": {"name", "description", "agents", "tools", "handoffs", "argument-hint", "user-invocable", "disable-model-invocation", "model", "target", "mcp-servers"},
    "instruction": {"applyTo", "description", "name", "excludeAgent", "metadata"},
    "prompt": {"name", "description", "agent", "mode", "model", "tools", "argument-hint", "metadata"},
}

def git(root: Path, *args: str) -> str:
    cmd = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
    if os.name == "nt":
        cmd += ["-c", "http.sslBackend=schannel"]
    cmd += ["-C", str(root), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise PortError(f"Git failed ({' '.join(args)}): {result.stderr.strip()}")
    return result.stdout.strip()


def _git_bytes(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    cmd = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
    if os.name == "nt":
        cmd += ["-c", "http.sslBackend=schannel"]
    cmd += ["-C", str(root), *args]
    result = subprocess.run(cmd, capture_output=True, input=input_data)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise PortError(f"Git failed ({' '.join(args)}): {detail}")
    return result.stdout


def _git_blobs(root: Path, object_ids: set[str]) -> dict[str, bytes]:
    if not object_ids:
        return {}
    request = b"".join(object_id.encode("ascii") + b"\n" for object_id in sorted(object_ids))
    output = _git_bytes(root, "cat-file", "--batch", input_data=request)
    blobs = {}
    offset = 0
    while offset < len(output):
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise PortError("Git cat-file batch returned a malformed header")
        try:
            returned_id, object_type, raw_size = output[offset:header_end].split(b" ", 2)
            size = int(raw_size)
        except (ValueError, TypeError) as exc:
            raise PortError("Git cat-file batch returned a malformed object header") from exc
        body_start = header_end + 1
        body_end = body_start + size
        if object_type != b"blob" or body_end >= len(output) or output[body_end:body_end + 1] != b"\n":
            raise PortError("Git cat-file batch returned an invalid symlink blob")
        blobs[returned_id.decode("ascii")] = output[body_start:body_end]
        offset = body_end + 1
    return blobs

def acquire(url: str, ref: str | None, sha: str | None, channel: str, cache: Path) -> tuple[Path, dict]:
    if not re.fullmatch(r"https://[^\s]+", url):
        raise PortError("Upstream URL must be HTTPS")
    if channel not in {"stable", "prerelease", "edge"}:
        raise PortError(f"Unknown channel: {channel}")
    if not ref and not sha:
        from .release import discover_sources
        match = re.fullmatch(r"https://github.com/([^/]+)/([^/]+?)(?:\.git)?", url)
        if not match:
            raise PortError("A custom upstream URL requires --ref or --sha")
        sources = discover_sources(channel=channel, owner=match[1], repository=match[2])
        if not sources:
            raise PortError(f"No published {channel} source found")
        selected = sources[-1]
        ref, sha = selected["source_ref"], selected["source_sha"]
    if sha and not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise PortError("--sha must be an exact 40-character commit")
    if ref and (ref.startswith("-") or any(c in ref for c in ("\n", "\x00"))):
        raise PortError("Invalid ref")
    cache = cache.resolve()
    checkout = cache / digest(url.encode())[:16]
    if not checkout.exists():
        cache.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false", "-c", "core.eol=lf", *(["-c", "http.sslBackend=schannel"] if os.name == "nt" else []), "clone", "--no-checkout", "--", url, str(checkout)], capture_output=True, text=True)
        if result.returncode:
            raise PortError(result.stderr.strip())
    if git(checkout, "remote", "get-url", "origin") != url:
        raise PortError("Cached upstream remote does not match")
    git(checkout, "fetch", "--tags", "origin")
    resolved_ref = git(checkout, "rev-parse", "--verify", (ref or sha) + "^{commit}")
    if sha and resolved_ref.lower() != sha.lower():
        raise PortError("Requested ref and SHA disagree")
    git(checkout, "checkout", "--detach", resolved_ref)
    return checkout, {"upstream_url": url, "upstream_ref": ref or sha, "upstream_sha": resolved_ref, "channel": channel}

def kind_of(path: str) -> str:
    if path.startswith(".github/skills/") and path.endswith("/SKILL.md"):
        return "skill"
    if path.startswith(".github/agents/") and path.endswith(".agent.md"):
        return "agent"
    if (path.startswith(".github/instructions/") and path.endswith(".instructions.md")) or path == ".github/copilot-instructions.md":
        return "instruction"
    if path.startswith(".github/prompts/") and path.endswith(".prompt.md"):
        return "prompt"
    return "other"


def _validate_frontmatter_types(meta: dict, source: str) -> None:
    def invalid(field: str, expected: str) -> None:
        raise PortError(f"Malformed frontmatter in {source}: {field} must be {expected}")

    for field in ("name", "description", "argument-hint"):
        if field in meta and not isinstance(meta[field], str):
            invalid(field, "a string")
    for field in ("user-invocable", "disable-model-invocation"):
        if field in meta and not isinstance(meta[field], bool):
            invalid(field, "a boolean")
    for field in ("tools", "allowed-tools", "agents", "applyTo"):
        value = meta.get(field)
        if field not in meta:
            continue
        if isinstance(value, str):
            continue
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            invalid(field, "a string or a list of strings")
    if "handoffs" in meta and (
        not isinstance(meta["handoffs"], list)
        or any(not isinstance(item, dict) for item in meta["handoffs"])
    ):
        invalid("handoffs", "a list of mappings")


def _git_link_metadata(root: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    """Read Git's symlink mode and raw link targets, independent of checkout OS."""
    if not (root / ".git").exists():
        return {}, {}
    staged = git(root, "diff", "--cached", "--name-only", "HEAD", "--")
    if staged:
        raise PortError(f"Staged source changes differ from HEAD: {staged.splitlines()[0]}")
    tree = _git_bytes(root, "ls-tree", "-r", "-z", "HEAD", "--")
    link_targets: dict[str, bytes] = {}
    blob_ids: dict[str, str] = {}
    symlink_ids = set()
    symlink_paths = set()
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, _kind, object_id = metadata.split(b" ", 2)
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise PortError("Upstream Git tree contains a non-portable path") from exc
        if mode == b"160000":
            raise PortError(f"Git submodules are unsupported by deterministic inventory: {path}")
        blob_ids[path] = object_id.decode("ascii")
        if mode == b"120000":
            symlink_ids.add(blob_ids[path])
            symlink_paths.add(path)
    symlink_blobs = _git_blobs(root, symlink_ids)
    for path in symlink_paths:
        link_targets[path] = symlink_blobs[blob_ids[path]]
    return link_targets, blob_ids


def _git_blob_id(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def _normalize_link_target(path: str, raw_target: bytes) -> tuple[str, str]:
    """Return a safe in-root target path and decoded raw target."""
    try:
        target = raw_target.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PortError(f"Symlink target is not UTF-8: {path}") from exc
    if not target or target.startswith(("/", "\\")) or "\\" in target or ":" in target or "\x00" in target:
        raise PortError(f"Symlink target must be a relative POSIX path: {path} -> {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise PortError(f"Symlink target escapes upstream: {path} -> {target}")
    return resolved, target


def _validate_link_target(
    root: Path,
    link_path: str,
    raw_target: bytes,
    link_targets: dict[str, bytes],
    blob_ids: dict[str, str],
    chain: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    if link_path in chain:
        raise PortError(f"Cyclic symlink in upstream: {link_path}")
    resolved, target = _normalize_link_target(link_path, raw_target)
    if not resolved or resolved == ".":
        target_path = root
    else:
        target_path = root.joinpath(*Path(resolved).parts)
    if resolved in link_targets:
        # Git symlink placeholders are ordinary files on Windows. Resolve their
        # semantic target recursively instead of trusting the placeholder path.
        _validate_link_target(root, resolved, link_targets[resolved], link_targets, blob_ids, chain | {link_path})
    elif not any(item == resolved or item.startswith(resolved.rstrip("/") + "/") for item in blob_ids):
        raise PortError(f"Dangling symlink in upstream: {link_path} -> {target}")
    try:
        actual = target_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PortError(f"Dangling or cyclic symlink in upstream: {link_path} -> {target}") from exc
    if not actual.is_relative_to(root.resolve()):
        raise PortError(f"Symlink target escapes upstream: {link_path} -> {target}")
    while resolved in link_targets:
        if resolved in chain or resolved == link_path:
            raise PortError(f"Cyclic symlink in upstream: {link_path}")
        resolved, _ = _normalize_link_target(resolved, link_targets[resolved])
    return resolved, target


def _source_paths(root: Path) -> tuple[list[str], list[dict], dict[str, str]]:
    """Enumerate regular files and safe Git mirror links without following links."""
    link_targets, blob_ids = _git_link_metadata(root)
    paths = []
    links = []
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if ".git" in Path(relative).parts:
                    continue
                info = entry.stat(follow_symlinks=False)
                reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & reparse:
                    if relative not in link_targets:
                        raise PortError(f"Untracked source symlink/reparse point is unsupported: {relative}")
                    if relative == ".github" or relative.startswith(".github/"):
                        raise PortError(f"Canonical .github symlink artifacts/resources are unsupported: {relative}")
                    raw_target = os.readlink(path).encode("utf-8")
                    if raw_target != link_targets[relative]:
                        raise PortError(f"Git symlink target differs from checkout: {relative}")
                    if relative in blob_ids and _git_blob_id(raw_target) != blob_ids[relative]:
                        raise PortError(f"Git symlink bytes differ from canonical blob: {relative}")
                    resolved, target = _validate_link_target(root, relative, raw_target, link_targets, blob_ids)
                    links.append((relative, raw_target, resolved, target))
                    continue
                if relative in link_targets:
                    # Git checks out mode 120000 as a regular placeholder on
                    # Windows. Its bytes are the link target, not file content.
                    raw_target = path.read_bytes()
                    if raw_target != link_targets[relative] or (
                        relative in blob_ids and _git_blob_id(raw_target) != blob_ids[relative]
                    ):
                        raise PortError(f"Git symlink placeholder differs from index tree: {relative}")
                    if relative == ".github" or relative.startswith(".github/"):
                        raise PortError(f"Canonical .github symlink artifacts/resources are unsupported: {relative}")
                    resolved, target = _validate_link_target(root, relative, raw_target, link_targets, blob_ids)
                    links.append((relative, raw_target, resolved, target))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    paths.append(relative)
    if blob_ids:
        observed = set(paths) | {link[0] for link in links}
        expected = set(blob_ids)
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        if missing:
            raise PortError(f"Tracked source files are missing from the checkout: {missing[0]}")
        if extra:
            raise PortError(f"Untracked source files are not permitted in immutable inventory: {extra[0]}")
    return sorted(paths), sorted(links), blob_ids


def scan(root: Path, provenance: dict | None = None) -> dict:
    root = root.resolve()
    provenance = provenance or {"upstream_ref": "local", "upstream_sha": git(root, "rev-parse", "HEAD") if (root / ".git").exists() else "0" * 40, "channel": "edge"}
    paths, symlinks, blob_ids = _source_paths(root)
    known = set(paths)
    aliases: dict[str, str] = {}
    for path, _raw_target, resolved, _target in symlinks:
        aliases[path] = resolved
        # Preserve link paths as accepted aliases for references, then rewrite
        # their graph targets to the canonical tracked target below.
        known.add(path)
        if any(item.startswith(resolved.rstrip("/") + "/") for item in paths):
            known.add(path.rstrip("/") + "/__hve_link_directory_marker__")

    def canonicalize_refs(refs: list[dict]) -> list[dict]:
        for reference in refs:
            target = reference.get("target")
            if target in aliases:
                reference["target"] = aliases[target]
        return refs
    seen = {}
    files = []
    artifacts = []
    findings = []
    for path in paths:
        key = unicodedata.normalize("NFC", path).casefold()
        if key in seen:
            raise PortError(f"Case/Unicode collision: {seen[key]} and {path}")
        seen[key] = path
        file = safe_path(root, path)
        data = file.read_bytes()
        if path in blob_ids and _git_blob_id(data) != blob_ids[path]:
            raise PortError(
                f"Tracked source bytes differ from the canonical Git blob: {path}; "
                "use a clean checkout with core.autocrlf=false and core.eol=lf"
            )
        kind = kind_of(path)
        classification = "runtime-resource" if path.startswith((".github/skills/", ".github/agents/", ".github/instructions/", ".github/prompts/", ".vscode/", "evals/")) else "excluded-with-rationale"
        rationale = "Supporting agent package, runtime configuration, or evaluation resource" if classification != "excluded-with-rationale" else "Not an agent-facing runtime artifact; reference closure may promote this file"
        files.append({"id": "file:" + path, "path": path, "source_sha": provenance["upstream_sha"], "hash": digest(data), "classification": classification, "rationale": rationale, "outputs": [], "status": "excluded-with-rationale" if classification == "excluded-with-rationale" else "unresolved"})
        if path.endswith((".chatmode.md", ".workflow.md")):
            findings.append({"source": path, "code": "unknown-artifact-kind"})
        markdown = None
        if path.casefold().endswith(".md"):
            try:
                markdown = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise PortError(f"Markdown resource is not UTF-8: {path}") from exc
        if kind == "other":
            if markdown is not None:
                # Support documents become dependencies when an artifact links
                # to them, so their own links must be part of the graph too.
                files[-1]["references"] = canonicalize_refs(references(markdown, path, known))
            continue
        if markdown is None:
            raise PortError(f"Instruction artifact is not UTF-8 Markdown: {path}")
        text = markdown
        meta, body = frontmatter(text, path)
        _validate_frontmatter_types(meta, path)
        files[-1]["references"] = canonicalize_refs(references(body, path, known))
        unknown = sorted(set(meta) - KNOWN_FIELDS[kind])
        for field in unknown:
            findings.append({"source": path, "code": "unknown-field", "field": field})
        refs = canonicalize_refs(references(body, path, known))
        artifacts.append({"id": kind + ":" + path, "kind": kind, "source": path, "source_sha": provenance["upstream_sha"], "hash": digest(data), "frontmatter": meta, "name": meta.get("name", Path(path).name.split(".")[0] if kind != "skill" else Path(path).parent.name), "user_invocable": meta.get("user-invocable", "/subagents/" not in path), "disable_model_invocation": meta.get("disable-model-invocation", False), "references": refs, "dependencies": sorted({r["target"] for r in refs if r["target"]}), "required_tools": meta.get("tools", meta.get("allowed-tools", [])), "strategy": "role-aware" if kind == "agent" else "structured-markdown", "outputs": [], "status": "unresolved"})
    for path, raw_target, resolved, target in symlinks:
        files.append({
            "id": "file:" + path,
            "path": path,
            "source_sha": provenance["upstream_sha"],
            "hash": digest(raw_target),
            "classification": "excluded-with-rationale",
            "rationale": f"Git symlink mirror of {resolved} (target {target}); canonical source is inventoried separately",
            "outputs": [],
            "status": "excluded-with-rationale",
            "symlink_target": target,
            "resolved_symlink_target": resolved,
        })
    manifests = []
    listed = set()
    missing = []
    for path in paths:
        if Path(path).name != "plugin.json":
            continue
        try:
            manifest = json.loads(safe_path(root, path).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise PortError(f"Malformed manifest {path}: {exc}") from exc
        manifests.append({"source": path, "metadata": manifest})
        for group in ("skills", "agents", "rules", "commands"):
            entries = manifest.get(group, [])
            if isinstance(entries, str):
                entries = [entries]
            for entry in entries:
                if not isinstance(entry, str):
                    findings.append({"source": path, "code": "unknown-manifest-entry", "field": group})
                    continue
                candidates = [entry.removeprefix("./"), str(Path(path).parent / entry).replace("\\", "/")]
                found = next((p for p in candidates if p in known or p + "/SKILL.md" in known), None)
                if found:
                    listed.add(found + "/SKILL.md" if found + "/SKILL.md" in known else found)
                elif not (entry.endswith("/") and any(p.startswith(entry.removeprefix("./")) for p in known)):
                    missing.append({"manifest": path, "group": group, "path": entry})
    return {"schema_version": 1, **provenance, "files": files, "artifacts": artifacts, "manifests": manifests, "manifest_missing": missing, "manifest_layout": "root" if "plugin.json" in known else "legacy-collections" if manifests else "unmanifested", "discovered_unlisted": sorted(a["source"] for a in artifacts if a["source"] not in listed), "findings": findings}
