# Confirming the Codex port works

Current work focuses on migration correctness: deterministic conversion, content preservation, reference translation, routing and hook adapter contracts, and valid generated artifacts. See [the active validation scope](validation.md#active-scope-migration-correctness). The live client procedures below are retained for a separate runtime and environment workstream; they are not prerequisites for passing migration tests. No sandbox setup or paid model run is authorized by this document.

Conversion, native discovery, and successful workflows are separate claims. A passing converter test does not prove that Codex selected a skill, delivered hook context, or completed a task. Vally owns upstream behavioral evaluation; custom Codex tests verify host discovery, hooks, and native roles.

## Current edge checkpoint

The pinned edge fixture has native positive Python guidance, a documentation negative control, successful resumed guidance, and a real named Luna Max child on the tested bundled CLI. The child read-only enforcement check is deferred by the user and remains unresolved; do not repeat it while the user is working remotely. The standalone CLI's earlier Luna rejection was resolved on 0.162.1, although native sandbox startup conflicts with Desktop holding its runtime open. These current results supersede those particular historical blockers without changing earlier reports. Human rubrics, other workflow evidence gaps, external-service prerequisites, and separate Desktop/VS Code acceptance remain open. See [validation notes](validation.md).

## Model-free checks first

Audit every source artifact, immutable original, generated entry point, and explicit-only policy:

```powershell
uv run hve-codex host-test coverage --distribution . --scenarios tests/codex-host/stable.json --output .cache/acceptance-coverage.json
uv run hve-codex host-test prepare --distribution . --scenarios tests/codex-host/stable.json --output .cache/integration-prepared
uv run hve-codex host-probe --probe-kind discovery --distribution . --codex-command codex --output .cache/native-discovery.json
```

The coverage report lists every artifact and its declared scenarios. An artifact without a scenario remains visibly uncovered. The discovery probe asks an isolated Codex app-server for skills, hooks, and effective configuration. It sends only initialization and discovery requests, creates no thread, starts no turn, copies no account credentials, and reports unobservable state explicitly. Skills are matched by their exact installed names and paths. Listing hooks proves discovery, not delivery; agent files on disk do not prove that a child started with those instructions.

For edge, use its generated distribution and `tests/codex-host/edge.json`. Upstream main changed the RPI roles, skills, and artifact contracts; stable names and paths must not be silently reused. Prepare original behavioral suites with `eval vally-prepare`; see the [Vally workflow](evaluations.md).

## Additional integration cases

| Case | Evidence required |
|---|---|
| `integration-01` | An untagged research task selects and reads relevant installed guidance, then produces a research artifact without editing the source. |
| `integration-02` | An unrelated local task leaves explicit-only workflows inactive and makes no external writes. |
| `integration-03` | Trusted native hooks deliver general and file-scoped context on the appropriate events, avoid unrelated targets, and continue working after resume. |
| `integration-04` | A real named custom role starts, receives its developer instructions and hook context, and returns an awaited result. |

Skill selection is task-based; file-specific instruction selection comes from the scoped rule integration. Positive and negative cases are needed for both. A final answer claiming success or echoing a fixture marker is supporting evidence only. Inspect native events, actual skill reads, child identities, hook output, resulting files, and task validation. If the client does not expose necessary evidence, mark the aspect unverified rather than manufacturing a pass.

Check approval handling separately from task progress. An explicit approval to enter Plan should be recorded as a dated, user-attributed decision with that scope; it does not complete the later human review of the Plan or authorize Implementation. Human-owned review and signature boxes stay unchecked unless the user specifically requests a truthful update to that box. Agent-owned progress and artifact self-checks may be marked from actual evidence. A timeout before a required critique finishes leaves the phase incomplete, even when a plan file exists.

From an installed project's root, `python .codex/hooks/hve_rules.py --rules-match src/app.py` retrieves matching guidance without a globally installed porter CLI. This read-only fallback does not simulate native events or establish hook delivery. For delegated roles, a task name or nickname is insufficient: select the actual custom role through the host's supported role argument and verify its loaded instructions. A generic child reading the procedure remains a manual fallback.

## Prepare a project for client validation

Unlike temporary preparation, persistent staging lets a human inspect the project and review hook trust before any live test:

On Windows, stage persistent fixtures from a normal user terminal. A restricted agent process can create a project owned by the sandbox account, causing native workspace ACL refresh failures. Check ownership when diagnosing startup errors; do not repair permissions manually as part of an eval. Moving to a fresh installation path requires reviewing its exact hook commands again.

```powershell
uv run hve-codex host-test stage --distribution . --scenarios tests/codex-host/stable.json --scenario-id integration-03 --output .cache/client-hook-probe
```

Open the resulting `project` folder in the client being tested. `acceptance-stage.json` contains the ordered prompts, rubric, exact source provenance, and initial fixture hashes. This command starts no models and grants no hook trust. Select Luna and Max reasoning and verify delegated-role settings before a paid run. Review project trust and the exact installed hook definitions with the client's supported controls; the CLI offers `/hooks`. Never use a hook-trust bypass to establish ordinary installation compatibility.

The automatic runner blocks native-hook cases that require this manual review step. Use the persistent fixture for their reviewed CLI, Desktop, or VS Code session instead. Do not record a hook-delivery pass from a standalone hook subprocess test.

After exact-path project trust and normal hook review, `host-test run --persistent-stage <stage-folder>` reuses that fixture without installing, replacing inputs, re-pinning models, or deleting it. The runner validates the receipt's scenario hash, provenance, project path, input hashes, and model settings. It then asks the official CLI whether the project config is loaded and its HVE hooks are trusted and enabled before paid execution. A trusted ancestor alone is insufficient for the independently rooted WSL fixtures. Native event and rubric evidence remain separate from readiness.

## Live acceptance after authorization

CLI runtime evaluation is authorized with Luna Max. Behavior checks now use Vally; native skill, agent, and hook checks use the four channel-specific cases in `tests/codex-host`. Desktop and VS Code validation remains pending human sessions and review. No spending hard cap is configured. The [Luna planning status](luna-eval-plan.md) does not yet provide a reliable full-suite credit estimate. Unsupported Vally judging and simulation remain blocked on standard Codex.

An earlier RPI attempt stopped because effective Windows sessions were read-only and required artifacts could not be persisted. It did not establish native-hook delivery or custom-role loading in that attempt. See [validation.md](validation.md#authorized-pilot-result). The current CLI authorization permits the stable and edge runs; assess their actual permission, hook, and role evidence rather than treating the earlier attempt as a pass or a continuing hold.

Automated evals load normal Codex configuration so the disposable project's agents and hooks can load. Each staged project pins the requested model and reasoning effort and sets `workspace-write` as its sandbox default. Before paid execution, the runner requires an existing exact-path trust entry for the eval fixture in Codex configuration. A trusted parent is insufficient for independently rooted fixtures: official WSL CLI config/read reported their project layers disabled until the exact fixture path was trusted. It changes no trust settings and grants no hook trust. It omits a live parent `--sandbox` override so agent profiles can apply stricter policies. On Windows, execution preserves the existing explicitly configured sandbox mode; the read-only readiness probe starts no thread or turn. A normal Codex sandbox command can refresh Windows permissions or provision sandbox accounts if required; the agent does not invoke setup directly. Such a readiness read remains held until the user authorizes those normal setup effects. This CLI build ignores per-command project-trust flags, so the runner does not supply one.

For the authorized CLI acceptance, record actual parent and child model/usage evidence and review the outcomes for both stable and edge cases. Desktop and VS Code remain pending separate human-run sessions, client versions, traces, changed files, and human review. A CLI result cannot stand in for a Desktop or extension session.

For missing workflow coverage, add Vally custom specs with the original grader types. Reserve host cases for native Codex behavior. External services need configured, authenticated connectors and explicit permission for the operation being tested. Missing access is a prerequisite blocker, not a successful test of that service.

The version 2 release gate requires independently verified full Vally results plus host evidence for CLI, Desktop, and VS Code. The deferred child read-only check remains required. Partial runs and historical golden-path captures do not satisfy this contract. Keep unsupported capabilities and untested workflows explicit.

The Design Thinking fixtures stop before a user chooses continuation and do not establish completion of Methods 1–3. Their checks require downstream handoff artifacts to remain absent while the source gate is pending. A successful handoff needs a separate fixture with completed methods and an explicit choice. The PRD/BRD review check follows the source reviewer's dated review directory. The stable upstream-change fixture includes an actual new frontmatter key for the requested comparison. Earlier runs keep their original definitions and results; corrected checks or inputs must be reviewed separately rather than relabeling those runs.


Paid eval model controls are verified separately from ordinary installation. The runner has explicit model-free compatibility evidence for CLI 0.144.6, the tested desktop-bundled 0.162.0-alpha.17.2, and standalone 0.162.1. Version 0.162.1 accepted and read back both global subagent defaults with strict config parsing and zero model sessions; an authenticated Luna Max compatibility turn also completed. Version 0.144.6 cannot parse the newer global subagent-default fields, so its evaluation projects retain root and named-profile Luna/Max pins but omit those global fields and mark generic-child settings unverified. Unknown CLI versions remain stageable and discoverable; paid runs stop until their model-control compatibility is verified. This is an eval spending guard, not an installer compatibility certification. Named-role loading and effective permissions still require actual native session evidence. Current profile/default precedence is described in the [official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents).
