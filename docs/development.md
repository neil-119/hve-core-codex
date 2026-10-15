# Development and detailed setup

An independent, deterministic port of Microsoft HVE Core's agent-facing distribution. It provides a portable skills plugin and a managed overlay for custom agents, scoped instructions, and advisory hooks. It does not imply Microsoft endorsement.

## Current distribution

This is a source-only builder repository. It contains no upstream checkout, generated plugin, overlay, or distribution reports. Builds fetch an immutable upstream source and generate those files outside this repository. The Python wheel contains the migrator and its supporting schemas/tests/backend, not an HVE distribution. Counts are discovered from source, never hard-coded in the converter.

Read `generated/compatibility-report.md` inside your built distribution before use. Inventory representation and structural validation do **not** establish operational parity. Stable publication is blocked until the required Codex CLI/Desktop and workflow evidence is reviewed. Optional services require your own authenticated connectors. Installing this plugin does not provision them.

Upstream skill invocation flags have different meanings. `disable-model-invocation: true` becomes Codex's explicit-only policy. `user-invocable: false` keeps automatic selection available but asks the source host to hide manual invocation. The tested Codex skill policy has no equivalent manual-visibility setting, so these skills remain discoverable and manually callable; conversion reports that limitation. Hiding them as resource files would also remove their automatic skill selection.

See [local validation results and remaining acceptance checks](validation.md).

## Develop and generate

Requires Python 3.11+, Git, and uv. Runtime workflows may declare additional dependencies. Installation never silently installs those dependencies.

```powershell
uv sync --locked
uv run hve-codex inventory --ref hve-core-v3.2.2
uv run hve-codex validate --output ../hve-core-codex-artifacts/stable
uv run pytest
uv run ruff check src tests/unit tests/integration tests/e2e
uv build
```

Build in Bash, including WSL, after installing Python 3.11+, Git, uv, and the locked dependencies in that environment:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/hve-core-codex/venv"
uv sync --locked
bash scripts/build.sh --channel stable --output ../hve-core-codex-artifacts/stable
```

The build downloads upstream into a temporary checkout, verifies immutable source bytes, runs the migrator, structurally validates the result, and produces `<output>.zip` and `<output>.tar.gz`, each with a SHA-256 file. Output and optional `--source-dir` must be outside this repository. Pin both `--ref` and `--sha` for a reproducible historical build; `--source-dir` selects an already acquired immutable checkout for offline use. `HVE_BUILD_PYTHON` can select an existing Python interpreter with locked dependencies when uv is unavailable. `--archive-only` repackages validated external output after release metadata generation. No upstream script or model is run.

Always pass `--distribution <external-output>` to installation/evaluation commands, or set `HVE_CODEX_DISTRIBUTION`. The fallback location is `~/.codex/hve-core-codex-distribution`; a missing distribution produces an actionable error. When a release archive is available, verify its checksum and extract it into an external distribution directory instead of building.

On Windows behind a system-trusted certificate authority use `uv --native-tls`; Git acquisition uses Windows Schannel. Certificate verification is never disabled.

For a Codex connection that fails with `UnknownIssuer`, its [documented custom-CA support](https://learn.chatgpt.com/docs/auth) accepts `CODEX_CA_CERTIFICATE` pointing to a PEM bundle of already trusted public CA certificates. Set it only for the intended process; do not disable certificate verification or change security software. The local pilot used this mechanism.

Use `--upstream-dir <immutable-checkout> --ref <ref> --sha <full-SHA>` for an existing source. Dirty checkouts and mismatched SHAs are rejected. Without a ref, network acquisition discovers the selected channel's latest published source; `edge` pins `main`. Explicit conversion does not imply publication approval.

Two identical conversions with the same source, converter, mapping and settings must have identical generated file bytes, including reports and checksum manifests. Structural validation checks declared mapped outputs and immutable source originals as well as format, references, and checksums. `validate --record` deliberately records structural evidence and refreshes checksums; run it before creating a release manifest. Plain validation is read-only. `validate --release-gate` additionally fails when compatibility blockers remain.

The active test scope is migration correctness. `pytest` exercises conversion and its local contracts without paid model sessions; intentionally broken behavioral-eval fixtures are excluded from the Ruff command above. Codex startup, sandbox setup, authentication, hook trust, and live workflow evaluation are a separate workstream and must not be treated as converter failures. See [validation scope](validation.md#active-scope-migration-correctness).

## Install into a selected project

```powershell
uv run hve-codex install --scope project --target C:/path/to/project --mode full --dry-run --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex install --scope project --target C:/path/to/project --mode full --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex doctor --target C:/path/to/project
```

Review the dry-run operations and diffs. Existing AGENTS.md, config, hooks, and marketplace entries are preserved through owned sections. Ownership baselines, backups, and conflicts are stored under `.codex/hve-core-codex`. Updates stop on incompatible edits; uninstall preserves modified files and reports leftovers.

The plugin and its resources are registered with the project marketplace, and custom agents/rules are installed under `.codex`. Restart the client if discovery has not refreshed. Marketplace registration is distinct from installation/activation:

```powershell
codex plugin marketplace add C:/path/to/project
codex plugin add hve-core-codex@hve-core-codex-local
codex plugin list --json
```

The source-only builder repo is not a plugin marketplace. Use the marketplace inside a downloaded/built distribution, or the personal marketplace registered by our installer. A Git-backed public catalog would need a separate distribution repository. Installing only the portable plugin does not install custom agents or the rule overlay.

Use `--no-plugin` for standalone skill discovery, or `--no-hooks` for explicit rule lookup. The installer avoids duplicate skill discovery. Hooks require the client's trust review and remain advisory. The installer never trusts hooks/projects, disables approvals, or expands network privileges. The generated AGENTS section points to the general upstream rules; read those and resolve specialty rules before edits.

### Global installation

Install once for your user to share the distribution across repositories. `--global` is an alias for `--scope user`, supported by install, update, doctor, and uninstall:

```powershell
uv run hve-codex install --global --no-plugin --dry-run --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex install --global --no-plugin --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex doctor --global
uv run hve-codex update --global --no-plugin --dry-run --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex update --global --no-plugin --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex uninstall --global --dry-run
```

Standalone skills live in `~/.agents/skills`; agents, hooks, rules, managed global guidance, and ownership records live under `~/.codex`. Existing personal settings and unrelated files are preserved through the same ownership/conflict handling as project installs. Review `/hooks` in Codex and restart the client if discovery has not refreshed. Plans, scratch work, and workflow outputs belong to the active repository, not the shared installation. Windows and WSL have separate homes and require separate installs.

The hook resolves file paths relative to the active repository, including when Codex starts in a nested folder. If a repository has its own HVE rule registry, that registry is selected before the shared registry; rules are not merged. Codex can display both global and project skills with the same name, so avoid installing identical HVE skills at both scopes.

To move existing project installs to global: preview and perform the global install, then use `uninstall --target <repo> --dry-run` for each previously installed project. Review its reported modifications and leftovers before running `uninstall --target <repo>`. Uninstall removes owned components; retain any local customizations you still need. Project installation remains useful for a separately pinned distribution, but global skills may still appear alongside it.

For isolated testing with a custom Codex home, select both `--codex-home <temporary-personal-root>/.codex` and `--user-home <temporary-personal-root>`; Codex home must stay inside that root. These options select the installer paths; run Codex with the matching home separately. No implicit writes to an unrelated repository occur. Global installation is checked offline; live global discovery and trust behavior remain unverified without a separate runtime acceptance run.

```powershell
uv run hve-codex update --target C:/path/to/project --dry-run --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex update --target C:/path/to/project --distribution ../hve-core-codex-artifacts/stable
uv run hve-codex uninstall --target C:/path/to/project --dry-run
uv run hve-codex uninstall --target C:/path/to/project
```

Use `--distribution <candidate>` to install/update another built distribution. The wheel includes migrator logic, mappings, schemas and custom scenario definitions, without a generated distribution.

### Build edge instead of stable

Build an edge candidate outside the repository, inspect its compatibility report, then explicitly select it for installation:

```powershell
bash scripts/build.sh --channel edge --output ../hve-core-codex-artifacts/edge
uv run hve-codex validate --output ../hve-core-codex-artifacts/edge
New-Item -ItemType Directory -Force C:/repos/my-hve-project | Out-Null
uv run hve-codex install --distribution ../hve-core-codex-artifacts/edge --target C:/repos/my-hve-project --no-plugin --dry-run
uv run hve-codex install --distribution ../hve-core-codex-artifacts/edge --target C:/repos/my-hve-project --no-plugin
uv run hve-codex doctor --target C:/repos/my-hve-project
```

Run these commands from a normal user terminal on Windows. The target directory must exist. Conversion records the exact upstream SHA in `generated/provenance.json`; a newer upstream revision may require a reviewed adapter before conversion succeeds. Structural validation does not establish runtime acceptance. Review `/hooks` inside the installed project. For updates, pass the same explicit `--distribution` choice to `update` so the target follows the intended channel.

## Invoke workflows and scoped rules

Invoke `$hve-catalog` to look up every upstream alias and required capability. Select the returned `$skill-name` explicitly. User-facing agents have entry-point skills; their TOMLs provide custom delegated roles. Copilot handoff buttons become named chat actions retaining the original prompt, arguments and gates. Explicit-only entry points declare `allow_implicit_invocation: false`.

```powershell
uv run hve-codex rules list
uv run hve-codex rules match src/example.py src/Example.cs
uv run hve-codex rules explain src/example.py
uv run hve-codex rules --root codex-overlay/rules match src/example.py
```

Rules use normalized repository-relative paths, source globs, import closure, and stable ordering. Shell commands with concealed targets do not establish exact rule coverage. Mandatory policies need separate checks rather than relying solely on hook context.

## Evaluation and release

Upstream Vally specs and fixtures are deterministically converted. Vally retains their graders, scoring, and run counts. Unsupported capabilities are reported before execution; upstream CI and eval scripts are never executed during conversion.

```powershell
uv run hve-codex eval vally-prepare --upstream-dir .cache/upstream --distribution . --output .cache/vally-input
uv run hve-codex eval vally-run --bundle .cache/vally-input --output .cache/vally-plan --dry-run
```

Real model sessions are explicitly invoked, consume configured Codex account access, and run in disposable fixture repositories with declared budgets:

```powershell
uv run hve-codex eval vally-run --bundle .cache/vally-input --output runtime-results --eval script-validation
```

Review deterministic assertions, captured sessions and outcome rubrics. A CLI run cannot certify IDE behavior. Missing review, untrusted hooks, missing connectors, or unavailable runtime features remain named blockers. Do not claim a passing result merely from installation, help output, static fixtures, or mocked clients.

See [release setup and evidence contracts](release.md) for scheduled sync, GitHub App permissions, protected branches/environments, bot PR gating, immutable tags, attestations, signatures, channel opt-in and credential/event chaining. No GitHub repository, remote mutation, release, or model evaluation is performed merely by developing this project.

## Repository layout

```text
src/hve_codex_porter/  converter, CLI, installer, rules, eval and release helpers
mappings/             reviewed capability mappings and semantic overrides
schemas/              pinned offline packaging schema
plugins/hve-core-codex/ portable generated plugin and complete supporting resources
codex-overlay/        agents, scoped registry, hooks and managed AGENTS guidance
generated/            source inventory, provenance, mapping, reports and hashes
tests/                unit, subsystem integration and explicit runtime scenarios
.github/workflows/    scheduled discovery, validation and protected publication
specs/SPEC.md         original implementation specification
```

## Contribute and troubleshoot

Add conversion semantics to reviewed mappings or narrowly scoped converter code with fixtures. Unknown tools, behavior fields, artifacts, missing references, lost gates and license changes require review. Routine builds do not invoke an LLM.

Use `doctor` for owned-file/config conflicts and prerequisites. Preserve the reported backups before resolving conflicts. For unsupported clients use standalone skills and explicit rule retrieval, and retain the reported fidelity limitations. Runtime dependencies require explicit installation/authentication; never place credentials in the generated tree.
