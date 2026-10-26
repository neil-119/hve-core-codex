# HVE Core to Codex: Full-Fidelity Migration and Automated Upstream Synchronization

**Status:** Implementation specification, v1.2; source-only builder; runtime acceptance is tracked separately
**Date:** 2026-10-13
**Source:** https://github.com/microsoft/hve-core
**Target repository:** `hve-core-codex` (independent migrator logic and build tooling; generated distribution is external)
**Primary target:** Official Codex CLI and Codex desktop; optional plugin distribution wherever supported. VS Code-specific tests and editor integration validation are out of scope. README instructions for using the Codex extension are informational, not acceptance requirements.

## 0. Instructions to the implementing agent

Implement this specification end to end. Do not stop at a proof of concept, a partial set of skills, or documentation-only changes. Discover the actual upstream inventory at execution time. Build a deterministic converter, package every in-scope artifact, implement installation and ongoing upstream synchronization, write automated tests, and produce a validated Codex distribution. Make defensible implementation decisions without asking routine questions. Do not claim functional parity for untested workflows or unavailable external services. Record limitations explicitly and block stable publication on unresolved critical conversion defects. Do not create a GitHub repository, push, merge, or release without an authorized repository and credentials.

## 1. Goals, scope, and acceptance philosophy

The Git repository must contain migrator logic, mappings, schemas, tests, documentation, and build/release automation only. Do not check in upstream sources, original resource mirrors, converted Markdown, generated plugin/overlay directories, or distribution reports. Fetch upstream at build time into an external temporary checkout. `scripts/build.sh` is the shared local/WSL/Actions entrypoint; it accepts immutable source pins, generates a validated external distribution, and creates a deterministic archive with SHA-256. The standalone Python package ships migrator logic and supporting resources, never migrated HVE content. Installers consume an explicit external distribution or `HVE_CODEX_DISTRIBUTION`. Actions reviews compact source-lock/release evidence metadata under `release/{channel}/`, builds artifacts externally, and publishes approved archives rather than committing generated trees. Retain all existing release and runtime evidence gates. Never manually edit migrated output: change migrator logic and regenerate. Compare all saved output checksums against exact-converter regeneration when changing this architecture.

The product is an automated, maintainable Codex port of the **entire HVE Core agent-facing distribution**, not just the RPI workflow. Preserve original functionality, language, user-facing entry points, state-management conventions, human-review gates, artifact schemas, delegated agent responsibilities, and supporting resources as closely as the Codex host allows.

Success has three independent dimensions:

1. **Inventory completeness:** Every applicable upstream artifact is discovered, tracked, and represented in the output or explicitly accounted for as host-specific. Zero silent omissions.
2. **Conversion correctness:** The generated artifacts use valid Codex/Agent Skills formats, resolve references, and retain their intended instructions and resources.
3. **Behavioral compatibility:** Workflows are discoverable, executable, properly gated, and validated in real Codex sessions. An artifact that merely parses is not automatically behaviorally compatible.

At the time of initial inspection, upstream `main`'s `plugin.json` listed **85 skills, 60 agents, 53 rules/instructions, and 0 commands**, but these are only a baseline. The build must not hard-code counts, paths, host features, or version numbers. Compare the manifest against the actual checked-out tree, including unlisted material, before conversion. Upstream `main` and tagged releases may differ; always pin the exact ref and SHA for each build.

**In scope:** `.github/skills/**` (including all references, scripts, templates, assets), `.github/agents/**/*.agent.md` (both user-facing agents and subagents), `.github/instructions/**/*.instructions.md`, `.github/copilot-instructions.md`, `.github/prompts/**/*.prompt.md` and command entries if present, plugin membership and metadata, referenced schemas and data, source files needed by runtime workflows, relevant usage documentation, and `.vscode`/Copilot settings to the extent that they define runtime behavior. Discover any newly introduced artifact kinds and surface them for review rather than ignoring them.

**Out of scope as direct binary ports:** The VS Code extension implementation/VSIX, Microsoft's GitHub CI infrastructure, unrelated Docusaurus site tooling, and non-agent-facing product code. Nevertheless inventory these and document any user-visible capability they supplied; implement an equivalent host integration where feasible. An excluded file must have a documented classification and rationale. Do not run or copy the upstream CI/workflow scripts indiscriminately.

**Non-goals:** Rewriting HVE's methodology; silently simplifying governance; hard-wiring OpenAI model names into migrated roles or paid feature assumptions; claiming that installing a plugin provisions third-party cloud services; automatically obtaining permissions or bypassing human approval; maintaining a custom Codex executable or a replacement behavioral evaluation engine.

Use official, unmodified OpenAI Codex. Environment issues such as authentication, hook trust, Windows sandbox provisioning, and runtime locks are a separate workstream. Do not repair Codex itself from this repository. Surface host limitations for user decisions. The agreed default is to preserve workflows with accurate warnings when native enforcement or UI parity cannot be established.

## 2. Required distribution architecture

Use **two complementary deliverables**, not a plugin alone:

- **Portable Codex plugin:** Skills, resources, metadata and optional compatible MCP configuration. Follow the current portable Agent Plugins convention: root `plugin.json` with `$schema: https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`, root `skills/`, optional root `mcp.json`, and `extensions.com.openai` for Codex-specific settings. A `.codex-plugin/plugin.json` compatibility overlay is optional, not the canonical manifest. Do not reuse upstream Copilot's `plugin.json` schema verbatim.
- **Managed Codex installation overlay:** The custom subagent TOMLs, instruction registry, project/user `AGENTS.md` integration, scoped-rule resolver and hook scripts, and dependency configuration that the portable plugin format does not automatically install as project settings.

Repository structure (refine only if validation demonstrates a better one):

```text
hve-core-codex/
  README.md
  LICENSE
  THIRD-PARTY-NOTICES.md
  pyproject.toml
  src/hve_codex_porter/
    inventory.py
    conversion.py
    references.py
    agent_converter.py
    skill_converter.py
    instruction_converter.py
    prompt_converter.py
    capabilities.py
    packaging.py
    installer.py
    release.py
    reporting.py
  mappings/
    tool-adapters.yaml
    workflow-overrides.yaml
    instruction-mappings.yaml
    known-host-limitations.yaml
  plugins/hve-core-codex/
    plugin.json
    skills/<unique-skill-id>/SKILL.md
    mcp.json                     # only when actual supported MCP definitions exist
    assets/ ...
    hooks/ ...                  # optional; requires runtime support and trust
  codex-overlay/
    agents/<unique-agent-id>.toml
    rules/<source-relative-path>.md
    rules/index.json
    hooks/hooks.json
    hooks/hve_rules.py
    AGENTS.hve-core.md
  .agents/plugins/marketplace.json
  generated/
    source-inventory.json
    provenance.json
    capability-map.json
    compatibility-report.json
    compatibility-report.md
    checksums.txt
  tests/
    fixtures/
    unit/
    integration/
    e2e/
  .github/workflows/
    sync-upstream.yml
    validate.yml
    publish.yml
```

Generated files can live in a dedicated `generated` branch if that makes reviewing diffs cleaner, but the repository must expose a clearly versioned, installable distribution on `main` and Git tags. Keep converter source separate from generated content. Prefer Python 3.11+ with pinned dependencies for the converter/installer; avoid involving an LLM in routine release conversions.

## 3. Upstream scanner and source-of-truth inventory

Build a scanner that accepts `--upstream-url`, `--ref`, `--sha`, and `--channel`. Checkout an immutable commit. Enumerate all files, parse the source manifest and YAML frontmatter, and construct an artifact dependency graph. Follow explicit paths from `plugin.json`, discover likely in-scope files on disk, and report differences both ways.

Each inventory record must include: stable artifact ID, type, original path, source SHA/hash, frontmatter fields, user-invocable vs delegated role, dependencies, links/reference targets, required tools/MCP servers, conversion strategy, generated output paths, and status. Represent cross-file `#file:` references, relative Markdown links, embedded images, templates, scripts, schemas, imported instructions, other skills, agent handoffs, and prompt calls as graph edges.

Protect against missing files, duplicate names, case-insensitive filesystem collisions, Unicode/Windows path issues, malformed YAML, directory traversal, escaping symlinks, broken links, and renamed/deleted artifacts. Fail with actionable diagnostics instead of silently omitting them. Preserve source-relative provenance in every generated artifact without modifying original copyright/license claims.

Required inventory status vocabulary: `converted`, `adapted`, `host-specific-alternative`, `requires-external-service`, `unresolved`, `excluded-with-rationale`. The release report must distinguish inventory coverage from *working* capability coverage. New artifact kinds, unsupported source fields, and unresolved references must be treated as explicit compatibility findings.

## 4. Artifact transformation rules

### 4.1 Skills — all of them

Copy every upstream-listed and discovered `SKILL.md` package into the plugin, retaining **all** supporting references, scripts, templates, binary assets and nested documentation. Preserve meaningful frontmatter including `name`, `description`, `license`, and compatibility metadata. Assign collision-free names; maintain an alias map so users can look up upstream names. Do not flatten directories in a way that breaks links.

Convert host-specific instructions when necessary: `/skill` and Copilot agent-picker references become the documented Codex skill invocation model (for example `$rpi-research`), references to `#file:` resolve to installed package-relative locations, and unavailable VS Code tools become tested alternatives. Rewrite syntactic references only outside literal examples and code fences unless the example itself is intentionally migrated. Prefer structured Markdown/YAML parsing and explicit rules over global regular-expression replacements.

Preserve original workflow stages, named artifact files, state/recovery formats, step requirements, security checks, and human gates. Supply dependencies only if necessary and explicitly documented. Build a catalog/lookup or dispatcher skill for the large collection so that skills remain discoverable even when Codex shortens the initial list of skill descriptions due to context budgets. The dispatcher must never conceal or omit the underlying skills.

### 4.2 Agents — all of them, with role-aware targets

Parse every `.agent.md` frontmatter and body, including `name`, `description`, `agents`, `tools`, `handoffs`, `argument-hint`, `user-invocable`, `disable-model-invocation`, and future unknown fields. Unknown behavior-bearing fields must generate a warning or failure, not disappear.

- Generate an installable `.codex/agents/<unique-id>.toml` for each original agent where a delegated execution role is meaningful. Required keys are `name`, `description`, and `developer_instructions`; other keys may be added only if documented and supported. Do not choose a particular model unless the source semantics demand it; normally inherit the parent. Preserve read-only roles and requested write boundaries using the appropriate tool/sandbox configuration, instructions, and testing.
- For each **user-facing Copilot agent**, also provide a Codex *entry-point skill* or dispatcher action that initiates the corresponding interactive workflow. A TOML subagent alone is insufficient to replace a Copilot agent picker entry.
- For explicit child-agent lists, preserve delegation intent: configure child-agent identities and rewrite orchestration into executable instructions for Codex's spawn/wait/resume model. Parallelize only independent tasks and prevent concurrent writes to the same files.
- Convert `handoffs` into explicit named stage/action routing, preserving labels, prompts, state handoff, wait/continue semantics, and approval boundaries. Do not pretend Copilot UI buttons or `send: true` exist in Codex; document the alternative user command and provide an equivalent skill workflow.
- Preserve `disable-model-invocation` and `user-invocable` intent in routing policy, descriptions, and dispatcher tests; mark any non-enforceable difference as a host limitation. Never automatically launch a gated workflow just because it is semantically adjacent.

The RPI Agent, Code Review, Accessibility Planner, Privacy Reviewer, Backlog Manager, BRD/PRD builders and other orchestrators must remain available as user-facing entry points. Pure delegated evaluators/reviewers must remain independently addressable by the orchestrators, without being accidentally exposed as autonomous user workflows.

### 4.3 Scoped instructions — all of them

Codex `AGENTS.md` is **not** a faithful replacement for Copilot's `applyTo` glob selection. Do not concatenate all 53+ rules into one huge root `AGENTS.md` and call it complete.

Generate an indexed, path-scoped instruction registry preserving each original file's `applyTo` patterns, precedence, and content. Include the general `.github/copilot-instructions.md` rules in a concise `AGENTS.hve-core.md` / installation-managed `AGENTS.md` section; keep full specialty instructions on demand. The installer must merge without overwriting a user's existing project rules or exceeding default instruction size limits.

Implement a deterministic rule resolver callable as `hve-codex rules list`, `hve-codex rules match <paths...>` and `hve-codex rules explain <path>`. Use tested glob matching, consistent repo-relative paths, negation/multiple-pattern handling where present, and stable ordering. Provide an adapter using **Codex hooks where supported and explicitly trusted**: `SessionStart`/`SubagentStart` for small indexing guidance, `UserPromptSubmit` for relevant initial context, and `PreToolUse` for file-specific rules before supported write tools. Keep injected context bounded and cache/dedupe within a turn. For shell commands that conceal target file paths, request or discover the target paths rather than asserting exact enforcement. Hooks are advisory unless the runtime explicitly enforces a supported block; implement mandatory policy as separate lint/check or CI protection where appropriate.

Respect user and administrator policies, hook trust prompts, sandbox settings and trusted-project rules. Hooks must not silently grant privileges, expand network access or disable approvals. Where hooks are unsupported (e.g. some hosted/plugin surfaces), provide documented `AGENTS.md`/skill-based fallback and report the fidelity loss. Validate behavior in Codex CLI and applicable Codex desktop surfaces, not just hook JSON syntax. Do not require VS Code-specific tests.

### 4.4 Prompts, commands and UX entry points

Port every `.prompt.md`/manifest command if present at a pinned upstream ref. A zero-count manifest today does not justify omitting this converter. Preserve variables, input prompts, command arguments, default text, references and invocation names. Convert parameterized prompts to skills or a single dispatching skill with an explicit argument contract. Document equivalents for Copilot's `/foo` slash workflow commands, agent-picker entries and handoff buttons. Preserve human-in-the-loop decision gates; if `vscode_askQuestions` or another host UI tool is unavailable, ask for choices in ordinary chat and persist the user's decision where the source requires it. Do not invent tools or claim a clickable UI exists.

### 4.5 Tool, connector and runtime adapters

Implement a table-driven `tool-adapters.yaml` with classifications `native`, `shell`, `MCP`, `ask-user`, `host-ui-alternative`, and `unsupported`. Examples to discover and cover include `read/*`, `search/*`, `edit/*`, `execute/*`, `agent/runSubagent`, `vscode_askQuestions`, `web/*`, `github/*`, `ado/*`, `jira/*`, `microsoft-docs/*`, and `work-iq-mcp`. Map *capabilities*, not just string names. When an external integration is required, declare the specific MCP server, auth/setup prerequisites, read/write scope and failure behavior; ship valid config examples but never credentials. Do not assume that every host has GitHub, Azure DevOps, Jira, Microsoft Docs or Work IQ connected. Replace editor-only actions with working host-neutral alternatives where feasible.

For every affected agent/skill, automatically report required tool capabilities and whether each is `available`, `requires-configuration`, `emulated`, or `unavailable`. A generated workflow requiring absent external services must fail gracefully with an actionable setup message, not hallucinate data or perform an unintended substitute action. Never broaden a read-only tool's privileges.

## 5. Installation, update and removal

Provide a cross-platform `hve-codex` CLI with commands:

```text
hve-codex inventory --ref <ref>
hve-codex convert --ref <ref> --output <dir>
hve-codex validate --output <dir>
hve-codex install --scope project --target <repo> --mode full --dry-run
hve-codex install --scope project --target <repo> --mode full
hve-codex install --scope user --mode full
hve-codex install --global --no-plugin
hve-codex doctor --global
hve-codex update --global --no-plugin
hve-codex uninstall --global --dry-run
hve-codex doctor --target <repo>
hve-codex update --target <repo>
hve-codex uninstall --target <repo>
hve-codex rules match <paths...>
hve-codex rules explain <path>
```

The default `full` mode installs all agents, all scoped instructions, hooks/rule resolver (subject to trust), and either a plugin registration or standalone skills; it must avoid installing duplicate discoverable copies of the same skill. Offer a no-plugin fallback for environments lacking plugin support. Provide a `--no-hooks` mode with a clear degradation warning. Never write directly into an unrelated repository without a selected target and consent.

Expose `--global` as a mutually exclusive alias for `--scope user` on install, update, doctor, and uninstall. Global installation shares skills and resources across repositories while retaining repository-local workflow outputs. Hook path matching uses the active repository root; a project registry takes precedence over the global registry without merging. Document duplicate skill discovery when users retain both scopes, preservation-safe project uninstall, and separate Windows/WSL user homes. Check the installed standalone hook across independent repositories offline; this does not certify live Codex discovery or hook trust.

The installer shall use an ownership manifest (source version, hashes, managed paths, custom files touched, and installed components), detect local modifications, show diffs, back up files it must change, merge owned sections of existing `AGENTS.md`/`config.toml`/`hooks.json`, and be safe to rerun. `update` performs a three-way merge or raises a conflict rather than destroying user customizations. `uninstall` removes only owned files/sections, preserving user modifications and other plugins. Protect symlinks, scope escapes, and Windows paths. Include Linux, macOS and Windows installation tests.

End-user setup uses published release assets, not source generation. Publish a deterministic distribution ZIP and SHA-256 alongside the migrator wheel and its SHA-256. Provide a dependency-free launcher runnable through uv with `--release stable`, `--release edge`, or an exact tag; stable is the default. It verifies assets, rejects unsafe extraction, installs the tool into a persistent uv environment so hooks retain a valid interpreter, and invokes the managed installer globally or for an explicit project. Never trust Codex hooks automatically. Keep source-build instructions in developer documentation; README leads with release installation. Public commands require a published release containing these assets.

Create a repository marketplace at `.agents/plugins/marketplace.json` with a valid `source.path` pointing to `./plugins/hve-core-codex` relative to the repository root. Document `codex plugin marketplace add OWNER/hve-core-codex` as marketplace registration, and verify plugin activation with the locally installed Codex client. Do not assume marketplace registration alone installs or enables the plugin; test the actual installation UX. Use plugin onboarding metadata to point users to the overlay installer/doctor where supported.

## 6. Automated upstream sync and release lifecycle

The automatic sync/publication workflow runs once weekly. In each run, consider only the latest published stable release and the latest pinned upstream `main` commit. Publish a channel only if its source or generated output has changed and its configured review/release gates pass. Do not publish every intervening release or run an automatic prerelease publication schedule. Keep separate PR validation and explicit diagnostic/manual operations. The converter can represent three channels:

| Channel | Upstream source | Publication |
| --- | --- | --- |
| `stable` | Latest non-draft, non-prerelease published HVE release tag | Automatically migration-validated stable channel |
| `prerelease` | Explicit upstream prerelease tags | Explicit opt-in; excluded from the weekly publication pair |
| `edge` | Pinned upstream `main` SHA | Development snapshot; never masquerades as stable |

The discovery job must list upstream releases/tags with pagination, compare against a persisted processed-source ledger, and record exact upstream SHA, upstream manifest version, converter SHA/version, channel, generation timestamp, and output hashes. Handle GitHub API failures, rate limiting, reissued/moved tags, duplicate schedules, simultaneous runs, deleted/renamed artifacts and multiple skipped releases. Protect against concurrent publish races with `concurrency` and source-SHA idempotence.

Pipeline:

1. Discover a new pinned upstream source and compare against last processed SHA.
2. Fetch source as read-only/untrusted input; run inventory and structural diff.
3. Execute deterministic conversion in a clean isolated environment.
4. Run schema, reference, policy, installation, and representative Codex runtime tests.
5. Generate human- and machine-readable compatibility/release reports and checksums.
6. For weekly stable and edge updates, publish directly from a validated migrator commit on protected `main`, without metadata PRs, routine human review, or environment approvals. Pin upstream source commits, keep generated content outside Git, transfer the exact build artifact ID, and bind all asset names and bytes to a digest from the read-only build job.
7. Run deterministic tests, lint, locked dependency audits, structural/inventory/reference checks, installer lifecycle smoke tests and archive checksums. Unknown behavior-bearing fields, missing references, licensing failures and other static blockers stop publication and notify the owner through an assigned failure issue. Run no paid models in publication.
8. Publish only the latest changed stable and edge source once weekly. Stable denotes the upstream release channel and uses explicit migration-only validation; it does not claim fresh CLI/Desktop or model acceptance. Preserve compatibility warnings and historical failures. Stage release assets in a draft, verify server asset checksums and inventory, then make the release visible. Never rewrite an existing tag. The optional runtime-evidence workflow retains its stricter evidence requirements separately.
9. Failure leaves the last good release intact and creates a clear issue/PR annotation with the offending upstream artifacts. Re-run is safe.

Use least-privilege Actions permissions, pinned actions/dependencies and protected branches. **Do not execute untrusted upstream scripts or arbitrary Python/PowerShell during conversion CI**; only run permitted runtime checks in a restricted sandbox without write secrets or tokens. A GitHub `GITHUB_TOKEN`-created push does not automatically trigger all subsequent workflows, so design the publication workflow/credential/event chain explicitly and test it. Do not force-push upstream tags or rewrite published history.

## 7. Determinism, traceability, compatibility reporting

For identical upstream SHA, converter SHA, adapter mappings and build settings, output bytes must be identical. Do not put nondeterministic timestamps in generated skill/agent content. Place build timestamps only in release metadata where necessary.

Required `compatibility-report.json` data fields:

```json
{
  "upstream_ref": "<tag-or-branch>",
  "upstream_sha": "<40-char-SHA>",
  "adapter_revision": "<commit>",
  "counts": {
    "skills": { "discovered": 0, "represented": 0, "validated": 0 },
    "agents": { "discovered": 0, "represented": 0, "validated": 0 },
    "instructions": { "discovered": 0, "represented": 0, "validated": 0 },
    "prompts": { "discovered": 0, "represented": 0, "validated": 0 }
  },
  "artifacts": [
    {
      "source": "<path>",
      "kind": "skill|agent|instruction|prompt|other",
      "outputs": ["<path>"],
      "status": "converted|adapted|host-specific-alternative|requires-external-service|unresolved|excluded-with-rationale",
      "required_capabilities": [],
      "limitations": [],
      "test_ids": []
    }
  ],
  "unknown_fields": [],
  "unresolved_references": [],
  "release_blockers": []
}
```

Also emit a Markdown report with summary tables, missing services, transformed handoffs, rule-coverage findings, instructions on resolving environment dependencies, and behavior-vs-syntax test status. Include the source-to-output mapping in the external distribution and release archive, so reviewers can inspect artifact changes without committing migrated content. No release report should claim 100% operational fidelity simply because all source files have output paths.

## 8. Tests and release gates

**Static/unit gates (mandatory, no model needed):** YAML/JSON/TOML/schema parsing; filesystem and manifest inventory reconciliation; unique and consistent names; metadata constraints; links/resources/`#file:` resolution; script and binary existence; exact `applyTo` glob matches; per-artifact tool mappings; generated manifest validity; license/attribution preservation; cross-platform path safety; deterministic double-conversion output; missing/renamed/deleted upstream artifacts; idempotent installer/update/uninstall and conflict behavior; no secret material; no unsupported Copilot tool tokens in *operative* converted instructions unless clearly documented as examples.

**Host integration gates (mandatory on supported targets):** Install plugin in a clean temporary Codex environment; verify discovery of every named skill through registry/catalog and representative direct invocations; verify project/user custom agents load with exact `name`, `description` and `developer_instructions`; verify skill → subagent dispatch and responses; verify all scoped instruction files can be retrieved by path match and that trusted hooks inject the correct contexts (with duplicate suppression and bounds); verify no-hooks fallback; verify safe installation alongside existing user configurations. Record Codex CLI/app version tested. Never treat a mocked Codex client as sufficient integration evidence.

**Behavioral golden-path scenarios (mandatory):**

1. RPI manual lifecycle and full-auto lifecycle, including interrupt/resume, persisted canonical artifacts, review gates and explicit stop-before-implementation.
2. Code review orchestration with several distinct delegated perspectives, proper read-only boundaries and aggregate report.
3. Accessibility, security, Responsible AI and privacy workflows with specialized references and artifact outputs.
4. PRD/BRD creation and independent quality-review delegation.
5. Backlog planning without writes, followed by *authorized* platform-specific mutation via mocked or approved test MCP servers; verify no mutations when authorization is absent.
6. Data-science and engineering workflow selection, required files, outputs and resumability.
7. Multiple language-specific `applyTo` rules: positive, negative, nested paths, mixed-language tasks and inactive rules.
8. User-facing agent handoffs with continuation choices, argument preservation and unavailable host UI fallback.
9. External dependency not installed: clear actionable error with no fake output or privilege escalation.
10. Upstream change simulation: new artifact, renamed artifact, new frontmatter field, changed tool and removed reference trigger correct release gate and report.

Use small safe fixture repositories, no production credentials, and bounded Codex execution budgets. Keep model-based evals separate from deterministic static gates, with scoring rubrics for outcomes and preservation of explicit human approvals. Verify manual human-review checkboxes are never auto-checked. Stable releases cannot ship with unresolved critical mapping, missing runtime dependencies declared as available, broken workflow continuation, or silently dropped governance controls. Legitimate optional connectors may remain `requires-external-service`, but they must be accurately described and tested for graceful failure.

### 8.1 Preserve Vally and translate its runtime boundary

Use Microsoft's Vally engine, original upstream definitions, fixtures, trial counts, graders, verdict schemas, reminders, and scoring. Deterministically prepare those definitions against the migrated distribution. Custom Codex checks cover migration and host-specific behavior; do not duplicate the upstream behavioral suite in a separate harness or require manual re-porting of every new upstream test.

Vally preparation follows the generated artifact dependency graph, including bounded exact manifest members for directory references. Runtime environments receive generated native agent profiles and package resource mirrors; grading environments keep their authored inputs. Relocate package anchors without changing role instructions, model inheritance, or sandbox settings. Preserve consumer project files, reject unsafe destination paths and conflicting normalized mappings, archive source specs unchanged, and retain source/output hashes.

Copied eval files that serve as reference material are not additional test cases. Use the exact converted-spec declarations in the generated source map to select inputs for Vally's existing schema validator and planner. Missing or malformed declared tests remain failures. Keep original task prompts and grading criteria unchanged.

Prepare genuine fixture prerequisites through Vally's standard `agent_environment.commands` before model execution. Branch-review fixtures may create local synthetic history only inside an owned disposable workspace, with signing and hooks disabled and no remote. Coding fixtures may create a private Python test environment from an existing pinned local runtime without network installation or overwriting consumer environments. Additional setup support must not replace or alter original fixture inputs. Keep source-preservation checks explicit. Authorized custom deadline increases change execution budgets, not upstream prompts, graders or scoring; retain previous failed and timed-out results.

The Codex backend executes tasks through the official CLI/app-server and translates recorded native events into Vally's format. `--codex-binary` selects an installed official executable, not a fork of Codex or Vally. `--judge-backend codex` supplies Vally's original LLM graders with the original messages, schemas, and verdict tools. Bounded read-only `view` and `grep` handlers deliver selected evidence. Actual verdict-tool invocation is required; final-response JSON is not a substitute.

Exact native judge tool isolation remains an accepted, unverified limitation: additional native tools may be available. Record that warning in runtime provenance, rather than claiming an exact tool allowlist. Other unsupported features fail preflight and remain explicit limitations. Do not silently skip their tests, alter original scoring, or patch Codex to simulate support.

### 8.2 Evidence and cost boundaries

Migration validation runs without live models. Host acceptance, Vally behavioral evaluation, environment readiness, and human Codex desktop checks are separate evidence. VS Code-specific checks are excluded from acceptance. Passing conversion does not prove every agent succeeds or authorize publication. Timeouts and failed grading remain failed or unverified outcomes.

Run paid evaluations only within user authorization. The current evaluation choice is Luna Max for tasks and judges; migrated roles normally inherit their parent model. Use native delegation where appropriate without an artificial two-agent cap. Preserve measured usage and incomplete traces; token counts are not credit charges. The child read-only permission check and human Codex desktop checks remain deferred until separately exercised. VS Code-specific validation is out of scope. Local synthetic Git commits need an explicit exception to the workspace's no-commit rule.

For the current implementation closeout, the user accepts the existing major RPI evidence, owns manual Codex desktop validation, and requests no further paid evaluations. Stop active paid work and retain partial evidence without grading it as a pass. The full 2,676-trial behavioral suite is deferred, not executed or certified. The owner subsequently authorized automatic migration-only weekly publication. Preserve the stricter runtime-evidence path as optional; automatic publication does not certify broader host or behavioral parity.

Add deterministic Codex host guidance where packaged Python utilities need to distinguish their installed location from the consumer workspace. Explain complete-package entry points, canonical input paths, caller output boundaries and avoidance of bytecode writes without modifying utility source or weakening its safety gates. Verify executable resource bytes remain identical and malformed traversal paths remain rejected.

## 9. Licensing, security, and provenance

Preserve upstream MIT license, authorship and source notices. Some upstream skills include OWASP-derived **CC BY-SA 4.0** content; copy attribution and `THIRD-PARTY-NOTICES` as required, and avoid incorrectly relabeling the whole converted tree MIT. Keep Microsoft/HVE naming descriptive and do not suggest Microsoft endorsement of the port. Implement dependency locking, vulnerability scanning and checksums. Never log secrets, install arbitrary runtime dependencies silently, or bypass Codex sandbox/hook trust/user confirmation. Pin external content and reproducibility inputs.

## 10. Delivery checklist (definition of done)

- [x] The repository is source-only; upstream and migrated content are external, and the migrator wheel contains no generated HVE payload.
- [x] The shared Bash builder was tested in WSL temporary directories with a fresh pinned upstream download and deterministic file/archive comparisons. All 1,460 saved snapshot files reproduce exactly with the archived converter.

- [ ] Every upstream-listed/discovered relevant skill, agent, instruction and prompt is inventoried and represented, with no silent omission.
- [ ] All supporting referenced files/scripts/assets are present and resolvable.
- [ ] All user-facing agents have usable Codex entry points; delegated agents have valid TOML profiles and tested orchestration.
- [ ] All instructions retain path-scoped selection through a tested rule engine and documented host-specific fallback.
- [ ] All tool/connector differences have explicit adapters, setup contracts or release blockers.
- [ ] The portable root-manifest plugin installs and skills load in tested Codex clients.
- [ ] The managed installer handles clean install, update, dry-run, conflict recovery and uninstall in project and user scopes.
- [ ] Weekly CI considers only changed latest stable and latest edge inputs, creates reviewable diffs, handles gates, and publishes safely under authorized repository policy.
- [ ] Automated tests and representative Codex end-to-end workflows pass; runtime limitations are surfaced rather than hidden.
- [ ] Licensing, attribution, source commit SHAs, file hashes and conversion mapping are recorded.
- [ ] README is concise ELI5 information: unofficial-port description, Microsoft source link, CLI/Desktop/VS Code setup, full RPI first, then individual stages/skills. Detailed compatibility, maintenance, and testing information lives in separate docs; `ELI5.md` explains the translation and host differences.

At completion, provide: repository tree, working commands, generated inventory/compatibility reports, CI workflows, test commands and results, installation walkthrough, release procedure, and a list of any **specific** features not yet behaviorally validated. If any checklist item remains incomplete, say so explicitly; do not mark the full migration complete.

## 11. Authoritative technical references (verify again during implementation)

- HVE source and primary README: https://github.com/microsoft/hve-core
- HVE upstream manifest: https://github.com/microsoft/hve-core/blob/main/plugin.json
- HVE agent architecture: https://github.com/microsoft/hve-core/blob/main/.github/CUSTOM-AGENTS.md
- Codex/ChatGPT plugin packaging: https://developers.openai.com/plugins/build/plugins
- Codex skills: https://learn.chatgpt.com/docs/build-skills
- Codex custom subagents: https://learn.chatgpt.com/docs/agent-configuration/subagents
- Codex AGENTS.md: https://learn.chatgpt.com/docs/agent-configuration/agents-md
- Codex lifecycle hooks: https://learn.chatgpt.com/docs/hooks

The current official documentation is the authority on schema, feature availability, hook trust, installation paths, and CLI commands; do not rely on assumptions from earlier Codex or Copilot versions.

### Evaluation fixture compatibility follow-up

Vally compatibility must reproduce the source test's intended repository context using generated resources, deliver declared Copilot instruction fixtures through explicit native Codex developer guidance, and retain process isolation from personal configuration. Actual event receipt timestamps must survive normalization for original wall-time graders. Scope exclusions and unverified native event/command predicates must be explicit before paid execution. Original prompts, rubric logic, thresholds, trial counts and historical results remain unchanged; fixes are validated by offline regressions and authorized targeted retries rather than automatically repeating the full suite.

## Release acceptance and credentials (2026-10-14)

Publication uses the built-in GitHub token; custom App credentials and GPG signing are optional rather than prerequisites. Preserve immutable tags, source and output hashes, structural validation, reviewed source locks, and candidate-specific CLI/Desktop approval. The stable workflow may explicitly select the committed historical behavioral acceptance policy, with a hash bound into its runtime envelope. This preserves the original 58.6% graded baseline and all failures/blocked counts; it is not a fresh-candidate full-suite pass. The child read-only environment check remains explicitly deferred; Desktop approval remains manual and missing evidence remains blocking. The default no-policy release verifier retains the original full-suite requirement.
