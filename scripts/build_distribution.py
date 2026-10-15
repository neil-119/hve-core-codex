"""Build immutable upstream input into an external, deterministic distribution."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path

from hve_codex_porter.common import PortError
from hve_codex_porter.conversion import convert
from hve_codex_porter.inventory import acquire, git
from hve_codex_porter.packaging import distribution_files, validate


def archive_distribution(output: Path) -> Path:
    """Canonical metadata avoids workstation paths, clocks and owners in archives."""
    destination = Path(str(output) + ".tar.gz")
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        pending = Path(temporary.name)
        try:
            with gzip.GzipFile(fileobj=temporary, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    files = distribution_files(output)
                    checksum_file = output / "generated/checksums.txt"
                    if checksum_file.is_file():
                        files.append(checksum_file)
                    for path in sorted(files, key=lambda item: item.relative_to(output).as_posix()):
                        if path.is_symlink():
                            raise PortError(f"Refusing distribution symlink: {path}")
                        if not path.is_file():
                            continue
                        info = tarfile.TarInfo(path.relative_to(output).as_posix())
                        info.size = path.stat().st_size
                        info.mode = 0o644
                        info.uid = info.gid = info.mtime = 0
                        info.uname = info.gname = ""
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
            temporary.flush()
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    os.replace(pending, destination)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    Path(str(destination) + ".sha256").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
    return destination


def zip_distribution(output: Path) -> Path:
    """Windows-friendly archive with the same managed payload and fixed metadata."""
    destination = Path(str(output) + ".zip")
    files = distribution_files(output)
    checksums = output / "generated/checksums.txt"
    if checksums.is_file():
        files.append(checksums)
    descriptor, name = tempfile.mkstemp(dir=destination.parent)
    os.close(descriptor)
    pending = Path(name)
    try:
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(files, key=lambda item: item.relative_to(output).as_posix()):
                if path.is_symlink():
                    raise PortError(f"Refusing distribution symlink: {path}")
                info = zipfile.ZipInfo(path.relative_to(output).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes(), compresslevel=9)
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    Path(str(destination) + ".sha256").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, help="Existing immutable Git checkout for offline builds")
    parser.add_argument("--channel", choices=("stable", "prerelease", "edge"), default="stable")
    parser.add_argument("--ref")
    parser.add_argument("--sha")
    parser.add_argument("--adapter-revision")
    parser.add_argument("--archive-only", action="store_true",
                        help="Repackage validated output after release metadata is generated")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    output = args.output.expanduser().resolve()
    if output == repo or output.is_relative_to(repo):
        raise PortError("Build output must be outside the migrator repository")
    if args.archive_only:
        if not validate(output, repo / "schemas/plugin.schema.json")["valid"]:
            raise PortError("Generated distribution failed structural validation")
        archive = archive_distribution(output)
        zip_archive = zip_distribution(output)
        print(json.dumps({"distribution": str(output), "archive": str(archive), "zip": str(zip_archive), "valid": True}))
        return 0
    with tempfile.TemporaryDirectory(prefix="hve-source-build-") as temporary:
        if args.source_dir:
            source = args.source_dir.expanduser().resolve()
            if source == repo or source.is_relative_to(repo):
                raise PortError("Upstream source must be outside the migrator repository")
            sha = git(source, "rev-parse", "HEAD")
            if args.sha and args.sha.lower() != sha:
                raise PortError("--sha disagrees with the immutable source checkout")
            provenance = {"upstream_url": "https://github.com/microsoft/hve-core.git",
                          "upstream_ref": args.ref or sha, "upstream_sha": sha, "channel": args.channel}
        else:
            source, provenance = acquire("https://github.com/microsoft/hve-core.git", args.ref,
                                         args.sha, args.channel, Path(temporary) / "upstream")
        if args.adapter_revision:
            provenance["adapter_revision"] = args.adapter_revision
        convert(source, output, provenance, repo / "mappings")
        report = validate(output, repo / "schemas/plugin.schema.json")
        if not report["valid"]:
            raise PortError("Generated distribution failed structural validation")
        archive = archive_distribution(output)
        zip_archive = zip_distribution(output)
        print(json.dumps({"distribution": str(output), "archive": str(archive), "zip": str(zip_archive),
                          "upstream_sha": provenance["upstream_sha"], "valid": True,
                          "release_eligible": report.get("release_eligible", False)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PortError as exc:
        raise SystemExit(str(exc)) from exc
