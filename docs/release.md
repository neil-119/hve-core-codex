# Upstream builds and releases

This repository contains the migrator, mappings, schemas, tests, and automation. Upstream checkouts, original mirrors, generated skills/agents/hooks, and distribution reports belong outside it. The Python wheel contains migrator tooling; the distribution archive contains the HVE port.

## Build locally

Run in Bash, including WSL, with Python 3.11+, Git, uv and the locked environment:

```bash
bash scripts/build.sh --channel stable --output ../hve-core-codex-artifacts/stable
bash scripts/build.sh --channel edge --output ../hve-core-codex-artifacts/edge
```

Each build fetches upstream into a temporary checkout, pins its commit, verifies source bytes, runs conversion, and validates the external result. It creates deterministic `<output>.zip` and `<output>.tar.gz` archives, each with a SHA-256 file. Use `--ref` plus `--sha` to reproduce an immutable historical source; use `--source-dir` for an already acquired immutable checkout. No upstream script or model runs. Never edit migrated output: change the migrator and rebuild.

`--archive-only` repackages validated external output after automated release metadata is generated. It archives only managed distribution paths, excluding unrelated files. Archive order, owners, modes and timestamps are normalized for deterministic bytes.

## Weekly source review

`weekly-sync.yml` runs on Mondays at 09:00 UTC. It considers the latest published stable source and the latest upstream `main` commit for edge. Prerelease sync remains manual; it is excluded from automatic publication. Push events only validate changes.

The sync workflow uses the same Bash builder. It compares the upstream pin and a fingerprint of relevant builder inputs with the reviewed channel record. Documentation edits, bytecode caches, and metadata-only merge commits do not cause another build candidate. Immutable builder and adapter commit pins remain recorded for reproduction.

A changed candidate produces an Actions artifact containing its distribution archive and review reports. A draft PR stages only:

```text
release/<channel>/source-lock.json
release/<channel>/release-manifest.json
release/<channel>/checksums.txt
```

The source lock records the schema, channel, upstream ref/SHA, adapter revision, builder commit, and build-input SHA-256. The manifest binds generated file hashes, mapping and scenario hashes, licensing/provenance, and any reviewed runtime envelope. Generated plugin trees and originals are never bot commits in this repository.

Sync PRs require human review and merge. Automatic merge is disabled while the source-only evidence flow is established. Review the Actions archive, compatibility warnings, immutable pins, and required runtime results; missing evidence is not a pass.

## Publication gates

The publisher reads metadata from the protected channel branch (`main` for stable, `edge` for edge). It skips missing/unreviewed records, older upstream candidates, and already published sources before obtaining signing credentials.

For an eligible record, `.github/scripts/rebuild_release_distribution.sh` extracts the exact pinned builder commit into temporary storage, verifies its input fingerprint, fetches the exact upstream source, and calls that builder's `scripts/build.sh`. Automated structural validation is recorded before restoring reviewed manifest/checksum metadata. The existing strict release eligibility checks compare rebuilt hashes and runtime evidence.

Stable requires the original/custom Vally evidence and reviewed CLI/Desktop acceptance envelope. Edge requires explicit development-snapshot opt-in and cannot masquerade as stable. Unknown mappings, missing references, licensing/security changes, checksum mismatches, missing captures, and unverified required behavior block publication. The user's implementation closeout and paid-eval pause do not clear these gates.

The publisher repeats branch-head, latest-source, hash, and eligibility checks after approval of its protected environment. It creates a signed immutable tag pointing to source-only builder/lock metadata and attaches the distribution ZIP/tar archives, their SHA-256 files, the installer wheel and its SHA-256, manifest, and generated checksums to the GitHub release. Existing published tags are never rewritten.

## Evidence and setup still required

Configure protected `main`/`edge` branches, the sync GitHub App credentials, protected `release-stable`/`release-edge` environments with human approval, and the publisher's App/signing secrets before using Actions publication. Inspect the workflow files for their exact secret names and permissions. No local task runs these workflows or publishes automatically.

Reviewed `release/<channel>/runtime-evidence.json` is restored as an envelope. Its referenced raw CLI captures and Vally evidence must also be available under the external distribution's expected paths; the current helper does not download an external evidence bundle. Missing files remain blocking. This delivery step and live GitHub Actions execution remain unverified; Desktop validation is manual and the full behavioral suite is deferred. Do not check migrated source or large runtime bundles into the builder repo to work around this gap.

For installation, verify and extract a release archive outside this repository, then use `hve-codex install --distribution <external-directory>` with project or global scope. The builder Git repository itself is no longer a plugin marketplace; the generated archive includes the marketplace catalog used by the installer/Desktop.

## Release-based end-user installation

The README uses `uv run` with `scripts/install_release.py` from the public repository. The dependency-free launcher chooses the latest published stable release, the latest published edge prerelease, or an exact GitHub release tag. It checks the ZIP and wheel SHA-256, rejects unsafe archive paths/symlinks, installs the wheel into a persistent uv tool environment, then invokes its managed installer on the downloaded distribution. Temporary downloads are cleaned up; Codex trust is never granted automatically. No Git checkout, upstream conversion, or WSL is required for end users. GitHub releases must include `dist.zip`, `dist.zip.sha256`, exactly one `hve_core_codex-...-py3-none-any.whl`, and its `.sha256` asset. Older releases lacking these assets report an error. Publication still requires the existing review/evidence gates.
