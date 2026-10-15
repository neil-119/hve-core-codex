"""Print a deterministic digest of files that affect generated release output."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


EXPLICIT_INPUTS = (
    "scripts/build.sh",
    "vally-executor/navigation.json",
    "scripts/build_distribution.py",
    "tests/codex-host/stable.json",
    "tests/codex-host/edge.json",
)


def input_paths(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for directory in ("src", "mappings", "schemas"):
        base = root / directory
        if not base.is_dir() or base.is_symlink():
            raise SystemExit(f"Missing or unsafe build input directory: {directory}")
        for path in base.rglob("*"):
            if "__pycache__" in path.relative_to(root).parts:
                continue
            if path.is_symlink():
                raise SystemExit(f"Symlink is not allowed in build inputs: {path.relative_to(root)}")
            if path.is_file():
                if directory == "src" and path.suffix != ".py":
                    continue
                paths.add(path)
    for relative in EXPLICIT_INPUTS:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Missing or unsafe build input: {relative}")
        paths.add(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def fingerprint(root: Path) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    for path in input_paths(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


if __name__ == "__main__":
    repository = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    print(fingerprint(repository))
