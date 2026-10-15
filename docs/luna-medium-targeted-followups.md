# Targeted migration follow-up validation

Latest results across the same 22 selected trials: **13 passed, 9 failed, 0 execution errors**. Original same-selection results: **3 passed, 19 failed**. These are targeted results, not a new full-suite pass rate. Original historical grades are unchanged.

All retries used GPT-6 Luna medium on official WSL Codex and source SHA `49dfb73866f8c5f81bf734d6ca808dac25a2a330`. Eight cases ran first (22 trials); two context-dependent cases were rerun after bundler corrections (6 trials); one startup case was rerun after schema staging (1 trial). Total actual live trial executions: **29**. No full-suite rerun.

## Implemented

- Exact declared Copilot instruction fixtures are delivered through native developer guidance, with path and size checks; personal config isolation remains enabled.
- Repository-contract fixtures receive generated HVE guidance, skills and script/schema files. The bundler uses the full artifact manifest and physically stages the added resources. Empty baseline controls remain unchanged.
- Native event receipt timestamps survive normalization; grader-input errors have a separate report field.
- Known Copilot/VS Code telemetry cases are excluded before planning. A selected telemetry control was blocked without model calls.
- Wrapper-sensitive Mural program grading is explicitly blocked as unverified. Its preflight control started no models; original program code was preserved.
- Resource capture now allows a bounded 40,000 files while retaining byte, traversal and symlink checks. The lower limit blocked the enlarged context before paid execution; the new bounded limit passes offline checks.

## Latest case results

| Case | Before passed/failed | Latest passed/failed |
| --- | ---: | ---: |
| `instruction-adr-byo-template-conformance` | 0/3 | 3/0 |
| `instruction-accessibility-identity-conformance` | 0/3 | 3/0 |
| `engagement-report-generator-ado-saved-query` | 0/5 | 0/5 |
| `accessibility-framework-assessor-absent-runtime-evidence` | 3/2 | 5/0 |
| `startup-disclaimer-before-phase1` | 0/1 | 0/1 |
| `evidence-gap-proportionate-flagging` | 0/1 | 1/0 |
| `data-subject-rights-deletion-request` | 0/1 | 1/0 |
| `skill-python-foundational-bleed-detection` | 0/3 | 0/3 |

## Remaining failures and limits

- The three Python-versus-TypeScript attribution controls still fail their unchanged wording regex.
- All five saved-query trials still fail. Three responses now describe the intended query-resolution/execution/hydration procedure, but their negative wording or ordering misses regex patterns. Two responses still fail to find the available repository guidance and ask for files. Do not label all five as harmless grading failures.
- The startup response still fails its unchanged turn-1 disclaimer grader. The final raw trajectory explicitly emits the disclaimer on turn 0, then asks Discovery questions on the graded turn 1. With the schema staged at its original scripts path, the final response saves the patient-portal state file; the missing-schema symptom is resolved.
- No remaining grade was relabeled or relaxed. Native shell-predicate equivalence remains unverified and blocked; this does not make that feature supported. Other full-suite failures, Desktop behavior and broader runtime limitations remain outside this targeted validation.

## Verification and evidence

- Windows Python: **390 passed, 6 skipped, 211 subtests passed**. WSL Node: **210 passed**. Lint passes.
- The broader WSL Python run had seven platform-sensitive failures; it was not repaired or treated as passing. Native Windows verification is recorded separately.
- All 13 upstream scoring/prompt/deadline contracts were compared unchanged; both baseline-control specs remain identical.
- Latest per-case counts were independently reconciled with raw JSONL. Each run records unchanged source integrity and timestamped turn boundaries.

Evidence directory: `C:/repos/hve-core-codex-artifacts/luna-medium-20261014/`. Final receipt: `followups-final-reconciliation.json`. Phase receipts: `followups-targeted-results.json`, `followups-context-results.json`, `followups-startup-results.json`. Contract receipt: `followups-contract-audit.json`. Raw report paths are retained in those receipts.

The generated bundles and raw trajectories remain outside this source-only migrator repository.
