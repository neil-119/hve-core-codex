from __future__ import annotations

import io
import hashlib
import json
import shutil
import stat
import zipfile
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from hve_codex_porter import release_evidence as transport
from hve_codex_porter.release_evidence import EvidenceDeliveryError, restore_runtime_evidence


REPOSITORY = "example/hve-core-codex"
SOURCE_SHA = "a" * 40


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_envelope(candidate: Path, files: dict[str, bytes]) -> bytes:
    value = {
        "schema_version": 2,
        "source_sha": SOURCE_SHA,
        "output_hashes": {
            "generated/runtime-evidence/" + name: _digest(data)
            for name, data in files.items()
        },
    }
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = candidate / "generated/runtime-evidence.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return raw


def _write_archive(path: Path, files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
    return path.read_bytes()


def _source_metadata(archive: bytes, envelope: bytes, *, source_kind: str = "actions-artifact") -> dict:
    source = {
        "schema_version": 1,
        "source_kind": source_kind,
        "repository": REPOSITORY,
        "archive_sha256": _digest(archive),
        "runtime_evidence_sha256": _digest(envelope),
    }
    source["artifact_id" if source_kind == "actions-artifact" else "asset_id"] = 142
    return source


def test_restores_exact_envelope_files_and_records_source_receipt(tmp_path: Path):
    files = {
        "cli.json": b'{"result":"passed"}\n',
        "bundle/generated/source-inventory.json": b'{"upstream_sha":"' + SOURCE_SHA.encode() + b'"}\n',
        "run/result.json": b'{"verified":true}\n',
    }
    archive_path = tmp_path / "capture.zip"
    archive = _write_archive(archive_path, files)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    envelope = _write_envelope(candidate, files)
    source_path = tmp_path / "evidence-source.json"
    source = _source_metadata(archive, envelope)
    source.pop("source_kind")  # Legacy metadata defaults to Actions artifact.
    source_path.write_text(json.dumps(source), encoding="utf-8")
    receipt_path = tmp_path / "runner-temp" / "delivery.json"
    receipt_path.parent.mkdir()
    calls = []

    def download(kind: str, repository: str, source_id: int, token: str, destination: Path) -> None:
        calls.append((kind, repository, source_id, token))
        shutil.copyfile(archive_path, destination)

    receipt = restore_runtime_evidence(
        source_path=source_path,
        candidate_root=candidate,
        repository=REPOSITORY,
        receipt_path=receipt_path,
        token="fake-token",
        downloader=download,
    )

    assert calls == [("actions-artifact", REPOSITORY, 142, "fake-token")]
    assert {item["path"] for item in receipt["files"]} == {
        "generated/runtime-evidence/" + name for name in files
    }
    assert receipt["candidate_source_sha"] == SOURCE_SHA
    assert receipt["runtime_evidence_sha256"] == _digest(envelope)
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    for name, data in files.items():
        assert (candidate / "generated/runtime-evidence" / name).read_bytes() == data


@pytest.mark.parametrize("failure", ["archive-hash", "envelope-hash", "repository"])
def test_pin_mismatch_fails_before_any_evidence_is_published(tmp_path: Path, failure: str):
    files = {"cli.json": b"captured report\n"}
    archive_path = tmp_path / "capture.zip"
    archive = _write_archive(archive_path, files)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    envelope = _write_envelope(candidate, files)
    source = _source_metadata(archive, envelope)
    if failure == "archive-hash":
        source["archive_sha256"] = "0" * 64
    elif failure == "envelope-hash":
        source["runtime_evidence_sha256"] = "1" * 64
    source_path = tmp_path / "evidence-source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    receipt = tmp_path / "runner-temp/receipt.json"
    receipt.parent.mkdir()
    calls = []

    def download(kind: str, repository: str, source_id: int, token: str, destination: Path) -> None:
        calls.append(source_id)
        shutil.copyfile(archive_path, destination)

    with pytest.raises(EvidenceDeliveryError):
        restore_runtime_evidence(
            source_path=source_path,
            candidate_root=candidate,
            repository="other/repository" if failure == "repository" else REPOSITORY,
            receipt_path=receipt,
            token="fake-token",
            downloader=download,
        )
    assert not (candidate / "generated/runtime-evidence").exists()
    assert not receipt.exists()
    assert calls == ([] if failure in ("repository", "envelope-hash") else [142])


@pytest.mark.parametrize("archive_kind", ["traversal", "symlink", "undeclared"])
def test_unsafe_or_undeclared_zip_entries_fail_without_partial_restore(tmp_path: Path, archive_kind: str):
    expected = {"cli.json": b"expected report\n"}
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    envelope = _write_envelope(candidate, expected)
    archive_path = tmp_path / "capture.zip"
    if archive_kind == "traversal":
        archive = _write_archive(archive_path, {"../escape.json": b"outside"})
    elif archive_kind == "symlink":
        archive = _write_archive(archive_path, expected, symlink="evil-link")
    else:
        archive = _write_archive(archive_path, {**expected, "extra.json": b"not declared"})
    source_path = tmp_path / "evidence-source.json"
    source_path.write_text(json.dumps(_source_metadata(archive, envelope)), encoding="utf-8")
    receipt_path = tmp_path / "runner-temp/receipt.json"
    receipt_path.parent.mkdir()
    archive_source = tmp_path / "pinned.zip"
    archive_source.write_bytes(archive)

    def download(_kind: str, _repository: str, _source_id: int, _token: str, destination: Path) -> None:
        shutil.copyfile(archive_source, destination)

    with pytest.raises(EvidenceDeliveryError):
        restore_runtime_evidence(
            source_path=source_path,
            candidate_root=candidate,
            repository=REPOSITORY,
            receipt_path=receipt_path,
            token="fake-token",
            downloader=download,
        )
    assert not (candidate / "generated/runtime-evidence").exists()
    assert not receipt_path.exists()
    assert not list((candidate / "generated").glob(".runtime-evidence-stage-*"))


def test_release_asset_source_uses_numeric_asset_id(tmp_path: Path):
    files = {"desktop.json": b"manual desktop capture\n"}
    archive_path = tmp_path / "capture.zip"
    archive = _write_archive(archive_path, files)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    envelope = _write_envelope(candidate, files)
    metadata = _source_metadata(archive, envelope, source_kind="release-asset")
    # Release assets are explicit because their IDs are distinct from artifact IDs.
    source_path = tmp_path / "evidence-source.json"
    source_path.write_text(json.dumps(metadata), encoding="utf-8")
    receipt_path = tmp_path / "runner-temp/receipt.json"
    receipt_path.parent.mkdir()
    calls = []

    def download(kind: str, repository: str, source_id: int, _token: str, destination: Path) -> None:
        calls.append((kind, repository, source_id))
        shutil.copyfile(archive_path, destination)

    receipt = restore_runtime_evidence(
        source_path=source_path,
        candidate_root=candidate,
        repository=REPOSITORY,
        receipt_path=receipt_path,
        token="fake-token",
        downloader=download,
    )
    assert calls == [("release-asset", REPOSITORY, 142)]
    assert receipt["source_kind"] == "release-asset"
    assert receipt["source_id"] == 142


@pytest.mark.parametrize(
    ("source_kind", "endpoint", "accept"),
    [
        ("actions-artifact", "/actions/artifacts/142/zip", "application/vnd.github+json"),
        ("release-asset", "/releases/assets/142", "application/octet-stream"),
    ],
)
def test_github_download_uses_pinned_endpoint_and_does_not_forward_token_to_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_kind: str, endpoint: str, accept: str,
):
    data = b"immutable archive bytes"
    calls = []

    class Response:
        status = 200
        headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            nonlocal data
            chunk, data = data, b""
            return chunk

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                headers = Message()
                headers["Location"] = "https://objects.example.test/pinned.zip?signature=secret"
                raise HTTPError(request.full_url, 302, "Found", headers, io.BytesIO())
            return Response()

    monkeypatch.setattr(transport.urllib.request, "build_opener", lambda *_handlers: Opener())
    destination = tmp_path / "download.zip"
    transport._download_archive(source_kind, REPOSITORY, 142, "ghs-token", destination)

    assert endpoint in calls[0].full_url
    assert calls[0].get_header("Accept") == accept
    assert calls[0].get_header("Authorization") == "Bearer ghs-token"
    assert calls[1].full_url.startswith("https://objects.example.test/")
    assert calls[1].get_header("Authorization") is None
    assert destination.read_bytes() == b"immutable archive bytes"


def test_rebuild_helper_restores_after_copying_envelope_and_skips_only_absent_metadata():
    helper = Path(__file__).resolve().parents[2] / ".github/scripts/rebuild_release_distribution.sh"
    source = helper.read_text(encoding="utf-8")
    copy_position = source.index('cp "$runtime_evidence_path" "$output/generated/runtime-evidence.json"')
    restore_position = source.index("python -m hve_codex_porter.release_evidence")
    missing_position = source.index("No reviewed runtime-evidence source is configured")
    assert copy_position < restore_position < missing_position
    assert '"$runtime_evidence_source_path"' in source
    assert '"${GITHUB_REPOSITORY:-}"' in source
    assert '"$RUNNER_TEMP/release-evidence-delivery-$channel-${GITHUB_RUN_ID:-local}.json"' in source
