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

Stable requires either the legacy full-suite Vally verification or the explicitly selected historical behavioral acceptance policy, plus the reviewed candidate-specific CLI/Desktop acceptance envelope. Edge requires explicit development-snapshot opt-in and cannot masquerade as stable. Unknown mappings, missing references, licensing/security changes, checksum mismatches, missing captures, and unverified required behavior block publication. The user's implementation closeout and paid-eval pause do not clear these gates.

The publisher repeats branch-head, latest-source, hash, and eligibility checks after approval of its protected environment. It creates an unsigned immutable tag pointing to source-only builder/lock metadata and attaches the distribution ZIP/tar archives, their SHA-256 files, the installer wheel and its SHA-256, manifest, and generated checksums to the GitHub release. Existing published tags are never rewritten.

## Evidence and setup still required

Configure protected `main`/`edge` branches and protected `release-stable`/`release-edge` environments with human approval. Publication and sync use the built-in GitHub token; no custom App or GPG secrets are required. Dispatch and publication still require owner authorization.

Reviewed `release/<channel>/runtime-evidence.json` is restored as an envelope. Its referenced raw CLI captures and Vally evidence must also be available under the external distribution's expected paths; the rebuild helper restores captures from a pinned same-repository Actions artifact when an evidence-source record is supplied. Missing files remain blocking. The artifact transport described below supplies file-backed captures; Desktop validation remains manual. Historical model-suite results are preserved separately. Do not check migrated source or large runtime bundles into the builder repo to work around this gap.

For installation, verify and extract a release archive outside this repository, then use `hve-codex install --distribution <external-directory>` with project or global scope. The builder Git repository itself is no longer a plugin marketplace; the generated archive includes the marketplace catalog used by the installer/Desktop.

## Release-based end-user installation

The README uses `uv run` with `scripts/install_release.py` from the public repository. The dependency-free launcher chooses the latest published stable release, the latest published edge prerelease, or an exact GitHub release tag. It checks the ZIP and wheel SHA-256, rejects unsafe archive paths/symlinks, installs the wheel into a persistent uv tool environment, then invokes its managed installer on the downloaded distribution. Temporary downloads are cleaned up; Codex trust is never granted automatically. No Git checkout, upstream conversion, or WSL is required for end users. GitHub releases must include `dist.zip`, `dist.zip.sha256`, exactly one `hve_core_codex-...-py3-none-any.whl`, and its `.sha256` asset. Older releases lacking these assets report an error. Publication still requires the existing review/evidence gates.

## First edge snapshot with the built-in GitHub token

`edge-snapshot.yml` is a separate, manual development-prerelease path. It leaves the strict stable publication workflow and its evidence requirements intact. Run it from current `main` only after the exact commit's **Validate HVE Core Codex** workflow is green. It fetches an immutable upstream `main` snapshot, runs the deterministic builder outside the checkout, validates and smoke-installs the output, and publishes the ZIP, tar archive, installer wheel, checksums and provenance reports. It runs no paid model evaluations.

Only the publication job has `contents: write`; the build job has read-only permissions. An existing tag or release is never overwritten. Use a higher positive `port_revision` for another port revision of the same upstream commit. All releases from this path are prereleases. They have SHA-256 sidecars but no GPG signature or GitHub artifact attestation; they do not claim stable runtime acceptance. Release notes retain the known validation limitations and distinguish the historical 58.6% graded baseline from the new source snapshot.

The repository can remain private during development. Collaborators can download release assets with authenticated GitHub CLI or the GitHub UI. Once it is public, the README's public `--release edge` installation command can download this prerelease without credentials. Changing repository visibility is a separate owner action.

## Reviewed behavioral acceptance

The stable workflow explicitly selects `mappings/release-acceptance.json`. It records the owner's acceptance of the historical Luna medium baseline: 1,310 passed, 927 failed, 439 blocked; 58.6% of graded trials. It binds the preserved reports by SHA-256 of their UTF-8 text with LF newlines, making the review independent of Windows checkout line endings. Failures and blocked trials are retained. This is a reviewed historical acceptance record, not independent re-verification of raw trial captures and not a fresh full-suite result for a newer candidate.

The candidate's runtime envelope must include `acceptance_policy_sha256` matching the exact policy bytes. Required CLI/Desktop checks, their report hashes, human approvals, and candidate source/adapter/converter/mapping/output pins still have to pass. Only `child-read-only-permission` is deferred under this policy; it remains unverified. Missing Desktop approval remains blocking. Omitting `--acceptance-policy` retains the legacy all-pass full-suite verification path. Neither path launches models during publication.

## Credentials after simplification

Release and sync jobs use GitHub's short-lived built-in `GITHUB_TOKEN`; custom GitHub Apps and GPG keys are no longer required. Tags remain immutable and exact-commit checked, but are unsigned. GPG is free and can be added later as a separate publisher-identity control. It does not establish test success. The weekly cadence and stable/edge source selection remain unchanged. Review environments and branch protection remain separate repository controls.

GitHub's built-in token does not trigger ordinary push/PR workflows for bot-created changes. Sync PRs remain manual-review candidates; run the required checks explicitly as appropriate before merging. Do not grant a pass to a check that never ran.

To validate a bot-created sync branch explicitly, run `gh workflow run validate.yml --ref <sync-branch>`. The validation workflow supports manual dispatch and retains read-only permissions.

## Delivering reviewed raw captures

Keep raw captures outside Git. Package exactly the files listed beneath `generated/runtime-evidence/` in the reviewed envelope's `output_hashes`, with that prefix removed inside the ZIP (for example `cli.json` and `desktop.json`). The restorer checks every declared file, rejects extra/missing files and unsafe archive paths, and never executes archive contents.

Commit `release/stable/runtime-evidence.json` plus an `evidence-source.json` alongside the source lock and reviewed release manifest. A same-repository uploaded release asset can use:

```json
{
  "schema_version": 1,
  "source_kind": "release-asset",
  "repository": "neil-119/hve-core-codex",
  "asset_id": 12345,
  "archive_sha256": "<actual SHA-256 of the capture ZIP>",
  "runtime_evidence_sha256": "<actual SHA-256 of the reviewed LF-encoded envelope>"
}
```

For a same-repository Actions artifact, use `source_kind: actions-artifact` and `artifact_id` instead of `asset_id`. IDs and hashes must identify the actual reviewed download; example placeholders are never accepted. An owner can upload local Desktop captures to a designated evidence-staging release with `gh release upload <staging-tag> <capture-zip>`, then record the returned asset ID. Do not replace an asset referenced by an accepted source record. Actions artifacts may expire; a missing or expired download blocks publication and requires a new reviewed source record.

The rebuild helper restores captures before eligibility checks. Its download receipt is written to `RUNNER_TEMP` outside the distribution, preserving the reviewed output hashes. Nothing is auto-approved: candidate-specific CLI/Desktop reports and reviewer decisions still need to be supplied. The optional legacy all-pass mode additionally requires the full Vally capture tree.

Repository setup still needed for strict stable publication: protect the channel branches, configure the release environments/reviewers, and allow Actions to create sync PRs if using the weekly bot. The current private repository has none of those controls configured; visibility changes remain an owner action. The manual edge snapshot path is separate and always publishes a prerelease.
