"""Restore reviewed runtime evidence from a pinned GitHub Actions artifact.

The archive is data only: this module validates and copies regular files but
never runs anything from it. Both the compressed archive and every extracted
file are bound to hashes recorded in the protected runtime-evidence envelope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from .common import is_canonical_var_alias

_PREFIX = "generated/runtime-evidence/"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_MAX_METADATA_BYTES = 64 * 1024
_MAX_ENVELOPE_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 100_000
_MAX_FILE_BYTES = 512 * 1024 * 1024
_MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class EvidenceDeliveryError(ValueError):
    """A pinned runtime-evidence artifact could not be restored safely."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceDeliveryError(f"JSON contains a duplicate key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, label: str, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    _reject_symlink_components(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise EvidenceDeliveryError(f"{label} must be a bounded regular file")
        chunks = []
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise EvidenceDeliveryError(f"{label} exceeds its size limit")
                chunks.append(chunk)
        raw = b"".join(chunks)
    except OSError as error:
        raise EvidenceDeliveryError(f"Could not read {label}") from error
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceDeliveryError(f"{label} must contain valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise EvidenceDeliveryError(f"{label} must be a JSON object")
    return value, raw


def _reject_symlink_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path.expanduser()))
    chain = [*reversed(absolute.parents), absolute]
    for component in chain:
        try:
            info = component.lstat()
        except OSError as error:
            if isinstance(error, FileNotFoundError):
                continue
            raise EvidenceDeliveryError(f"Could not inspect path: {component}") from error
        is_reparse = stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        if is_reparse:
            if component != absolute and is_canonical_var_alias(component):
                continue
            raise EvidenceDeliveryError(f"Path must not contain symlinks or reparse points: {component}")


def _real_directory(path: Path, *, label: str) -> Path:
    _reject_symlink_components(path)
    if not path.is_dir():
        raise EvidenceDeliveryError(f"{label} must be an existing regular directory")
    return path.absolute()


def _validate_repository(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _REPOSITORY.fullmatch(value):
        raise EvidenceDeliveryError(f"{label} must be an owner/repository name")
    return value


def _validate_metadata(source: dict[str, Any], expected_repository: str) -> dict[str, Any]:
    common_fields = {"schema_version", "repository", "archive_sha256", "runtime_evidence_sha256"}
    source_kind = source.get("source_kind", "actions-artifact")
    if source_kind == "actions-artifact":
        identifier_field = "artifact_id"
    elif source_kind == "release-asset":
        identifier_field = "asset_id"
    else:
        raise EvidenceDeliveryError("Evidence source kind must be actions-artifact or release-asset")
    fields = common_fields | {identifier_field}
    if "source_kind" in source:
        fields.add("source_kind")
    if set(source) != fields:
        raise EvidenceDeliveryError("Evidence source metadata has missing or unknown fields")
    if source.get("schema_version") != 1 or type(source.get("schema_version")) is not int:
        raise EvidenceDeliveryError("Evidence source schema version is unsupported")
    repository = _validate_repository(source.get("repository"), label="Evidence source repository")
    if repository != _validate_repository(expected_repository, label="GitHub repository"):
        raise EvidenceDeliveryError("Evidence source belongs to a different GitHub repository")
    source_id = source.get(identifier_field)
    if type(source_id) is not int or source_id <= 0:
        raise EvidenceDeliveryError(f"Evidence source {identifier_field} must be a positive integer")
    for name in ("archive_sha256", "runtime_evidence_sha256"):
        if not isinstance(source.get(name), str) or not _SHA256.fullmatch(source[name]):
            raise EvidenceDeliveryError(f"Evidence source {name} must be a lowercase SHA-256 digest")
    return {
        "repository": repository,
        "source_kind": source_kind,
        "source_id": source_id,
        "archive_sha256": source["archive_sha256"],
        "runtime_evidence_sha256": source["runtime_evidence_sha256"],
    }


def _safe_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise EvidenceDeliveryError(f"{label} must be a non-empty relative path")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise EvidenceDeliveryError(f"{label} is not a safe portable relative path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise EvidenceDeliveryError(f"{label} contains an unsafe path component")
    if any(":" in part or part.endswith((".", " ")) for part in parts):
        raise EvidenceDeliveryError(f"{label} contains a non-portable path component")
    if any(part.split(".", 1)[0].upper() in _WINDOWS_RESERVED for part in parts):
        raise EvidenceDeliveryError(f"{label} contains a reserved Windows path component")
    return value


def _expected_files(envelope: dict[str, Any]) -> dict[str, str]:
    output_hashes = envelope.get("output_hashes")
    if not isinstance(output_hashes, dict):
        raise EvidenceDeliveryError("Runtime-evidence envelope must include output_hashes")
    expected: dict[str, str] = {}
    folded: set[str] = set()
    for raw_path, file_hash in output_hashes.items():
        if not isinstance(raw_path, str):
            raise EvidenceDeliveryError("Runtime-evidence output paths must be strings")
        if not isinstance(file_hash, str) or not _SHA256.fullmatch(file_hash):
            raise EvidenceDeliveryError(f"Runtime-evidence output hash is invalid for {raw_path}")
        if not raw_path.startswith(_PREFIX):
            continue
        path = _safe_relative_path(raw_path, label="Runtime-evidence output path")
        relative = path[len(_PREFIX):]
        if not relative:
            raise EvidenceDeliveryError("Runtime-evidence output path must name a file")
        _safe_relative_path(relative, label="Runtime-evidence archive path")
        if relative.casefold() in folded:
            raise EvidenceDeliveryError("Runtime-evidence paths collide on a case-insensitive filesystem")
        folded.add(relative.casefold())
        expected[relative] = file_hash
    if not expected:
        raise EvidenceDeliveryError("Runtime-evidence envelope declares no raw files to restore")
    return expected


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_zip(archive_path: Path, expected: dict[str, str]) -> list[zipfile.ZipInfo]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise EvidenceDeliveryError("Runtime-evidence artifact is not a valid ZIP archive") from error
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_ARCHIVE_ENTRIES:
            raise EvidenceDeliveryError("Runtime-evidence artifact has an invalid entry count")
        expected_folded = {name.casefold() for name in expected}
        file_infos: dict[str, zipfile.ZipInfo] = {}
        seen: set[str] = set()
        directories: set[str] = set()
        expanded_total = 0
        for info in infos:
            name = info.filename
            is_directory = info.is_dir() or name.endswith("/")
            raw_name = name[:-1] if is_directory and name.endswith("/") else name
            relative = _safe_relative_path(raw_name, label="ZIP entry path")
            folded = relative.casefold()
            if folded in seen:
                raise EvidenceDeliveryError("Runtime-evidence ZIP contains duplicate or case-colliding entries")
            seen.add(folded)
            if info.flag_bits & 0x1:
                raise EvidenceDeliveryError("Encrypted ZIP entries are not supported")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise EvidenceDeliveryError("Runtime-evidence ZIP uses an unsupported compression method")
            mode_type = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
            if is_directory:
                if mode_type not in (0, stat.S_IFDIR) or info.file_size != 0:
                    raise EvidenceDeliveryError("Runtime-evidence ZIP contains a non-regular directory entry")
                directories.add(relative)
                continue
            if mode_type not in (0, stat.S_IFREG):
                raise EvidenceDeliveryError("Runtime-evidence ZIP contains a symlink or special file")
            if info.file_size < 0 or info.file_size > _MAX_FILE_BYTES:
                raise EvidenceDeliveryError("Runtime-evidence ZIP contains an oversized file")
            expanded_total += info.file_size
            if expanded_total > _MAX_EXPANDED_BYTES:
                raise EvidenceDeliveryError("Runtime-evidence ZIP expands beyond the size limit")
            if folded not in expected_folded:
                raise EvidenceDeliveryError(f"Runtime-evidence ZIP contains an undeclared file: {relative}")
            file_infos[relative] = info
        if set(file_infos) != set(expected):
            missing = sorted(set(expected) - set(file_infos))
            raise EvidenceDeliveryError("Runtime-evidence ZIP is missing declared files: " + ", ".join(missing[:8]))
        required_directories = {
            parent.as_posix()
            for name in expected
            for parent in Path(name).parents
            if parent.as_posix() != "."
        }
        if any(directory not in required_directories for directory in directories):
            raise EvidenceDeliveryError("Runtime-evidence ZIP contains an undeclared directory")
        return [file_infos[name] for name in sorted(file_infos)]


def _extract_verified(
    archive_path: Path,
    infos: list[zipfile.ZipInfo],
    expected: dict[str, str],
    staging: Path,
) -> list[dict[str, Any]]:
    receipt_files: list[dict[str, Any]] = []
    total = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in infos:
                relative = _safe_relative_path(info.filename, label="ZIP entry path")
                target = staging.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info, "r") as source, target.open("xb") as destination:
                    while chunk := source.read(_CHUNK_BYTES):
                        size += len(chunk)
                        total += len(chunk)
                        if size > _MAX_FILE_BYTES or total > _MAX_EXPANDED_BYTES:
                            raise EvidenceDeliveryError("Runtime-evidence ZIP expanded beyond the size limit")
                        digest.update(chunk)
                        destination.write(chunk)
                actual_hash = digest.hexdigest()
                if size != info.file_size or actual_hash != expected[relative]:
                    raise EvidenceDeliveryError(f"Runtime-evidence file does not match its envelope hash: {relative}")
                receipt_files.append({
                    "path": _PREFIX + relative,
                    "sha256": actual_hash,
                    "size_bytes": size,
                })
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, EvidenceDeliveryError):
            raise
        raise EvidenceDeliveryError("Could not safely extract runtime-evidence ZIP") from error
    return receipt_files


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _download_archive(
    source_kind: str,
    repository: str,
    source_id: int,
    token: str,
    destination: Path,
) -> None:
    if not token or "\r" in token or "\n" in token:
        raise EvidenceDeliveryError("GH_TOKEN is required to download the pinned runtime-evidence artifact")
    if source_kind == "actions-artifact":
        endpoint = f"https://api.github.com/repos/{repository}/actions/artifacts/{source_id}/zip"
        accept = "application/vnd.github+json"
    elif source_kind == "release-asset":
        endpoint = f"https://api.github.com/repos/{repository}/releases/assets/{source_id}"
        accept = "application/octet-stream"
    else:
        raise EvidenceDeliveryError("Unsupported GitHub runtime-evidence source kind")
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "User-Agent": "hve-core-codex-release-evidence/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        try:
            response = opener.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise EvidenceDeliveryError(f"GitHub artifact download failed with HTTP {error.code}") from error
            location = error.headers.get("Location")
            error.close()
            if not location or len(location) > 16_384:
                raise EvidenceDeliveryError("GitHub artifact API returned an invalid download redirect")
            redirect = urllib.parse.urlsplit(location)
            if redirect.scheme != "https" or not redirect.hostname or redirect.username or redirect.password:
                raise EvidenceDeliveryError("GitHub artifact API returned an unsafe download redirect")
            # The signed blob URL is requested without the GitHub token.
            blob_request = urllib.request.Request(location, headers={"User-Agent": "hve-core-codex-release-evidence/1"})
            try:
                response = opener.open(blob_request, timeout=120)
            except urllib.error.HTTPError as blob_error:
                raise EvidenceDeliveryError("GitHub artifact blob download failed") from blob_error
        with response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise EvidenceDeliveryError(f"GitHub artifact download returned HTTP {status}")
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    declared_length = int(length)
                except ValueError as error:
                    raise EvidenceDeliveryError("GitHub artifact response has an invalid Content-Length") from error
                if declared_length < 0 or declared_length > _MAX_ARCHIVE_BYTES:
                    raise EvidenceDeliveryError("Runtime-evidence artifact archive exceeds the size limit")
            written = 0
            with destination.open("xb") as output:
                while chunk := response.read(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > _MAX_ARCHIVE_BYTES:
                        raise EvidenceDeliveryError("Runtime-evidence artifact archive exceeds the size limit")
                    output.write(chunk)
            if length is not None and written != declared_length:
                raise EvidenceDeliveryError("GitHub artifact response length did not match Content-Length")
    except urllib.error.URLError as error:
        raise EvidenceDeliveryError("Could not download the pinned GitHub runtime-evidence artifact") from error


Downloader = Callable[[str, str, int, str, Path], None]


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    _reject_symlink_components(path.parent)
    if not path.parent.is_dir() or os.path.lexists(path):
        raise EvidenceDeliveryError("Delivery receipt path must be new and have an existing regular parent")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".release-evidence-receipt-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            raise EvidenceDeliveryError("Delivery receipt path already exists")
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise EvidenceDeliveryError("Could not write the runtime-evidence delivery receipt") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore_runtime_evidence(
    *,
    source_path: Path,
    candidate_root: Path,
    repository: str,
    receipt_path: Path,
    token: str | None = None,
    downloader: Downloader | None = None,
) -> dict[str, Any]:
    """Download, verify, and atomically restore the envelope-declared raw files."""
    source, source_bytes = _read_json(
        source_path, label="Evidence source metadata", max_bytes=_MAX_METADATA_BYTES,
    )
    metadata = _validate_metadata(source, repository)
    candidate = _real_directory(candidate_root, label="Release candidate")
    envelope_path = candidate / "generated/runtime-evidence.json"
    envelope, envelope_bytes = _read_json(
        envelope_path, label="Runtime-evidence envelope", max_bytes=_MAX_ENVELOPE_BYTES,
    )
    envelope_hash = hashlib.sha256(envelope_bytes).hexdigest()
    if envelope_hash != metadata["runtime_evidence_sha256"]:
        raise EvidenceDeliveryError("Runtime-evidence envelope does not match reviewed source metadata")
    expected = _expected_files(envelope)
    try:
        source_sha = envelope.get("source_sha")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise EvidenceDeliveryError("Runtime-evidence envelope must identify its immutable source SHA")
        generated = _real_directory(candidate / "generated", label="Candidate generated directory")
    except OSError as error:
        raise EvidenceDeliveryError("Could not inspect the release candidate") from error
    target = generated / "runtime-evidence"
    if os.path.lexists(target):
        raise EvidenceDeliveryError("Candidate already contains generated/runtime-evidence; refusing to merge or replace it")
    receipt = receipt_path.absolute()
    try:
        receipt.relative_to(candidate)
    except ValueError:
        pass
    else:
        raise EvidenceDeliveryError("Delivery receipt must be written outside the release candidate")

    download_token = token if token is not None else os.environ.get("GH_TOKEN", "")
    download = downloader if downloader is not None else _download_archive
    published = False
    receipt_written = False
    staging: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="hve-release-evidence-") as temporary_directory:
            archive_path = Path(temporary_directory) / "artifact.zip"
            download(
                metadata["source_kind"], metadata["repository"], metadata["source_id"],
                download_token, archive_path,
            )
            if not archive_path.is_file() or archive_path.stat().st_size > _MAX_ARCHIVE_BYTES:
                raise EvidenceDeliveryError("Downloaded runtime-evidence archive is missing or oversized")
            archive_hash = _sha256_file(archive_path)
            if archive_hash != metadata["archive_sha256"]:
                raise EvidenceDeliveryError("Runtime-evidence artifact archive does not match reviewed source metadata")
            infos = _verify_zip(archive_path, expected)
            staging = Path(tempfile.mkdtemp(prefix=".runtime-evidence-stage-", dir=generated))
            files = _extract_verified(archive_path, infos, expected, staging)
            os.rename(staging, target)
            staging = None
            published = True

        receipt_data = {
            "schema_version": 1,
            "repository": metadata["repository"],
            "source_kind": metadata["source_kind"],
            "source_id": metadata["source_id"],
            "source_metadata_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "archive_sha256": archive_hash,
            "runtime_evidence_sha256": envelope_hash,
            "candidate_source_sha": source_sha,
            "files": files,
        }
        _write_receipt(receipt, receipt_data)
        receipt_written = True
        return receipt_data
    except (OSError, zipfile.BadZipFile) as error:
        if isinstance(error, EvidenceDeliveryError):
            raise
        raise EvidenceDeliveryError("Runtime-evidence artifact restore failed") from error
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if published and not receipt_written:
            shutil.rmtree(target, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="reviewed evidence-source.json")
    parser.add_argument("--candidate", type=Path, required=True, help="external rebuilt distribution root")
    parser.add_argument("--repository", required=True, help="owner/repository for the GitHub API")
    parser.add_argument("--receipt", type=Path, required=True, help="receipt output path outside the candidate")
    args = parser.parse_args(argv)
    try:
        receipt = restore_runtime_evidence(
            source_path=args.source,
            candidate_root=args.candidate,
            repository=args.repository,
            receipt_path=args.receipt,
        )
    except (EvidenceDeliveryError, OSError) as error:
        print(f"Runtime-evidence delivery failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
