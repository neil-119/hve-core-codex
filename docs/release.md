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

## Automatic weekly publication

`weekly-sync.yml` runs Mondays at 09:00 UTC and can also be dispatched for a retry. It selects the newest published Microsoft stable release and newest upstream `main` commit for edge. Channels run independently. Already published sources are skipped; there is no backlog replay, metadata PR, environment approval, or routine owner task.

Both channels use the migrator on protected `main`, whose exact commit must have a successful push validation run. Upstream input is pinned to a full commit SHA and treated as data: upstream scripts are never executed. Generated content stays outside the repository and is packaged as release assets.

The read-only build job runs unit tests, lint, a locked dependency audit, structural validation, conversion inventory/reference coverage, an install/update/uninstall smoke test, and archive checksum verification. Unknown behavior-bearing fields, unresolved references, invalid output paths and other compatibility blockers fail the job. The publisher downloads the exact artifact ID and checks a digest over every asset name and byte, current protected main, latest upstream source, release channel and immutable tag before publication. Only the publication job can write releases. An existing tag is never moved; an already published source is a no-op. A failed tag-only attempt can be retried against the same port commit.

Releases include `dist.zip`, `dist.tar.gz`, an installer wheel, SHA-256 files, the release manifest, compatibility report, generated checksums, and migration eligibility result. Stable is a normal release; edge is a prerelease. The built-in GitHub token is sufficient. No GPG key, paid model access, or review environment is required. Repository visibility is unchanged.

The automated policy is explicitly **migration-only** (`release eligibility --validation-policy migration`). It does not claim fresh live CLI/Desktop behavior or Vally model acceptance. Missing runtime evidence remains visible but does not block this policy. Historical evaluation failures and deferred host limitations remain documented. Actual conversion or validation failures block publication.

On failure, a separate job creates or updates an assigned GitHub issue with the Actions run URL. Successful and unchanged runs do not create issues. Notification delivery follows the owner's GitHub account settings.

## Optional runtime-evidence publication

`publish.yml` and manual `sync-upstream.yml` retain the stricter candidate-specific evidence process. They are not called by the weekly workflow. Their protected channel metadata and CLI/Desktop evidence requirements apply only when explicitly using that path. The evidence delivery and historical acceptance contracts below describe this optional path, not automatic weekly prerequisites.

## Release-based end-user installation

The README uses `uv run` with `scripts/install_release.py` from the public repository. The dependency-free launcher chooses the latest published stable release, the latest published edge prerelease, or an exact GitHub release tag. It checks the ZIP and wheel SHA-256, rejects unsafe archive paths/symlinks, installs the wheel into a persistent uv tool environment, then invokes its managed installer on the downloaded distribution. Temporary downloads are cleaned up; Codex trust is never granted automatically. No Git checkout, upstream conversion, or WSL is required for end users. GitHub releases must include `dist.zip`, `dist.zip.sha256`, exactly one `hve_core_codex-...-py3-none-any.whl`, and its `.sha256` asset. Older releases lacking these assets report an error. Automatic weekly publication uses the migration-only gates above.

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
