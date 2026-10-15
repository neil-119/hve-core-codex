# Offline triage of the Luna medium Vally run

## Conclusion

The 58.6% pass rate is an uncorrected harness result, not a reliable estimate of migration correctness. Saved evidence confirms defects in evaluation context delivery and event reporting, brittle upstream grading, out-of-scope test inclusion, and actual workflow deviations. There is no matching Microsoft/Copilot baseline, so the remainder cannot be attributed to upstream, Luna or the migrator with confidence.

No live models, retries, fixes, grader changes or historical result edits were performed during this triage.

## Scope and evidence

Run: GPT-6 Luna medium, official WSL Codex, upstream SHA `49dfb73866f8c5f81bf734d6ca808dac25a2a330`. Original totals: 1,310 passed, 927 grading failures, 439 blocked, zero runner-level execution errors. Raw evidence is `/home/neilrao/.local/share/hve-core-codex/luna-medium-full-20261014-live`.

All 2,237 executed trial records were scanned for grader types and context indicators. Representative prompts, responses, grader implementations, tool calls and source adapter logic were manually inspected. Counts below overlap and are not an additive classification of 927 failures. Automated text indicators are candidates, not causal proof.

## Confirmed findings

### 1. Evaluation context is missing or delivered in a Copilot-specific location

In `agent-behavior`, 455 executed trials stage a file at `.github/copilot-instructions.md`: 188 fail and 267 pass. Of the 188 failures, 68 make no tool calls, so they do not explicitly read that file. The converter preserves this authored destination; it does not make its contents native Codex project guidance. The executor launches with `--ignore-user-config`, and skill isolation sets `project_doc_max_bytes=0`. These are deliberate isolation controls, but the fixture's implicit Copilot instruction delivery has no equivalent in these executions. Passing trials do not prove the intended instruction was loaded: general knowledge can satisfy some checks.

Example: `0000-live`, `accessibility-framework-assessor-absent-runtime-evidence`, stages the assessor at `.github/copilot-instructions.md`. The task prohibits tools, and the recorded trajectory has no tool calls. The judge says the conservative rationale is present but the exact required status is absent. This is a demonstrably mismatched delivery path, not proof the packaged assessor itself is broken.

Other suites assume repository context without declaring it. `behavior-conformance-instructions` has no root environment; the sampled ADR instruction case has no stimulus environment. In `0396-live`, actual shell calls search for ADR/BYO/AGENTS files and the agent reports an empty workspace. `0250-live` attempts to read the launched accessibility agent, searches again, then answers generically; the conformance spec declares no root environment. This fixture does not reproduce running inside the HVE repository.

Across the run, 214 failed responses contain missing-context indicators; 96 are instruction-conformance trials. These heuristic counts are not all confirmed missing-context causes. The count of 278 failed trials with no stimulus-level file declarations also does not establish missing context by itself: root environments or selected skills may supply it.

### 2. Missing event timestamps invalidate wall-time grading

All 2,237 saved trajectories lack timestamp fields on their normalized events. All 26 failing wall-time grader applications explicitly report: `Wall time is unavailable — trajectory events have no timestamps`. Twelve failed trials have no other failing component grader.

The CLI adapter records start/completion metadata but emits normalized events without timestamps. This is an adapter evidence defect. Zero runner-level execution errors therefore does not mean every grader had valid inputs: wall-time graders return an internal error inside a failed grade.

Do not retroactively relabel these trials as passes. Original scoring and complete, correctly timestamped evidence are needed for acceptance.

### 3. Regex failures include reasonable answers rejected on wording

836 of the 927 failed trials have only `output-matches` component failures. This does NOT imply 836 false negatives: many responses genuinely omit requested behavior.

A confirmed narrow example is `0693-live`, `skill-python-foundational-bleed-detection`. All three trials answer that Python guidance does not apply to a TypeScript module, using sentences such as: `No. python-foundational is for Python work, and this module is TypeScript.` The grader only accepts phrases matching `not apply`, `does not`, `inapplicable`, `different skill`, or `N/A`, and rejects all three answers. This is a wording false negative against the apparent attribution intent; original grades remain unchanged.

Failed component applications total 1,405 output-matches, 36 prompt, 26 wall-time, 22 program, 9 file-matches, 9 run-command, 4 output-contains, 4 file-exists, 3 skill-invocation, 3 transcript-matches, 1 diff-empty and 1 output-not-matches. A trial can fail several components.

### 4. Command-shape assumptions can misclassify evidence

`0112-live`, `dt-coach-mural-scope-upgrade-stops-seeding`, uses an upstream program grader that permits simple `cat <path>` reads but flags other shell commands containing `mural`. Codex records shell commands as `/bin/bash -lc ...`; a wrapped read can miss the grader's anchored simple-read exception. The sampled outputs correctly stop for missing scope. This is a concrete compatibility risk in the grader implementation; each flagged call must be traced before concluding it was only a harmless read.

`0181-live` also reports missing `python`, then a read-only filesystem error when trying `python3` for Mural readiness. This is task-runtime evidence inside a completed conversation, not a model transport error. No external Mural mutation is established by the sample.

### 5. Scope exclusion was not implemented

The full bundle executed 33 `copilot-otel-metrics` trials: nine passed and 24 failed. The inspected cases explicitly manipulate `github.copilot.chat.otel.*` and discuss VS Code profiles/reloads. They are outside the user's Codex-only test scope. The full-suite report's statement that VS Code-specific tests were excluded was incorrect. Preserve the original counts, but do not use these cases to judge Codex migration acceptance. This count identifies this named family, not an exhaustive classification of every possible VS Code-specific case.

### 6. Some custom workflow failures are meaningful

`0778-live` RPI produced the core full-auto implementation and review artifact, but the judge found generated tracked Python bytecode outside the two permitted source paths. That is an observed scope deviation, not just a missing-context answer.

`0779-live` branch review paused at orientation and kept source read-only, but could not persist branch-scoped artifacts because the fixture had no usable Git history/branch. The earlier successful review selection used a prepared synthetic Git fixture; this full run used the preserved original bundle. This result reflects a fixture precondition failure and does not establish a general failure of the review workflow.

`0784-live` design-thinking handoff failed `diff-empty`: the saved workspace patch is nonempty. It needs file-by-file attribution before calling that mutation harmless or required.

## What this means

There are confirmed problems in the Codex evaluation integration. These results cannot be dismissed as ordinary upstream failures, nor do they prove hundreds of migrated skills or agents are broken. The strongest demonstrated common causes are context delivery and event evidence. Successful isolated skill discovery in metadata proves discovery configuration, not that the model read or followed the relevant instructions.

The original totals remain the historical record. Do not publish an adjusted pass rate by subtracting overlapping candidate categories.

## Recommended follow-up, not implemented

1. Define a faithful fixture-context contract for upstream tests: deliver intended instructions natively, stage referenced repository context, and retain deliberate isolation from personal configuration. Preserve original prompts/rubrics and provenance.
2. Capture real event timestamps at receipt and preserve them through normalization; distinguish grader-input errors from behavioral failures in summaries.
3. Apply an explicit reviewed scope filter before planning, including the Copilot/VS Code telemetry family.
4. Audit wrapped shell reads and native event predicates against unchanged graders; surface unsupported contracts instead of silently treating them as behavioral failures.
5. Validate the above offline, then obtain authorization for a small targeted comparison. No further paid runs are authorized by this triage request.

## Audit artifacts

External directory: `C:/repos/hve-core-codex-artifacts/luna-medium-20261014/`.

- `final-reconciliation.json`: independently reconciled original counts.
- `offline-triage-inventory.json`: component failures and representative raw evidence.
- `offline-triage-patterns.json`: overlapping exposure indicators and timestamp-only failure list.
- `offline-triage-secondary.json`: program, transcript, file and mutation grader samples.

The scripts in that directory perform read-only analysis of the preserved run. Migrated content and raw trajectories remain outside the migrator repository.
