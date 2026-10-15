# Evaluating the migrated HVE content

Use Microsoft's Vally for behavioral tests and the custom Codex host checks for discovery, hooks, and named roles. The adapter targets standard Codex; no custom Codex build is required or distributed.

## Prepare and inspect original Vally tests

Prepare the immutable upstream definitions against a converted distribution:

```powershell
uv run hve-codex eval vally-prepare --upstream-dir <pinned-checkout> --distribution <converted-distribution> --eval-filename eval.yaml --eval-filename eval.yml --eval-filename instructions.eval.yaml --eval-filename skill-behavior.eval.yaml --output <fresh-bundle>
uv run hve-codex eval vally-plan --bundle <fresh-bundle> --output <plan.json>
uv run hve-codex eval vally-run --bundle <fresh-bundle> --output <fresh-run-directory> --judge-backend codex --dry-run
```

Preparation preserves the original specs, fixtures, trial counts, and scoring. It follows recorded artifact dependencies and stages native agent profiles and resource mirrors for the declared runtime environments. Copied eval reference material is excluded from test selection using the generated source map. Optional custom behavior cases use the same Vally engine through `--custom-dir`. Upstream runtime dependencies can be staged offline with `vally-fixture-deps-prepare`; lockfile integrity and copied resources are verified.

The public runner blocks unsupported selections before paid execution. Support for simulation, MCP eval declarations, attachments, and recoverable delegated-agent limits remains unresolved. Select `--judge-backend codex` to run Vally's original LLM graders through the official Codex app-server with Luna Max. Judges receive bounded `view` and `grep` evidence tools and must invoke the original verdict tool. Extra native tools may remain available: exact judge tool isolation is an accepted, unverified limitation. The experimental comparison and judge commands have been removed. See [current limitations](vally-port-status.md) and request a user decision before implementing alternatives.

Selections using supported execution and static graders can use `vally-run` with exact `--eval` and `--stimulus` selectors and the installed CLI through `--codex-binary`. Removing `--dry-run` starts model sessions. Offline readiness is not behavioral acceptance.

## Verify saved standard-host evidence

```powershell
uv run hve-codex eval vally-verify --bundle <bundle> --run <run> --source-sha <upstream-commit> --output <verification.json>
```

Verification checks source/runtime integrity, original scoring, and complete selected-trial coverage. Partial results do not certify the full suite.

## Custom Codex host checks

`host-test` covers skill selection, trusted hook delivery and resume, and named-role context. Definitions live in `tests/codex-host/stable.json` and `edge.json`. Preparation and staging start no models. Hooks require normal client trust; discovery does not prove delivery or successful workflow execution. Codex desktop needs separate human checks. VS Code-specific testing is out of scope. The child read-only check remains deferred.

The retired curated behavior harness is not used. Its original fixture JSON remains only for conversion regression checks.

## Native fixture context and scoped validation

Converted repository-contract stimuli that omit their own environment receive repository context from the pinned generated resource tree, including original skill and script/schema locations for explicit reads. The bundler resolves these additions from the full generated artifact manifest and materializes them before evaluation; empty baseline controls remain isolated. Explicitly declared fixture environments keep their authored scope. The executor reads a declared `.github/copilot-instructions.md` inside the disposable workspace and passes its exact content through process-local Codex `developer_instructions`; personal project-document discovery stays disabled. Delivery is recorded separately from skill discovery, and oversize or escaping instruction paths fail closed. This is fixture compatibility, not a change to upstream prompts or graders.

The resource-integrity capture permits at most 40,000 files, retaining its 32 MiB per-file and 1 GiB total byte limits and symlink checks.

The CLI adapter timestamps observed JSONL records when received and retains those timestamps on normalized events. App-server events are timestamped on notification receipt. Historical evidence is never backfilled with invented timestamps. Runner reports expose component grader-input errors separately from execution errors.

Planning excludes the reviewed `skill-copilot-otel-metrics-*` family, which tests VS Code/Copilot settings rather than Codex. Planner output records exact exclusions; explicitly selecting only excluded cases fails before model execution. Other platform-specific cases still require review. Program graders with known wrapper-sensitive `simpleRead`/Mural contracts remain unchanged and are preflight-blocked as unverified, rather than normalized through an unsafe shell rewrite.

Use `--reasoning-effort medium` on `eval vally-run` to explicitly select medium for both tasks and judges. Targeted fixes should be validated with exact `--eval` and `--stimulus` selections against a newly generated bundle; preserve earlier reports and unchanged wording-sensitive controls.

Generated integration instructions include generic guidance for finding HVE files in hidden repository directories. For an explicitly labeled Vally comparison, pass --navigation-context to deliver that same guidance to the task model. It is off by default, does not reach the judge, and does not change upstream prompts or graders. The canonical text lives in vally-executor/navigation.json and is included in build fingerprints and runtime provenance.

## Shared response style

Every Codex task execution now receives the versioned response style in `vally-executor/navigation.json` through process-local developer instructions, including subsequent turns and app-server executions. It asks for explicit conclusions, literal prohibitions, scope decisions, ordered steps, exact identifiers and evidence boundaries. Task-specific formats take precedence over these stylistic defaults. Microsoft prompts, trial counts and graders remain unchanged; the separate judge client receives no style overlay.

This is an evaluation adaptation, not a claim to reproduce Haiku's behavior. Trajectories record `responseStyleVersion`, and runtime provenance binds the full context file bytes. Earlier results are pre-overlay baselines and must not be combined silently with new results. The optional navigation switch remains separate. Subsequent focused paid validation is recorded separately in the response-style v1 and v2 reports.

Response-style version 2 adds generic direct/indirect wording examples using unrelated tasks. The targeted saved-query comparison is documented in `response-style-v2-focused-validation.md`; it remains a failing case (0/5), despite all trials passing the prohibition wording component. These tuning runs are development comparisons, not independent benchmark evidence.
