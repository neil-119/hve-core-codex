# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Install published HVE assets without Git, WSL, or building upstream content."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "neil-119/hve-core-codex"
API = f"https://api.github.com/repos/{REPOSITORY}"
DOWNLOAD_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"


def metadata(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "hve-core-codex-release-installer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Release metadata exceeds the supported size.")
    return json.loads(data)


def select_release(selector: str, get=metadata) -> dict:
    if selector == "stable":
        release = get(f"{API}/releases/latest")
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("No published stable release is available.")
        return release
    if selector != "edge":
        release = get(f"{API}/releases/tags/{urllib.parse.quote(selector, safe='')}")
        if release.get("draft") or release.get("tag_name") != selector:
            raise ValueError("The requested tag is not a published release.")
        return release
    candidates = []
    for page in range(1, 101):
        releases = get(f"{API}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError("Malformed release list.")
        candidates.extend(item for item in releases if not item.get("draft") and item.get("prerelease")
                          and re.fullmatch(r"hve-core-edge-[0-9a-f]{40}-codex\.\d+", item.get("tag_name", "")))
        if len(releases) < 100:
            break
    else:
        raise ValueError("Release pagination limit reached; choose an explicit tag.")
    if not candidates:
        raise ValueError("No published edge release is available.")
    return max(candidates, key=lambda item: item.get("published_at") or "")


def download(asset: dict, destination: Path) -> None:
    url = asset.get("browser_download_url", "")
    if not url.startswith(DOWNLOAD_PREFIX):
        raise ValueError("Release asset must be hosted by the expected GitHub repository.")
    request = urllib.request.Request(url, headers={"User-Agent": "hve-core-codex-release-installer"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as stream:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > 512 * 1024 * 1024:
                raise ValueError("Release asset exceeds the supported size.")
            stream.write(chunk)


def verified_asset(assets: dict, name: str, directory: Path, fetch=download) -> Path:
    if name not in assets or name + ".sha256" not in assets:
        raise ValueError(f"Release is missing {name} or its SHA-256 asset; select a release with installer assets.")
    path = directory / name
    checksum = directory / (name + ".sha256")
    fetch(assets[name], path)
    fetch(assets[name + ".sha256"], checksum)
    entries = checksum.read_text(encoding="utf-8").splitlines()
    if len(entries) != 1:
        raise ValueError("Expected a single release asset checksum.")
    fields = entries[0].split()
    if len(fields) != 2 or fields[1].lstrip("*") != name or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise ValueError("Malformed release asset checksum.")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual.lower() != fields[0].lower():
        raise ValueError(f"SHA-256 mismatch for {name}; installation stopped.")
    return path


def extract_distribution(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 100_000 or sum(item.file_size for item in members) > 2 * 1024 ** 3:
            raise ValueError("Distribution ZIP exceeds supported extraction limits.")
        names = set()
        for item in members:
            name = item.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or not path.parts:
                raise ValueError(f"Unsafe ZIP path: {name}")
            for part in path.parts:
                stem = part.split(".", 1)[0].upper()
                if part.endswith((".", " ")) or stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
                    raise ValueError(f"Unsafe ZIP path: {name}")
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Distribution ZIP must not contain symlinks.")
            key = name.casefold()
            if key in names:
                raise ValueError(f"Duplicate ZIP path: {name}")
            names.add(key)
        # Validate every member before writing any files.
        for item in members:
            path = destination.joinpath(*PurePosixPath(item.filename.replace("\\", "/")).parts)
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="stable", help="stable, edge, or an exact published release tag")
    parser.add_argument("--target", type=Path, help="Install for one existing project instead of globally")
    parser.add_argument("--no-plugin", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Preview distribution changes; tool installation is still required")
    args = parser.parse_args(argv)
    if args.target and not args.target.is_dir():
        parser.error("--target must be an existing project directory.")
    release = select_release(args.release)
    assets = {item["name"]: item for item in release.get("assets", [])}
    wheels = [name for name in assets if re.fullmatch(r"hve_core_codex-[A-Za-z0-9_.]+-py3-none-any\.whl", name)]
    if len(wheels) != 1:
        raise ValueError("Release must contain exactly one HVE installer wheel.")
    print(f"Installing HVE Core for Codex from {release['tag_name']}", flush=True)
    with tempfile.TemporaryDirectory(prefix="hve-release-install-") as temporary:
        directory = Path(temporary)
        bundle = verified_asset(assets, "dist.zip", directory)
        wheel = verified_asset(assets, wheels[0], directory)
        distribution = directory / "distribution"
        extract_distribution(bundle, distribution)
        if not (distribution / "generated/provenance.json").is_file():
            raise ValueError("ZIP does not contain an HVE distribution.")
        # A persistent tool environment keeps installed hook interpreter paths valid.
        subprocess.run(["uv", "tool", "install", "--force", str(wheel)], check=True)
        tool_root = Path(subprocess.check_output(["uv", "tool", "dir"], text=True).strip())
        executable = tool_root / "hve-core-codex" / ("Scripts/hve-codex.exe" if os.name == "nt" else "bin/hve-codex")
        command = [str(executable), "update" if args.update else "install", "--distribution", str(distribution)]
        command += ["--target", str(args.target)] if args.target else ["--global"]
        if args.no_plugin:
            command.append("--no-plugin")
        if args.dry_run:
            command.append("--dry-run")
        tool_environment = os.environ.copy()
        tool_environment.pop("PYTHONPATH", None)
        tool_environment.pop("PYTHONHOME", None)
        subprocess.run(command, check=True, env=tool_environment)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Release installation failed: {exc}") from exc
