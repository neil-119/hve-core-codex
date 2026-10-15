#!/usr/bin/env bash
# Rebuild a reviewed source lock outside the checkout; candidate metadata is data only.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: rebuild_release_distribution.sh CHANNEL EXTERNAL_OUTPUT" >&2
  exit 2
fi

channel="$1"
output="$2"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
case "$channel" in
  stable|prerelease|edge) ;;
  *) echo "Unsupported source-lock channel: $channel" >&2; exit 2 ;;
esac

lock_path="$repo_root/release/$channel/source-lock.json"
manifest_path="$repo_root/release/$channel/release-manifest.json"
checksums_path="$repo_root/release/$channel/checksums.txt"
runtime_evidence_path="$repo_root/release/$channel/runtime-evidence.json"
lock_fields="$RUNNER_TEMP/release-lock-$channel.txt"

for path in "$lock_path" "$manifest_path" "$checksums_path"; do
  if [[ -L "$path" || ! -f "$path" ]]; then
    echo "Reviewed release metadata is missing or unsafe: $path" >&2
    exit 1
  fi
done
if [[ -L "$runtime_evidence_path" ]]; then
  echo "Reviewed runtime-evidence metadata must not be a symlink." >&2
  exit 1
fi

PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}" uv run --project "$repo_root" --locked python - "$lock_path" "$manifest_path" "$channel" "$lock_fields" <<'PY'
import json
import re
import sys
from pathlib import Path

from hve_codex_porter.release import validate_source_ref

lock_path, manifest_path, expected_channel, output_path = sys.argv[1:]
lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
if not isinstance(lock, dict) or not isinstance(manifest, dict):
    raise SystemExit("Source lock and release manifest must be JSON objects.")
if lock.get("schema_version") != 1 or lock.get("channel") != expected_channel:
    raise SystemExit("Source lock schema or channel is unsupported.")
if manifest.get("schema_version") != 1 or manifest.get("channel") != expected_channel:
    raise SystemExit("Release manifest schema or channel is unsupported.")
source = manifest.get("source")
converter = manifest.get("converter")
if not isinstance(source, dict) or not isinstance(converter, dict):
    raise SystemExit("Release manifest source or builder provenance is malformed.")
source_ref = validate_source_ref(lock.get("upstream_ref"), field="source-lock upstream ref")
source_sha = lock.get("upstream_sha")
adapter_revision = lock.get("adapter_revision")
builder_commit = lock.get("builder_commit")
build_inputs_sha256 = lock.get("build_inputs_sha256")
for field, value in (("upstream SHA", source_sha), ("adapter revision", adapter_revision), ("builder commit", builder_commit)):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SystemExit(f"Source lock {field} must be a full lowercase Git SHA.")
if not isinstance(build_inputs_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", build_inputs_sha256):
    raise SystemExit("Source lock build-input digest must be a lowercase SHA-256 value.")
if (source.get("ref"), source.get("sha"), manifest.get("adapter_sha"), converter.get("sha")) != (
    source_ref, source_sha, adapter_revision, builder_commit
):
    raise SystemExit("Source lock does not match the reviewed manifest source, adapter, or builder pin.")
if source.get("repository") != "microsoft/hve-core":
    raise SystemExit("Release manifest does not name the expected upstream repository.")
Path(output_path).write_text(
    "\n".join((source_ref, source_sha, adapter_revision, build_inputs_sha256, builder_commit)) + "\n",
    encoding="utf-8",
)
PY

mapfile -t lock_values < "$lock_fields"
if [[ ${#lock_values[@]} -ne 5 ]]; then
  echo "Could not read the validated source-lock fields." >&2
  exit 1
fi
source_ref="${lock_values[0]}"
source_sha="${lock_values[1]}"
adapter_revision="${lock_values[2]}"
expected_inputs_sha256="${lock_values[3]}"
builder_commit="${lock_values[4]}"

if [[ -e "$output" ]]; then
  echo "External distribution output already exists; refusing to reuse it: $output" >&2
  exit 1
fi
mkdir -p "$(dirname -- "$output")"
builder_dir="$(mktemp -d "$RUNNER_TEMP/hve-builder-$channel.XXXXXX")"
source_dir="$(mktemp -d "$RUNNER_TEMP/hve-upstream-$channel.XXXXXX")"
git cat-file -e "$builder_commit^{commit}"
git archive "$builder_commit" | tar -x -C "$builder_dir"
actual_inputs_sha256="$(python3 "$builder_dir/.github/scripts/build_input_fingerprint.py" "$builder_dir")"
if [[ "$actual_inputs_sha256" != "$expected_inputs_sha256" ]]; then
  echo "Pinned builder contents do not match the source lock's build-input digest." >&2
  exit 1
fi

git -c core.hooksPath=/dev/null -c core.autocrlf=false init "$source_dir"
git -c core.hooksPath=/dev/null -c core.autocrlf=false -C "$source_dir" remote add origin https://github.com/microsoft/hve-core.git
git -c core.hooksPath=/dev/null -c core.autocrlf=false -C "$source_dir" fetch --no-tags --depth=1 origin "$source_sha"
git -c core.hooksPath=/dev/null -c core.autocrlf=false -C "$source_dir" checkout --detach FETCH_HEAD
actual_source_sha="$(git -c core.hooksPath=/dev/null -c core.autocrlf=false -C "$source_dir" rev-parse HEAD)"
if [[ "$actual_source_sha" != "$source_sha" ]]; then
  echo "Upstream checkout did not resolve to the locked immutable commit." >&2
  exit 1
fi

uv sync --project "$builder_dir" --locked --all-groups
bash "$builder_dir/scripts/build.sh" \
  --output "$output" \
  --source-dir "$source_dir" \
  --channel "$channel" \
  --ref "$source_ref" \
  --sha "$source_sha" \
  --adapter-revision "$adapter_revision"
uv run --project "$builder_dir" --locked hve-codex validate --output "$output" --record

mkdir -p "$output/generated"
cp "$manifest_path" "$output/generated/release-manifest.json"
cp "$checksums_path" "$output/generated/checksums.txt"
# The envelope alone does not satisfy stable release eligibility: the verifier
# also requires file-backed captures under generated/runtime-evidence/. This
# source-only rebuild does not transport those reviewer-authored capture files,
# so the unchanged strict gate remains fail-closed until they are supplied.
if [[ -f "$runtime_evidence_path" ]]; then
  cp "$runtime_evidence_path" "$output/generated/runtime-evidence.json"
fi
printf '%s\n' "$builder_dir" > "$RUNNER_TEMP/hve-release-builder-$channel.txt"
printf '%s\n' "$repo_root/release/$channel/source-lock.json" > "$RUNNER_TEMP/hve-release-lock-$channel.txt"
echo "Rebuilt locked $channel distribution at $output from $source_ref ($source_sha)."
