"""Compare every generated file by SHA-256; never normalize or hide differences."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def checksums(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    for root in (args.baseline, args.candidate):
        if not root.is_dir():
            parser.error(f"Distribution directory missing: {root}")
    before, after = checksums(args.baseline), checksums(args.candidate)
    missing = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    changed = [{"path": name, "baseline_sha256": before[name], "candidate_sha256": after[name]}
               for name in sorted(before.keys() & after.keys()) if before[name] != after[name]]
    report = {"baseline": str(args.baseline.resolve()), "candidate": str(args.candidate.resolve()),
              "baseline_files": len(before), "candidate_files": len(after),
              "matching_files": sum(before[name] == after[name] for name in before.keys() & after.keys()),
              "missing": missing, "added": added, "changed": changed,
              "identical": not (missing or added or changed)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"changed", "missing", "added"}}))
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
