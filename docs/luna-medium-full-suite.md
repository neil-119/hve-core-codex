# Full Vally suite: GPT-6 Luna medium

Started: 2026-10-14T14:12:05.185937+00:00. Status: finished.

Official WSL Codex; model `gpt-6-luna`; explicit `medium` effort for tasks and judges; four concurrent stimulus selections. Original test prompts, trial counts, graders and deadlines are unchanged. No failed cases are repaired or retried.

Source: Microsoft HVE Core `49dfb73866f8c5f81bf734d6ca808dac25a2a330`. Full preserved bundle: 23 specs, 2,676 trials (includes custom Codex cases). VS Code-specific tests are excluded. Unsupported cases are blocked, not passed.

Passed: **1310**. Grading failures: **927**. Execution errors: **0**. Blocked: **439**. Pending/unrun: **0**.

Raw evidence: `/home/neilrao/.local/share/hve-core-codex/luna-medium-full-20261014-live`. Queue state: `/mnt/c/repos/hve-core-codex-artifacts/luna-medium-20261014/queue-state.json`.

Invocation per selection:

```text
/home/neilrao/.local/share/hve-core-codex/node-runtime/node-v24.21.0-linux-x64/bin/node /home/neilrao/.local/share/hve-core-codex/vally-executor-luna-medium-20261014/run.mjs --bundle /home/neilrao/.local/share/hve-core-codex/vally-edge-dependencies-final-20261013 --output <fresh selection directory> --eval <name> --stimulus <name> --judge-backend codex --reasoning-effort medium --codex-binary /home/neilrao/.local/bin/codex
```

## Acceptance and interpretation

The retained baseline is **58.6% of graded trials** (1,310/2,237). Targeted follow-ups do not alter it. We accept the observed framework behavior while retaining blocked and unverified limitations. Specific wording and turn-scoping mismatches are documented in [Luna evaluation interpretation](luna-eval-interpretation.md); they do not explain away every failure. Further wording tuning and paid retries are stopped.

## Failed tests and execution errors

| Eval | Stimulus | Trial | Outcome | Details |
| --- | --- | --- | --- | --- |
| script-validation | identify-missing-skill-md | 0 | failed | 1/2 graders passed |
| script-validation | identify-missing-skill-md | 2 | failed | 1/2 graders passed |
| agent-behavior | accessibility-framework-assessor-absent-runtime-evidence | 0 | failed | 0/1 graders passed |
| agent-behavior | accessibility-framework-assessor-absent-runtime-evidence | 1 | failed | 0/1 graders passed |
| agent-behavior | accessibility-framework-assessor-disclaimer-posture | 0 | failed | 0/2 graders passed |
| agent-behavior | accessibility-framework-assessor-disclaimer-posture | 1 | failed | 1/2 graders passed |
| agent-behavior | accessibility-framework-assessor-disclaimer-posture | 2 | failed | 0/2 graders passed |
| agent-behavior | accessibility-framework-assessor-disclaimer-posture | 3 | failed | 1/2 graders passed |
| agent-behavior | accessibility-framework-assessor-disclaimer-posture | 4 | failed | 1/2 graders passed |
| agent-behavior | accessibility-framework-assessor-malformed-runtime-evidence | 1 | failed | 0/1 graders passed |
| agent-behavior | accessibility-framework-assessor-malformed-runtime-evidence | 3 | failed | 0/1 graders passed |
| agent-behavior | accessibility-framework-assessor-method-adequacy | 0 | failed | 2/3 graders passed |
| agent-behavior | accessibility-planner-handoff-write-failure | 4 | failed | 0/1 graders passed |
| agent-behavior | accessibility-reviewer-required-probe-failure | 1 | failed | 0/1 graders passed |
| agent-behavior | ado-backlog-executor-frontmatter-name-and-description | 0 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-frontmatter-name-and-description | 1 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-frontmatter-name-and-description | 2 | failed | 2/3 graders passed |
| agent-behavior | ado-backlog-executor-frontmatter-name-and-description | 3 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-frontmatter-name-and-description | 4 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-structural-template | 2 | failed | 4/5 graders passed |
| agent-behavior | ado-backlog-executor-structural-template | 4 | failed | 4/5 graders passed |
| agent-behavior | ado-backlog-executor-tool-restriction-fit | 0 | failed | 1/2 graders passed |
| agent-behavior | ado-backlog-executor-tool-restriction-fit | 1 | failed | 1/2 graders passed |
| agent-behavior | ado-backlog-executor-tool-restriction-fit | 2 | failed | 1/2 graders passed |
| agent-behavior | ado-backlog-executor-tool-restriction-fit | 3 | failed | 1/2 graders passed |
| agent-behavior | ado-backlog-executor-user-invocable-flag | 0 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-user-invocable-flag | 1 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-user-invocable-flag | 2 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-user-invocable-flag | 3 | failed | 1/3 graders passed |
| agent-behavior | ado-backlog-executor-user-invocable-flag | 4 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-frontmatter-name-and-description | 0 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-frontmatter-name-and-description | 1 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-frontmatter-name-and-description | 2 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-frontmatter-name-and-description | 3 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-frontmatter-name-and-description | 4 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-handoff-targets-and-labels | 0 | failed | 1/3 graders passed |
| agent-behavior | backlog-manager-handoff-targets-and-labels | 1 | failed | 2/3 graders passed |
| agent-behavior | backlog-manager-handoff-targets-and-labels | 2 | failed | 2/3 graders passed |
| agent-behavior | backlog-manager-handoff-targets-and-labels | 3 | failed | 2/3 graders passed |
| agent-behavior | backlog-manager-handoff-targets-and-labels | 4 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-class-recipe | 0 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-class-recipe | 1 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-class-recipe | 2 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-class-recipe | 3 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-class-recipe | 4 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-records-blocked-research-segment | 0 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-research-blocked | 0 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-research-blocked | 2 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-research-blocked | 3 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-research-blocked | 4 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-research-receipt | 0 | failed | 2/3 graders passed |
| agent-behavior | brd-builder-research-receipt | 1 | failed | 2/3 graders passed |
| agent-behavior | code-review-branch-diff-standard-profile | 0 | failed | 1/2 graders passed |
| agent-behavior | code-review-branch-diff-standard-profile | 1 | failed | 1/2 graders passed |
| agent-behavior | code-review-branch-diff-standard-profile | 2 | failed | 1/2 graders passed |
| agent-behavior | code-review-branch-diff-standard-profile | 3 | failed | 1/2 graders passed |
| agent-behavior | code-review-branch-diff-standard-profile | 4 | failed | 1/2 graders passed |
| agent-behavior | code-review-class-recipe | 0 | failed | 4/5 graders passed |
| agent-behavior | code-review-class-recipe | 1 | failed | 4/5 graders passed |
| agent-behavior | code-review-class-recipe | 2 | failed | 4/5 graders passed |
| agent-behavior | code-review-class-recipe | 3 | failed | 4/5 graders passed |
| agent-behavior | code-review-class-recipe | 4 | failed | 4/5 graders passed |
| agent-behavior | code-review-decision-ready-dispatch-board | 0 | failed | 3/5 graders passed |
| agent-behavior | code-review-decision-ready-dispatch-board | 1 | failed | 2/5 graders passed |
| agent-behavior | code-review-decision-ready-dispatch-board | 2 | failed | 3/5 graders passed |
| agent-behavior | code-review-decision-ready-dispatch-board | 3 | failed | 3/5 graders passed |
| agent-behavior | code-review-decision-ready-dispatch-board | 4 | failed | 4/5 graders passed |
| agent-behavior | code-review-native-emission-sha-gate | 0 | failed | 1/2 graders passed |
| agent-behavior | code-review-native-emission-sha-gate | 1 | failed | 1/2 graders passed |
| agent-behavior | code-review-native-emission-sha-gate | 2 | failed | 1/2 graders passed |
| agent-behavior | code-review-native-emission-sha-gate | 3 | failed | 1/2 graders passed |
| agent-behavior | code-review-native-emission-sha-gate | 4 | failed | 1/2 graders passed |
| agent-behavior | code-review-orientation-retry-contract | 0 | failed | 0/1 graders passed |
| agent-behavior | code-review-orientation-retry-contract | 1 | failed | 0/1 graders passed |
| agent-behavior | code-review-orientation-retry-contract | 3 | failed | 0/1 graders passed |
| agent-behavior | code-review-orientation-retry-contract | 4 | failed | 0/1 graders passed |
| agent-behavior | code-review-perspective-no-output-retry | 0 | failed | 1/2 graders passed |
| agent-behavior | code-review-perspective-no-output-retry | 1 | failed | 1/2 graders passed |
| agent-behavior | code-review-perspective-no-output-retry | 2 | failed | 0/2 graders passed |
| agent-behavior | code-review-perspective-no-output-retry | 3 | failed | 1/2 graders passed |
| agent-behavior | code-review-perspective-no-output-retry | 4 | failed | 1/2 graders passed |
| agent-behavior | code-review-pull-request-standard-profile | 0 | failed | 1/3 graders passed |
| agent-behavior | code-review-pull-request-standard-profile | 1 | failed | 1/3 graders passed |
| agent-behavior | code-review-pull-request-standard-profile | 2 | failed | 1/3 graders passed |
| agent-behavior | code-review-pull-request-standard-profile | 3 | failed | 2/3 graders passed |
| agent-behavior | code-review-pull-request-standard-profile | 4 | failed | 1/3 graders passed |
| agent-behavior | code-review-rejects-unsupported-finding | 0 | failed | 0/2 graders passed |
| agent-behavior | code-review-rejects-unsupported-finding | 1 | failed | 1/2 graders passed |
| agent-behavior | code-review-rejects-unsupported-finding | 2 | failed | 1/2 graders passed |
| agent-behavior | code-review-rejects-unsupported-finding | 3 | failed | 1/2 graders passed |
| agent-behavior | code-review-rejects-unsupported-finding | 4 | failed | 0/2 graders passed |
| agent-behavior | code-review-security-planning-drift-context | 4 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-content-contradiction | 0 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-content-contradiction | 3 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-content-contradiction | 4 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-name-collision | 1 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-name-collision | 2 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-name-collision | 3 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-merge-name-collision | 4 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-unknown-origin-dedup | 1 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-unknown-origin-dedup | 2 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-user-tier-precedence | 1 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-user-tier-precedence | 2 | failed | 2/3 graders passed |
| agent-behavior | code-review-standards-user-tier-precedence | 4 | failed | 2/3 graders passed |
| agent-behavior | code-review-target-checkout-mismatch | 1 | failed | 2/3 graders passed |
| agent-behavior | codebase-profiler-skill-mapping | 1 | failed | 0/2 graders passed |
| agent-behavior | council-arbiter-untrusted-proposal-confinement | 0 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-untrusted-proposal-confinement | 1 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-untrusted-proposal-confinement | 2 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-untrusted-proposal-confinement | 3 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-untrusted-proposal-confinement | 4 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-user-approved-reconciliation | 0 | failed | 1/3 graders passed |
| agent-behavior | council-arbiter-user-approved-reconciliation | 1 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-user-approved-reconciliation | 2 | failed | 0/3 graders passed |
| agent-behavior | council-arbiter-user-approved-reconciliation | 3 | failed | 1/3 graders passed |
| agent-behavior | council-arbiter-user-approved-reconciliation | 4 | failed | 1/3 graders passed |
| agent-behavior | council-critic-independent-evidence-review | 0 | failed | 2/3 graders passed |
| agent-behavior | council-critic-independent-evidence-review | 4 | failed | 2/3 graders passed |
| agent-behavior | council-critic-untrusted-path-confinement | 0 | failed | 1/4 graders passed |
| agent-behavior | council-critic-untrusted-path-confinement | 1 | failed | 0/4 graders passed |
| agent-behavior | council-critic-untrusted-path-confinement | 2 | failed | 0/4 graders passed |
| agent-behavior | council-critic-untrusted-path-confinement | 3 | failed | 0/4 graders passed |
| agent-behavior | council-critic-untrusted-path-confinement | 4 | failed | 0/4 graders passed |
| agent-behavior | data-science-engineering-coach-analysis-skill-primary | 0 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-analysis-skill-primary | 1 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-analysis-skill-primary | 2 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-analysis-skill-primary | 3 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-analysis-skill-primary | 4 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-blocked-write-recovery | 0 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-blocked-write-recovery | 1 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-blocked-write-recovery | 2 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-blocked-write-recovery | 3 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-blocked-write-recovery | 4 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-continuous-restore | 0 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-continuous-restore | 1 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-continuous-restore | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-continuous-restore | 3 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-continuous-restore | 4 | failed | 0/2 graders passed |
| agent-behavior | data-science-engineering-coach-durable-write-scan | 0 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-durable-write-scan | 1 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-durable-write-scan | 2 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-durable-write-scan | 3 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-durable-write-scan | 4 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-embedded-waiver | 3 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-high-scan-result | 1 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-high-scan-result | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-high-scan-result | 3 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-high-scan-result | 4 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-interrupted-write-recovery | 0 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-interrupted-write-recovery | 1 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-interrupted-write-recovery | 2 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-interrupted-write-recovery | 3 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-interrupted-write-recovery | 4 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-missing-scan-report | 1 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-missing-scan-report | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-missing-scan-report | 4 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-registry-parity | 0 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-registry-parity | 1 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-registry-parity | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-registry-parity | 3 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-registry-parity | 4 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-slug-mismatch | 0 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-slug-mismatch | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-slug-mismatch | 3 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-slug-mismatch | 4 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-valid-resume | 0 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-valid-resume | 1 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-valid-resume | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-ev02-valid-resume | 3 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-persists | 0 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-persists | 1 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-persists | 2 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-persists | 3 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-persists | 4 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-routing | 0 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-routing | 1 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-routing | 2 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-routing | 3 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluation-job-routing | 4 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research-blocked | 0 | failed | 3/4 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research-blocked | 1 | failed | 2/4 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research-blocked | 2 | failed | 3/4 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research-blocked | 3 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research-blocked | 4 | failed | 2/4 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research | 0 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research | 1 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research | 2 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research | 3 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-evaluator-research | 4 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-initialize-and-select | 0 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-initialize-and-select | 1 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-initialize-and-select | 2 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-initialize-and-select | 3 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-initialize-and-select | 4 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-pause-complete-resume | 0 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-pause-complete-resume | 1 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-pause-complete-resume | 2 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-pause-complete-resume | 3 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-pause-complete-resume | 4 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-missing-outcome | 0 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-missing-outcome | 1 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-missing-outcome | 2 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-missing-outcome | 3 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-missing-outcome | 4 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-unresolved-axis | 0 | failed | 1/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-unresolved-axis | 1 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-unresolved-axis | 2 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-unresolved-axis | 3 | failed | 0/7 graders passed |
| agent-behavior | data-science-engineering-coach-problem-framing-unresolved-axis | 4 | failed | 1/7 graders passed |
| agent-behavior | data-science-engineering-coach-reconciles-rpi-pipeline-results | 0 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-reconciles-rpi-pipeline-results | 1 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-reconciles-rpi-pipeline-results | 2 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-reconciles-rpi-pipeline-results | 4 | failed | 1/2 graders passed |
| agent-behavior | data-science-engineering-coach-reconstruct-state | 0 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-reconstruct-state | 1 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-reconstruct-state | 2 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-reconstruct-state | 3 | failed | 1/3 graders passed |
| agent-behavior | data-science-engineering-coach-reconstruct-state | 4 | failed | 2/3 graders passed |
| agent-behavior | data-science-engineering-coach-refuses-embedded-gate-waiver | 0 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-refuses-embedded-gate-waiver | 1 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-refuses-embedded-gate-waiver | 2 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-refuses-embedded-gate-waiver | 3 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-refuses-embedded-gate-waiver | 4 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-transition-detour | 0 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-transition-detour | 1 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-transition-detour | 2 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-transition-detour | 3 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-transition-detour | 4 | failed | 0/3 graders passed |
| agent-behavior | data-science-engineering-coach-transition-plain-language | 0 | failed | 0/4 graders passed |
| agent-behavior | data-science-engineering-coach-transition-plain-language | 1 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-transition-plain-language | 2 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-transition-plain-language | 3 | failed | 1/4 graders passed |
| agent-behavior | data-science-engineering-coach-transition-plain-language | 4 | failed | 1/4 graders passed |
| agent-behavior | dt-coach-mural-scope-upgrade-stops-seeding | 0 | failed | 2/3 graders passed |
| agent-behavior | dt-coach-mural-scope-upgrade-stops-seeding | 3 | failed | 2/3 graders passed |
| agent-behavior | dt-coach-mural-scope-upgrade-stops-seeding | 4 | failed | 2/3 graders passed |
| agent-behavior | dt-learning-tutor-class-recipe | 2 | failed | 2/3 graders passed |
| agent-behavior | engagement-report-generator-ado-saved-query | 0 | failed | 0/4 graders passed |
| agent-behavior | engagement-report-generator-ado-saved-query | 1 | failed | 0/4 graders passed |
| agent-behavior | engagement-report-generator-ado-saved-query | 2 | failed | 0/4 graders passed |
| agent-behavior | engagement-report-generator-ado-saved-query | 3 | failed | 0/4 graders passed |
| agent-behavior | engagement-report-generator-ado-saved-query | 4 | failed | 0/4 graders passed |
| agent-behavior | engagement-report-generator-separate-draft-approval | 0 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-generator-separate-draft-approval | 1 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-generator-separate-draft-approval | 2 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-generator-separate-draft-approval | 3 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-generator-separate-draft-approval | 4 | failed | 0/3 graders passed |
| agent-behavior | engagement-report-reviewer-grounding-and-privacy | 0 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-reviewer-grounding-and-privacy | 1 | failed | 2/3 graders passed |
| agent-behavior | engagement-report-reviewer-grounding-and-privacy | 2 | failed | 2/3 graders passed |
| agent-behavior | engagement-report-reviewer-untrusted-handoff-confinement | 0 | failed | 0/3 graders passed |
| agent-behavior | engagement-report-reviewer-untrusted-handoff-confinement | 1 | failed | 2/3 graders passed |
| agent-behavior | engagement-report-reviewer-untrusted-handoff-confinement | 2 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-reviewer-untrusted-handoff-confinement | 3 | failed | 1/3 graders passed |
| agent-behavior | engagement-report-reviewer-untrusted-handoff-confinement | 4 | failed | 1/3 graders passed |
| agent-behavior | experiment-designer-conditional-ml-route | 0 | failed | 4/7 graders passed |
| agent-behavior | experiment-designer-conditional-ml-route | 1 | failed | 6/7 graders passed |
| agent-behavior | experiment-designer-conditional-ml-route | 2 | failed | 4/7 graders passed |
| agent-behavior | experiment-designer-conditional-ml-route | 3 | failed | 5/7 graders passed |
| agent-behavior | experiment-designer-conditional-ml-route | 4 | failed | 5/7 graders passed |
| agent-behavior | experiment-designer-research-preparation | 1 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-research-preparation | 2 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-research-preparation | 3 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-research-preparation | 4 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-starts-explicit-challenge | 3 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-writes-post-execution-outcome | 0 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-writes-post-execution-outcome | 1 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-writes-post-execution-outcome | 2 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-writes-post-execution-outcome | 3 | failed | 2/3 graders passed |
| agent-behavior | experiment-designer-writes-post-execution-outcome | 4 | failed | 2/3 graders passed |
| agent-behavior | finding-deep-verifier-no-new-findings | 0 | failed | 1/2 graders passed |
| agent-behavior | finding-deep-verifier-no-new-findings | 1 | failed | 1/2 graders passed |
| agent-behavior | finding-deep-verifier-no-new-findings | 2 | failed | 1/2 graders passed |
| agent-behavior | finding-deep-verifier-no-new-findings | 3 | failed | 1/2 graders passed |
| agent-behavior | finding-deep-verifier-no-new-findings | 4 | failed | 1/2 graders passed |
| agent-behavior | finding-deep-verifier-verdict-blocks | 0 | failed | 1/4 graders passed |
| agent-behavior | finding-deep-verifier-verdict-blocks | 1 | failed | 1/4 graders passed |
| agent-behavior | finding-deep-verifier-verdict-blocks | 2 | failed | 0/4 graders passed |
| agent-behavior | finding-deep-verifier-verdict-blocks | 3 | failed | 1/4 graders passed |
| agent-behavior | finding-deep-verifier-verdict-blocks | 4 | failed | 0/4 graders passed |
| agent-behavior | functional-planner-frontmatter-name-and-description | 0 | failed | 2/3 graders passed |
| agent-behavior | functional-planner-frontmatter-name-and-description | 1 | failed | 2/3 graders passed |
| agent-behavior | functional-planner-frontmatter-name-and-description | 2 | failed | 2/3 graders passed |
| agent-behavior | functional-planner-frontmatter-name-and-description | 3 | failed | 2/3 graders passed |
| agent-behavior | functional-planner-frontmatter-name-and-description | 4 | failed | 1/3 graders passed |
| agent-behavior | functional-planner-handoff-target-and-label | 0 | failed | 2/3 graders passed |
| agent-behavior | functional-planner-jira-credential-loading-boundary | 0 | failed | 3/4 graders passed |
| agent-behavior | github-backlog-executor-frontmatter-name-and-description | 0 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-frontmatter-name-and-description | 1 | failed | 2/3 graders passed |
| agent-behavior | github-backlog-executor-frontmatter-name-and-description | 2 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-frontmatter-name-and-description | 3 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-frontmatter-name-and-description | 4 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-structural-template | 0 | failed | 4/5 graders passed |
| agent-behavior | github-backlog-executor-structural-template | 1 | failed | 4/5 graders passed |
| agent-behavior | github-backlog-executor-structural-template | 2 | failed | 4/5 graders passed |
| agent-behavior | github-backlog-executor-tool-restriction-fit | 0 | failed | 1/2 graders passed |
| agent-behavior | github-backlog-executor-tool-restriction-fit | 1 | failed | 1/2 graders passed |
| agent-behavior | github-backlog-executor-tool-restriction-fit | 2 | failed | 1/2 graders passed |
| agent-behavior | github-backlog-executor-tool-restriction-fit | 3 | failed | 1/2 graders passed |
| agent-behavior | github-backlog-executor-tool-restriction-fit | 4 | failed | 1/2 graders passed |
| agent-behavior | github-backlog-executor-user-invocable-flag | 0 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-user-invocable-flag | 1 | failed | 2/3 graders passed |
| agent-behavior | github-backlog-executor-user-invocable-flag | 2 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-user-invocable-flag | 3 | failed | 1/3 graders passed |
| agent-behavior | github-backlog-executor-user-invocable-flag | 4 | failed | 1/3 graders passed |
| agent-behavior | hve-demo-material-class-recipe | 0 | failed | 3/4 graders passed |
| agent-behavior | hve-demo-material-class-recipe | 1 | failed | 3/4 graders passed |
| agent-behavior | hve-demo-material-class-recipe | 2 | failed | 3/4 graders passed |
| agent-behavior | hve-demo-material-class-recipe | 3 | failed | 3/4 graders passed |
| agent-behavior | hve-demo-material-class-recipe | 4 | failed | 2/4 graders passed |
| agent-behavior | issue-triage-dismissal-request | 2 | failed | 2/3 graders passed |
| agent-behavior | issue-triage-dismissal-request | 4 | failed | 2/3 graders passed |
| agent-behavior | jira-backlog-executor-frontmatter-name-and-description | 0 | failed | 1/3 graders passed |
| agent-behavior | jira-backlog-executor-frontmatter-name-and-description | 1 | failed | 1/3 graders passed |
| agent-behavior | jira-backlog-executor-frontmatter-name-and-description | 2 | failed | 1/3 graders passed |
| agent-behavior | jira-backlog-executor-frontmatter-name-and-description | 3 | failed | 2/3 graders passed |
| agent-behavior | jira-backlog-executor-frontmatter-name-and-description | 4 | failed | 2/3 graders passed |
| agent-behavior | jira-backlog-executor-structural-template | 0 | failed | 4/5 graders passed |
| agent-behavior | jira-backlog-executor-structural-template | 1 | failed | 4/5 graders passed |
| agent-behavior | jira-backlog-executor-structural-template | 2 | failed | 4/5 graders passed |
| agent-behavior | jira-backlog-executor-structural-template | 4 | failed | 4/5 graders passed |
| agent-behavior | jira-backlog-executor-user-invocable-flag | 0 | failed | 2/3 graders passed |
| agent-behavior | jira-backlog-executor-user-invocable-flag | 1 | failed | 1/3 graders passed |
| agent-behavior | jira-backlog-executor-user-invocable-flag | 2 | failed | 1/3 graders passed |
| agent-behavior | jira-backlog-executor-user-invocable-flag | 3 | failed | 1/3 graders passed |
| agent-behavior | jira-backlog-executor-user-invocable-flag | 4 | failed | 1/3 graders passed |
| agent-behavior | outlook-drafter-ambiguous-create-attempt | 0 | failed | 1/2 graders passed |
| agent-behavior | outlook-drafter-ambiguous-create-attempt | 1 | failed | 1/2 graders passed |
| agent-behavior | outlook-drafter-ambiguous-create-attempt | 2 | failed | 1/2 graders passed |
| agent-behavior | outlook-drafter-ambiguous-create-attempt | 3 | failed | 1/2 graders passed |
| agent-behavior | outlook-drafter-ambiguous-create-attempt | 4 | failed | 1/2 graders passed |
| agent-behavior | outlook-drafter-approved-html-create | 0 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-approved-html-create | 1 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-approved-html-create | 2 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-approved-html-create | 3 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-approved-html-create | 4 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-path-traversal-unapproved-handoff | 0 | failed | 0/3 graders passed |
| agent-behavior | outlook-drafter-path-traversal-unapproved-handoff | 1 | failed | 1/3 graders passed |
| agent-behavior | outlook-drafter-path-traversal-unapproved-handoff | 2 | failed | 0/3 graders passed |
| agent-behavior | outlook-drafter-path-traversal-unapproved-handoff | 3 | failed | 1/3 graders passed |
| agent-behavior | outlook-drafter-path-traversal-unapproved-handoff | 4 | failed | 1/3 graders passed |
| agent-behavior | outlook-drafter-table-conversion-failure | 0 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-table-conversion-failure | 1 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-table-conversion-failure | 2 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-table-conversion-failure | 3 | failed | 0/2 graders passed |
| agent-behavior | outlook-drafter-table-conversion-failure | 4 | failed | 1/2 graders passed |
| agent-behavior | pptx-subagent-partial-rebuild-flags | 0 | failed | 1/3 graders passed |
| agent-behavior | pptx-subagent-partial-rebuild-flags | 1 | failed | 1/3 graders passed |
| agent-behavior | pptx-subagent-partial-rebuild-flags | 2 | failed | 1/3 graders passed |
| agent-behavior | pptx-subagent-partial-rebuild-flags | 3 | failed | 1/3 graders passed |
| agent-behavior | pptx-subagent-partial-rebuild-flags | 4 | failed | 1/3 graders passed |
| agent-behavior | pptx-subagent-task-and-paths | 2 | failed | 3/4 graders passed |
| agent-behavior | pptx-subagent-task-and-paths | 3 | failed | 2/4 graders passed |
| agent-behavior | prd-builder-class-recipe | 0 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-class-recipe | 1 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-class-recipe | 2 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-class-recipe | 3 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-class-recipe | 4 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-research-blocked | 0 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-research-blocked | 3 | failed | 2/3 graders passed |
| agent-behavior | prd-builder-research-blocked | 4 | failed | 2/3 graders passed |
| agent-behavior | rai-planner-cold-start-scoped-load | 0 | failed | 1/2 graders passed |
| agent-behavior | rai-planner-cold-start-scoped-load | 1 | failed | 0/2 graders passed |
| agent-behavior | rai-planner-cold-start-scoped-load | 2 | failed | 1/2 graders passed |
| agent-behavior | rai-planner-cold-start-scoped-load | 4 | failed | 1/2 graders passed |
| agent-behavior | rai-planner-from-security-plan-no-plans | 0 | failed | 1/2 graders passed |
| agent-behavior | rai-planner-mural-accept-reads-reference | 2 | failed | 2/3 graders passed |
| agent-behavior | rai-planner-mural-setup-and-login-verdicts | 0 | failed | 3/4 graders passed |
| agent-behavior | rai-planner-mural-setup-and-login-verdicts | 1 | failed | 3/4 graders passed |
| agent-behavior | rai-planner-phase2-mural-offer | 0 | failed | 2/3 graders passed |
| agent-behavior | rai-planner-phase2-mural-offer | 1 | failed | 2/3 graders passed |
| agent-behavior | rai-planner-phase2-mural-offer | 2 | failed | 2/3 graders passed |
| agent-behavior | rai-reviewer-audit-completion-contract | 0 | failed | 4/9 graders passed |
| agent-behavior | rai-reviewer-audit-completion-contract | 1 | failed | 7/9 graders passed |
| agent-behavior | rai-reviewer-audit-completion-contract | 2 | failed | 3/9 graders passed |
| agent-behavior | rai-reviewer-audit-completion-contract | 3 | failed | 7/9 graders passed |
| agent-behavior | rai-reviewer-audit-completion-contract | 4 | failed | 7/9 graders passed |
| agent-behavior | rai-skill-assessor-reference-first-plan-mode | 0 | failed | 0/3 graders passed |
| agent-behavior | rai-skill-assessor-reference-first-plan-mode | 1 | failed | 0/3 graders passed |
| agent-behavior | rai-skill-assessor-reference-first-plan-mode | 2 | failed | 0/3 graders passed |
| agent-behavior | rai-skill-assessor-reference-first-plan-mode | 3 | failed | 0/3 graders passed |
| agent-behavior | rai-skill-assessor-reference-first-plan-mode | 4 | failed | 0/3 graders passed |
| agent-behavior | security-planner-disclaimer-shown-resume | 1 | failed | 2/4 graders passed |
| agent-behavior | security-planner-disclaimer-shown-resume | 2 | failed | 2/4 graders passed |
| agent-behavior | security-planner-disclaimer-shown-resume | 3 | failed | 3/4 graders passed |
| agent-behavior | security-planner-disclaimer-shown-resume | 4 | failed | 2/4 graders passed |
| agent-behavior | security-planner-disclaimer-source-missing | 0 | failed | 1/2 graders passed |
| agent-behavior | security-planner-disclaimer-source-missing | 1 | failed | 1/2 graders passed |
| agent-behavior | security-reviewer-llm-agentic-preset | 0 | failed | 2/3 graders passed |
| agent-behavior | security-reviewer-llm-agentic-preset | 1 | failed | 2/3 graders passed |
| agent-behavior | security-reviewer-llm-agentic-preset | 2 | failed | 2/3 graders passed |
| agent-behavior | security-reviewer-llm-agentic-preset | 3 | failed | 2/3 graders passed |
| agent-behavior | security-reviewer-llm-agentic-preset | 4 | failed | 2/3 graders passed |
| agent-behavior | security-reviewer-plan-drift | 0 | failed | 4/5 graders passed |
| agent-behavior | security-reviewer-plan-drift | 1 | failed | 4/5 graders passed |
| agent-behavior | security-reviewer-plan-drift | 2 | failed | 4/5 graders passed |
| agent-behavior | security-reviewer-plan-drift | 3 | failed | 3/5 graders passed |
| agent-behavior | security-reviewer-plan-drift | 4 | failed | 4/5 graders passed |
| agent-behavior | skill-assessor-audit-mode-format | 0 | failed | 2/4 graders passed |
| agent-behavior | skill-assessor-audit-mode-format | 1 | failed | 1/4 graders passed |
| agent-behavior | skill-assessor-audit-mode-format | 2 | failed | 3/4 graders passed |
| agent-behavior | skill-assessor-audit-mode-format | 3 | failed | 3/4 graders passed |
| agent-behavior | skill-assessor-audit-mode-format | 4 | failed | 2/4 graders passed |
| agent-behavior | skill-assessor-plan-mode-vocabulary | 0 | failed | 0/2 graders passed |
| agent-behavior | skill-assessor-plan-mode-vocabulary | 1 | failed | 1/2 graders passed |
| agent-behavior | skill-assessor-plan-mode-vocabulary | 2 | failed | 1/2 graders passed |
| agent-behavior | skill-assessor-plan-mode-vocabulary | 3 | failed | 1/2 graders passed |
| agent-behavior | skill-assessor-plan-mode-vocabulary | 4 | failed | 0/2 graders passed |
| agent-behavior | sssc-planner-from-security-plan-selection | 0 | failed | 2/3 graders passed |
| agent-behavior | sssc-planner-from-security-plan-selection | 3 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-class-recipe | 0 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-class-recipe | 1 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-class-recipe | 2 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-class-recipe | 3 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-class-recipe | 4 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-diff-fallback-retains-supply-chain-files | 1 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-diff-fallback-retains-supply-chain-files | 4 | failed | 4/5 graders passed |
| agent-behavior | sssc-reviewer-vex-full-scan-shape | 1 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-vex-full-scan-shape | 3 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-vex-full-scan-shape | 4 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-vex-triage-from-report | 0 | failed | 0/3 graders passed |
| agent-behavior | sssc-reviewer-vex-triage-from-report | 1 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-vex-triage-from-report | 2 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-vex-triage-from-report | 3 | failed | 2/3 graders passed |
| agent-behavior | sssc-reviewer-vex-triage-from-report | 4 | failed | 0/3 graders passed |
| agent-behavior | system-architecture-reviewer-research-gate | 1 | failed | 1/2 graders passed |
| agent-behavior | system-architecture-reviewer-research-gate | 2 | failed | 1/2 graders passed |
| agent-behavior | system-architecture-reviewer-research-gate | 3 | failed | 1/2 graders passed |
| agent-behavior | system-architecture-reviewer-research-gate | 4 | failed | 1/2 graders passed |
| agent-behavior | system-architecture-reviewer-research-unavailable | 0 | failed | 1/3 graders passed |
| agent-behavior | system-architecture-reviewer-research-unavailable | 2 | failed | 1/3 graders passed |
| agent-behavior | system-architecture-reviewer-research-unavailable | 3 | failed | 2/3 graders passed |
| agent-behavior | system-architecture-reviewer-research-unavailable | 4 | failed | 1/3 graders passed |
| agent-behavior | ux-ui-designer-accessibility-routing | 0 | failed | 1/2 graders passed |
| agent-behavior | ux-ui-designer-accessibility-routing | 1 | failed | 1/2 graders passed |
| agent-behavior | ux-ui-designer-accessibility-routing | 2 | failed | 1/2 graders passed |
| agent-behavior | ux-ui-designer-accessibility-routing | 3 | failed | 1/2 graders passed |
| agent-behavior | ux-ui-designer-accessibility-routing | 4 | failed | 1/2 graders passed |
| agent-behavior | ux-ui-designer-artifact-mode-matrix | 0 | failed | 0/5 graders passed |
| agent-behavior | ux-ui-designer-artifact-mode-matrix | 1 | failed | 0/5 graders passed |
| agent-behavior | ux-ui-designer-artifact-mode-matrix | 2 | failed | 0/5 graders passed |
| agent-behavior | ux-ui-designer-artifact-mode-matrix | 3 | failed | 0/5 graders passed |
| agent-behavior | ux-ui-designer-artifact-mode-matrix | 4 | failed | 0/5 graders passed |
| agent-behavior | ux-ui-designer-figma-write-gate | 2 | failed | 2/3 graders passed |
| agent-behavior | ux-ui-designer-human-review-gate | 0 | failed | 1/3 graders passed |
| agent-behavior | ux-ui-designer-human-review-gate | 2 | failed | 1/3 graders passed |
| agent-behavior | ux-ui-designer-mural-environment-verdicts | 1 | failed | 3/4 graders passed |
| agent-behavior | ux-ui-designer-mural-environment-verdicts | 2 | failed | 3/4 graders passed |
| agent-behavior | ux-ui-designer-mural-environment-verdicts | 3 | failed | 3/4 graders passed |
| agent-behavior | ux-ui-designer-mural-environment-verdicts | 4 | failed | 2/4 graders passed |
| agent-behavior | ux-ui-designer-mural-extractor-metadata-boundary | 2 | failed | 3/4 graders passed |
| agent-behavior | ux-ui-designer-unsupported-cohort-guardrail | 0 | failed | 2/3 graders passed |
| agent-behavior | ux-ui-designer-unsupported-cohort-guardrail | 3 | failed | 2/3 graders passed |
| agent-behavior | vally-test-author-advisory-and-routing | 0 | failed | 2/3 graders passed |
| agent-behavior | vally-test-author-advisory-and-routing | 2 | failed | 0/3 graders passed |
| agent-behavior | vally-test-author-advisory-and-routing | 3 | failed | 1/3 graders passed |
| agent-behavior | vally-test-author-advisory-and-routing | 4 | failed | 0/3 graders passed |
| agent-behavior | vally-test-author-probes-order-sensitive-regex | 0 | failed | 3/4 graders passed |
| agent-conformance-accessibility-planner | aria-no-native-semantics-anti-pattern | None | failed | 1/2 graders passed |
| agent-conformance-accessibility-planner | automated-scan-not-equivalent-to-at-testing | None | failed | 1/2 graders passed |
| agent-conformance-accessibility-planner | cognitive-accessibility-plain-language-signal | None | failed | 0/2 graders passed |
| agent-conformance-accessibility-planner | conformance-claim-boundary-no-certification | None | failed | 1/2 graders passed |
| agent-conformance-accessibility-planner | evidence-gap-proportionate-flagging | None | failed | 1/2 graders passed |
| agent-conformance-accessibility-planner | phase-advance-explicit-denial-preserves-state | None | failed | 1/2 graders passed |
| agent-conformance-accessibility-planner | phase-advance-requires-explicit-confirmation | None | failed | 0/2 graders passed |
| agent-conformance-accessibility-planner | phase1-discovery-questions | None | failed | 0/2 graders passed |
| agent-conformance-accessibility-planner | phase2-framework-selection-with-defaults | None | failed | 0/2 graders passed |
| agent-conformance-accessibility-planner | phase6-prioritized-backlog-handoff | None | failed | 1/2 graders passed |
| agent-conformance-accessibility-planner | startup-disclaimer-before-phase1 | None | failed | 0/2 graders passed |
| agent-conformance-dt-coach | init-confirms-before-resuming-derived-project | None | failed | 0/3 graders passed |
| agent-conformance-dt-coach | method-1-identify-stakeholders | None | failed | 1/3 graders passed |
| agent-conformance-privacy-planner | capture-phase-exploratory-data-flow-questions | None | failed | 0/2 graders passed |
| agent-conformance-privacy-planner | data-minimization-unjustified-collection | None | failed | 1/2 graders passed |
| agent-conformance-privacy-planner | data-subject-rights-deletion-request | None | failed | 1/2 graders passed |
| agent-conformance-privacy-planner | high-risk-signal-flags-dpia-consideration | None | failed | 1/2 graders passed |
| agent-conformance-privacy-planner | legal-signoff-boundary-no-compliance-claim | None | failed | 1/2 graders passed |
| agent-conformance-privacy-planner | phase-advance-requires-explicit-confirmation | None | failed | 0/2 graders passed |
| agent-conformance-privacy-planner | proportionate-dpia-reasoning-routine-processing | None | failed | 0/2 graders passed |
| agent-conformance-privacy-planner | retention-indefinite-policy-gap | None | failed | 0/2 graders passed |
| agent-conformance-privacy-planner | startup-disclaimer-before-questions | None | failed | 0/2 graders passed |
| agent-conformance-privacy-planner | third-party-processing-undocumented-sharing | None | failed | 1/2 graders passed |
| agent-conformance-rai-planner | startup-disclaimer-and-framework-attribution | None | failed | 2/3 graders passed |
| agent-conformance-security-planner | startup-disclaimer-before-questions | None | failed | 0/3 graders passed |
| agent-conformance-sssc-planner | startup-disclaimer-and-standards-attribution | None | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-accessibility-identity-conformance | 0 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-accessibility-identity-conformance | 1 | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-accessibility-identity-conformance | 2 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-adr-byo-template-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-adr-byo-template-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-adr-byo-template-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-adr-identity-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-backlog-guardrails-conformance | 0 | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-backlog-guardrails-conformance | 1 | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-backlog-guardrails-conformance | 2 | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-backlog-handoff-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-backlog-handoff-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-backlog-handoff-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-bash-error-handling-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-bash-error-handling-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-bash-error-handling-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-bash-naming-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-bash-naming-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-bash-script-structure-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-community-interaction-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-community-interaction-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-community-interaction-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-content-policy-citation-neutral-public-comment | 0 | failed | 0/4 graders passed |
| behavior-conformance-instructions | instruction-content-policy-citation-neutral-public-comment | 1 | failed | 0/4 graders passed |
| behavior-conformance-instructions | instruction-content-policy-citation-neutral-public-comment | 2 | failed | 0/4 graders passed |
| behavior-conformance-instructions | instruction-content-policy-citation-no-deep-link | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-content-policy-citation-no-deep-link | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-content-policy-citation-no-deep-link | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-copilot-tracking-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-copilot-tracking-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-copilot-tracking-location-conformance | 1 | failed | 1/3 graders passed |
| behavior-conformance-instructions | instruction-copilot-tracking-location-conformance | 2 | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-data-science-licensing-posture-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-data-science-licensing-posture-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-diff-computation-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-diff-computation-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-disclaimer-language-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-disclaimer-language-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-docusaurus-edits-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-dt-coach-telemetry-conformance | 1 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-dt-coach-telemetry-conformance | 2 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-engagement-reporting-data-handling-conformance | 0 | failed | 1/3 graders passed |
| behavior-conformance-instructions | instruction-engagement-reporting-data-handling-conformance | 1 | failed | 1/3 graders passed |
| behavior-conformance-instructions | instruction-engagement-reporting-data-handling-conformance | 2 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-engagement-reporting-terminology-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-engagement-reporting-terminology-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-engagement-reporting-terminology-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-experiment-designer-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-experiment-designer-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-git-merge-conformance | 0 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-git-merge-conformance | 2 | failed | 1/3 graders passed |
| behavior-conformance-instructions | instruction-github-backlog-grooming-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-licensing-posture-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-pester-tests-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-pester-tests-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-pester-tests-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-planner-identity-base-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-comment-help-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-comment-help-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-comment-help-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-data-file-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-data-file-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-data-file-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-error-handling-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-error-handling-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-error-handling-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-module-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-module-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-script-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-script-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-powershell-script-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-pptx-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-privacy-identity-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-pull-request-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-pull-request-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-pull-request-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-cli-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-cli-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-cli-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-pathlib-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-pathlib-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-pathlib-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-subprocess-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-subprocess-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-script-subprocess-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-python-tests-pytest-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-tests-pytest-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-python-uv-projects-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-python-uv-projects-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-python-uv-projects-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-rai-identity-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rai-identity-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rai-license-posture-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rai-risk-classification-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rai-risk-classification-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-async-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-async-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-async-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-cargo-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-cargo-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-cargo-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-rust-error-handling-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-rust-error-handling-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-rust-http-unit-test-isolation-conformance | 1 | failed | 1/3 graders passed |
| behavior-conformance-instructions | instruction-rust-http-unit-test-isolation-conformance | 2 | failed | 0/3 graders passed |
| behavior-conformance-instructions | instruction-rust-naming-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-rust-naming-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-rust-naming-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-rust-tests-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-sssc-assessment-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-standards-mapping-conformance | 0 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-standards-mapping-conformance | 1 | failed | 1/3 graders passed |
| behavior-conformance-instructions | instruction-standards-mapping-conformance | 2 | failed | 2/3 graders passed |
| behavior-conformance-instructions | instruction-telemetry-overlay-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-telemetry-overlay-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-telemetry-overlay-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-terraform-module-structure-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-terraform-module-structure-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-terraform-naming-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-terraform-naming-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-tm7-generation-workflow-conformance | 0 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-tm7-generation-workflow-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-typescript-doc-comments-contract-content | 0 | failed | 3/4 graders passed |
| behavior-conformance-instructions | instruction-untrusted-content-boundary-conformance | 0 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-untrusted-content-boundary-conformance | 1 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-untrusted-content-boundary-conformance | 2 | failed | 0/2 graders passed |
| behavior-conformance-instructions | instruction-writing-style-conformance | 1 | failed | 1/2 graders passed |
| behavior-conformance-instructions | instruction-writing-style-conformance | 2 | failed | 1/2 graders passed |
| behavior-conformance-instructions | skill-ado-create-pull-request-conformance | 0 | failed | 1/3 graders passed |
| behavior-conformance-instructions | skill-ado-create-pull-request-conformance | 1 | failed | 1/3 graders passed |
| behavior-conformance-instructions | skill-mural-human-writeback-conformance | 2 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-accessibility-coverage-matrix-failed-probe | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-analysis-authoring-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-analysis-authoring-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-analysis-authoring-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-backlog-execute-dependency-resolution | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-backlog-execute-dependency-resolution | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-backlog-execute-dependency-resolution | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-backlog-execute-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-backlog-plan-readonly-boundary | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-backlog-plan-readonly-boundary | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-backlog-plan-readonly-boundary | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-backlog-templates-tool-trigger | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-backlog-templates-tool-trigger | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-backlog-templates-tool-trigger | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-code-review-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-code-review-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-content-and-secret-boundary | 0 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-content-and-secret-boundary | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-content-and-secret-boundary | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-exact-diff-decline | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-exact-diff-decline | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-exact-diff-decline | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-exporter-response-not-proof | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-exporter-response-not-proof | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-hostile-store-content | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-hostile-store-content | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-hostile-store-content | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-operator-owned-commands | 0 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-operator-owned-commands | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-operator-owned-commands | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-profile-policy-ambiguity | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-second-write-new-approval | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-second-write-new-approval | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-second-write-new-approval | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-tool-trigger | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-copilot-otel-metrics-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-cspell-config-conformance | 0 | failed | 4/6 graders passed |
| behavior-conformance-skills | skill-cspell-config-conformance | 2 | failed | 5/6 graders passed |
| behavior-conformance-skills | skill-customer-card-render-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-customer-card-render-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-customer-card-render-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-customer-card-render-tool-trigger | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-customer-card-render-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-data-catalog-ev02-valid-synthetic-output | 0 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-data-catalog-ev02-valid-synthetic-output | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-data-catalog-ev02-valid-synthetic-output | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-data-science-engineering-foundation-state-and-transition | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-ownership-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-ownership-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-ownership-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-tiers-and-validation-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-tiers-and-validation-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-validation-drift-provenance | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-validation-drift-provenance | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dataops-validation-drift-provenance | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-setup-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-setup-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-setup-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-setup-tool-trigger | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-video-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-video-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-demo-video-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dt-coaching-foundation-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dt-coaching-foundation-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dt-handoff-implementation-space-missing-approval | 2 | failed | 5/6 graders passed |
| behavior-conformance-skills | skill-dt-methods-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-dt-methods-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-dt-methods-tool-trigger | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-dt-methods-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-engagement-report-council-critique-conformance | 0 | failed | 0/4 graders passed |
| behavior-conformance-skills | skill-engagement-report-council-critique-conformance | 1 | failed | 1/4 graders passed |
| behavior-conformance-skills | skill-engagement-report-council-critique-conformance | 2 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-engagement-report-council-critique-ignore-stop | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-engagement-report-council-critique-ignore-stop | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-coverage-stop | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-coverage-stop | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-coverage-stop | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-ignore-protection | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-ignore-protection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-ignore-protection | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-ambiguous-create | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-ambiguous-create | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-ambiguous-create | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-conversion-stop | 0 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-conversion-stop | 1 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-conversion-stop | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-html | 0 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-html | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-engagement-reporting-outlook-html | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-current-fact-research | 0 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-current-fact-research | 1 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-current-fact-research | 2 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-ev02-valid-synthetic-output | 0 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-ev02-valid-synthetic-output | 1 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-ev02-valid-synthetic-output | 2 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-evaluation-design-knowledge | 0 | failed | 2/5 graders passed |
| behavior-conformance-skills | skill-evaluation-design-knowledge | 1 | failed | 2/5 graders passed |
| behavior-conformance-skills | skill-evaluation-design-knowledge | 2 | failed | 2/5 graders passed |
| behavior-conformance-skills | skill-experiment-design-ml-partitioning | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-experiment-design-mve-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-experiment-design-mve-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-experiment-design-mve-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-experiment-design-production-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-experiment-design-production-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-experiment-design-production-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-experiment-design-research-preparation | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-experiment-design-research-preparation | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-feasibility-ev02-negative-handoff-output | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-feasibility-ev02-negative-handoff-output | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-feasibility-ev02-negative-handoff-output | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-feasibility-handoff-emission-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-feasibility-handoff-emission-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-feasibility-profile-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-feasibility-profile-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-feasibility-profile-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-functional-planner-dependency-resolution | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-functional-planner-dependency-resolution | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-functional-planner-dependency-resolution | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-gh-code-scanning-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-git-commit-conformance | 0 | failed | 6/7 graders passed |
| behavior-conformance-skills | skill-git-commit-conformance | 1 | failed | 6/7 graders passed |
| behavior-conformance-skills | skill-git-commit-conformance | 2 | failed | 6/7 graders passed |
| behavior-conformance-skills | skill-hve-builder-convergence-guards | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-hve-demo-material-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-hve-demo-material-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-hve-demo-material-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-hve-demo-material-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-hve-demo-material-tool-trigger | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-hve-demo-material-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-mcsb-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-mcsb-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-mcsb-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ml-experimentation-authority | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ml-experimentation-authority | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-ml-experimentation-authority | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-aggregation-default | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-assess-brd-handoff-read-only | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-assess-investigate | 0 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-assess-investigate | 1 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-assess-investigate | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-bleed-detection | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-bleed-detection | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-bleed-detection | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-eligible | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-eligible | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-eligible | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-ineligible | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-ineligible | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-ineligible | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-placeholder | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-placeholder | 1 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-placeholder | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-brd-handoff-target-mismatch | 1 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-caution-reference-unavailable | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-confidence-derivation | 0 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-confidence-derivation | 1 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-confidence-derivation | 2 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-create-investigate-oh0-stop | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-d4-d6-provisional | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-human-owned-status | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-justified-individual-exception | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-justified-individual-exception | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-late-privacy-gate | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-late-privacy-gate | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-privacy-planner-gate | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-privacy-planner-unavailable | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-privacy-planner-unavailable | 1 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-privacy-planner-unavailable | 2 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-rai-ai-alone-no-routing | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-rai-ai-alone-no-routing | 1 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-rai-ai-alone-no-routing | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-rai-material-impact-routing | 0 | failed | 1/4 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-rai-material-impact-routing | 1 | failed | 1/4 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-rai-material-impact-routing | 2 | failed | 1/4 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-tool-trigger | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-tool-trigger | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-outcome-hypothesis-tool-trigger | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-agentic-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-cicd-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-cicd-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-cicd-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-cicd-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-docker-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-infrastructure-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-infrastructure-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-infrastructure-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-infrastructure-knowledge | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-llm-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-llm-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-llm-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-mcp-knowledge | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-mcp-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-owasp-top-10-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-powerpoint-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-privacy-standards-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-privacy-standards-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-proposal-response-authority-boundary | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-proposal-response-authority-boundary | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-proposal-response-missing-outcome-evidence | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-proposal-response-rerender-replaces | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-proposal-response-rerender-replaces | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-proposal-response-rerender-replaces | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-proposal-response-source-immutability | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-proposal-response-source-immutability | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-proposal-response-source-immutability | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-python-foundational-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-python-foundational-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-python-foundational-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-requirements-author-research-receipt | 0 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-requirements-author-research-receipt | 1 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-requirements-author-research-receipt | 2 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-rpi-challenger-contract | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-critique-contradiction-versus-incomplete-evidence | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-critique-contradiction-versus-incomplete-evidence | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-implement-changes-record-heading | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-implement-human-review-scope | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-implement-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-implement-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-plan-critique-repeated-interruption | 0 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-plan-critique-skip | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-plan-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-plan-tool-trigger | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-plan-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-research-closeout-topic-bottom-line | 0 | failed | 4/5 graders passed |
| behavior-conformance-skills | skill-rpi-research-closeout-topic-bottom-line | 1 | failed | 4/5 graders passed |
| behavior-conformance-skills | skill-rpi-research-closeout-topic-bottom-line | 2 | failed | 3/5 graders passed |
| behavior-conformance-skills | skill-rpi-research-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-research-tool-trigger | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-review-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-rpi-review-interrupted-resume | 2 | failed | 0/1 graders passed |
| behavior-conformance-skills | skill-rpi-review-tool-trigger | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-rpi-review-tool-trigger | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-security-planning-native-loop-bleed | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-security-planning-native-loop-bleed | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-synth-data-generate-missing-decisions | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-synth-data-generate-missing-decisions | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-synth-data-generate-missing-decisions | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-synth-data-generate-stale-replacement | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-activation-contract | 1 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-activation-contract | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-figma-confirmation-boundary | 0 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-figma-confirmation-boundary | 1 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-figma-confirmation-boundary | 2 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-frame-needs-source-safety | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-frame-needs-source-safety | 1 | failed | 0/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-frame-needs-source-safety | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-handoff-intent-authority | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-handoff-intent-authority | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-handoff-intent-authority | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-inclusion-boundary | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-inclusion-boundary | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-inclusion-boundary | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-map-journey-from-problem-framing | 0 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-map-journey-from-problem-framing | 1 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-map-journey-from-problem-framing | 2 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-missing-destination-input | 0 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-missing-destination-input | 1 | failed | 3/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-mural-untrusted-boundary | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-mural-untrusted-boundary | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-mural-untrusted-boundary | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-non-ux-bleed | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-non-ux-bleed | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-non-ux-bleed | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-sketch-structure-evidence-bound | 0 | failed | 3/5 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-sketch-structure-evidence-bound | 1 | failed | 2/5 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-sketch-structure-evidence-bound | 2 | failed | 2/5 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-sketch-structure-projection-boundary | 0 | failed | 1/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-sketch-structure-projection-boundary | 1 | failed | 2/4 graders passed |
| behavior-conformance-skills | skill-ux-artifacts-sketch-structure-projection-boundary | 2 | failed | 1/4 graders passed |
| behavior-conformance-skills | skill-ux-coaching-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-direct-entry-problem-framing | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-direct-entry-problem-framing | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-resume-reconstruction | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-resume-reconstruction | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-tool-trigger | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-tool-trigger | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-ux-coaching-tool-trigger | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-vally-tests-bleed-detection | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-vally-tests-bleed-detection | 1 | failed | 2/3 graders passed |
| behavior-conformance-skills | skill-vally-tests-bleed-detection | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-vally-tests-corpus-import | 0 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-vally-tests-corpus-import | 2 | failed | 0/2 graders passed |
| behavior-conformance-skills | skill-vally-tests-from-artifact | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-vally-tests-tool-trigger | 0 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-vally-tests-tool-trigger | 1 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-vally-tests-tool-trigger | 2 | failed | 1/3 graders passed |
| behavior-conformance-skills | skill-video-to-gif-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-video-to-gif-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-video-to-gif-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-video-to-gif-knowledge | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-vscode-playwright-bleed-detection | 0 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-vscode-playwright-bleed-detection | 1 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-vscode-playwright-bleed-detection | 2 | failed | 1/2 graders passed |
| behavior-conformance-skills | skill-vscode-playwright-tool-trigger | 2 | failed | 0/2 graders passed |
| codex-port-edge-edge-path-01 | edge-edge-path-01-rpi-lifecycle | None | failed | 9/10 graders passed |
| codex-port-edge-edge-path-02 | edge-edge-path-02-code-review | None | failed | 1/4 graders passed |
| codex-port-edge-edge-path-06 | edge-edge-path-06-data-science-coach | None | failed | 1/2 graders passed |
| codex-port-edge-edge-path-08 | edge-edge-path-08-dt-rpi-handoff | None | failed | 3/4 graders passed |
| skill-quality | architecture-review-record-and-authority | 0 | failed | 1/3 graders passed |
| skill-quality | architecture-review-record-and-authority | 1 | failed | 2/3 graders passed |
| skill-quality | architecture-review-record-and-authority | 2 | failed | 0/3 graders passed |
| skill-quality | owasp-cicd-pipeline-poisoning | 0 | failed | 0/2 graders passed |
| skill-quality | owasp-cicd-pipeline-poisoning | 2 | failed | 1/2 graders passed |

## Blocked selections

- `agent-behavior/brd-builder-executes-research-segment` (5 trials): agent-behavior/brd-builder-executes-research-segment: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/brd-builder-reconciles-completed-research-segment` (5 trials): agent-behavior/brd-builder-reconciles-completed-research-segment: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/code-review-resume-native-edit` (5 trials): agent-behavior/code-review-resume-native-edit/review-resume-uses-native-edit: The original write_only disallow rule names bash, powershell, run_in_terminal. Commands without a proven Windows PowerShell wrapper remain exec_command, whose write targets the current Vally matcher does not extract. Enforcement across all native command paths remains unverified.
- `agent-behavior/data-science-engineering-coach-blocks-rpi-customer-write-when-scanner-unavailable` (5 trials): agent-behavior/data-science-engineering-coach-blocks-rpi-customer-write-when-scanner-unavailable: tool-call simulation is unsupported; agent-behavior/data-science-engineering-coach-blocks-rpi-customer-write-when-scanner-unavailable/data-science-rpi-scanner-failure-observed: The exact anchored alternatives powershell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.
- `agent-behavior/data-science-engineering-coach-produces-rpi-pipeline-delivery` (5 trials): agent-behavior/data-science-engineering-coach-produces-rpi-pipeline-delivery: tool-call simulation is unsupported; agent-behavior/data-science-engineering-coach-produces-rpi-pipeline-delivery: recoverable agent-duration limit awaits full delegated/background runtime acceptance; agent-behavior/data-science-engineering-coach-produces-rpi-pipeline-delivery/data-science-rpi-clean-scan-observed: The exact anchored alternatives powershell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.
- `agent-behavior/dt-coach-denied-board-intent` (5 trials): agent-behavior/dt-coach-denied-board-intent: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/dt-coach-lifecycle-continuity` (5 trials): agent-behavior/dt-coach-lifecycle-continuity: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/dt-coach-state-write-failure-recovery` (5 trials): agent-behavior/dt-coach-state-write-failure-recovery: tool-call simulation is unsupported; agent-behavior/dt-coach-state-write-failure-recovery: recoverable agent-duration limit awaits full delegated/background runtime acceptance; agent-behavior/dt-coach-state-write-failure-recovery/coach-state-writer-failure-observed: The exact anchored alternatives bash, shell, powershell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.
- `agent-behavior/experiment-designer-ambiguous-classification` (5 trials): agent-behavior/experiment-designer-ambiguous-classification: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-backlog-consent` (5 trials): agent-behavior/experiment-designer-backlog-consent: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-critiques-execution-plan` (5 trials): agent-behavior/experiment-designer-critiques-execution-plan: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-executes-convergence-research` (5 trials): agent-behavior/experiment-designer-executes-convergence-research: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-failed-tracking-write` (5 trials): agent-behavior/experiment-designer-failed-tracking-write: tool-call simulation is unsupported; agent-behavior/experiment-designer-failed-tracking-write: recoverable agent-duration limit awaits full delegated/background runtime acceptance; agent-behavior/experiment-designer-failed-tracking-write/expdes-failed-save-writer-error-observed: The exact anchored alternatives powershell, bash, shell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.
- `agent-behavior/experiment-designer-plans-execution` (5 trials): agent-behavior/experiment-designer-plans-execution: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-produces-execution-rpi-artifacts` (5 trials): agent-behavior/experiment-designer-produces-execution-rpi-artifacts: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-progressive-hypotheses` (5 trials): agent-behavior/experiment-designer-progressive-hypotheses: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-reconciles-convergence-research` (5 trials): agent-behavior/experiment-designer-reconciles-convergence-research: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-reviews-execution` (5 trials): agent-behavior/experiment-designer-reviews-execution: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-supported-classification` (5 trials): agent-behavior/experiment-designer-supported-classification: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/experiment-designer-weak-evidence-rollback` (5 trials): agent-behavior/experiment-designer-weak-evidence-rollback: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/prd-builder-executes-approved-authoring-plan` (5 trials): agent-behavior/prd-builder-executes-approved-authoring-plan: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/prd-builder-reconciles-completed-authoring-segment` (5 trials): agent-behavior/prd-builder-reconciles-completed-authoring-segment: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/security-planner-phase6-no-drift-evidence` (5 trials): agent-behavior/security-planner-phase6-no-drift-evidence: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/security-planner-phase6-with-drift-evidence` (5 trials): agent-behavior/security-planner-phase6-with-drift-evidence: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/system-architecture-reviewer-produces-convergence-research` (5 trials): agent-behavior/system-architecture-reviewer-produces-convergence-research: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/system-architecture-reviewer-writes-record-first-handoffs` (5 trials): agent-behavior/system-architecture-reviewer-writes-record-first-handoffs: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `agent-behavior/ux-ui-designer-problem-framing-route` (5 trials): agent-behavior/ux-ui-designer-problem-framing-route/routes-to-problem-framing: This program has an alternate pass path requiring both skill_activation (or a skill tool call) and a view/read/read_file tool call naming the reference. The current adapter does not emit those event identities for Codex skill loading or command reads. Any separate output-based pass branch is preserved and not evaluated by this audit.
- `agent-behavior/ux-ui-designer-stakeholder-advocacy-terminal-route` (5 trials): agent-behavior/ux-ui-designer-stakeholder-advocacy-terminal-route/routes-to-stakeholder-advocacy: This program has an alternate pass path requiring both skill_activation (or a skill tool call) and a view/read/read_file tool call naming the reference. The current adapter does not emit those event identities for Codex skill loading or command reads. Any separate output-based pass branch is preserved and not evaluated by this audit.
- `agent-conformance-dt-coach/active-coaching-open-ended-questions` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/active-coaching-periodic-summary` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/backward-transition-accepted` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/collaborate-not-execute` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/empowers-with-choices` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/hint-broad-direction-first` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/hint-escalation-on-repeated-confusion` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/init-asks-for-project-slug` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/init-asks-for-short-project-name-when-context-is-insufficient` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/init-clarifies-context` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/init-defaults-to-method-1` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-1-frozen-vs-fluid` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-2-research-planning` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-3-pattern-recognition` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-4-divergent-ideation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-5-concept-validation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-6-scrappy-prototypes` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-7-feasibility-testing` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-8-systematic-validation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-9-continuous-optimization` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/method-transition-recap-and-confirm` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/never-make-users-feel-foolish` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/no-multiple-choice-quizzes` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/no-prescriptive-solutions` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/no-unsolicited-method-change` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/session-closure-summary` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/session-resumption` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/short-conversational-responses` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/think-speak-empower-pattern` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-dt-coach/transparent-method-shift` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/ai-security-modeling-prompt-injection` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/consequential-use-risk-recognition` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/evidence-backed-controls-gap-disclosure` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/phase-advance-requires-explicit-confirmation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/phase1-ai-system-scoping-questions` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/phase2-prohibited-uses-gate-first` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/proportionate-low-risk-classification` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/regulatory-signoff-boundary-no-certification` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-rai-planner/standards-mapping-trustworthiness-characteristic` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/ai-component-detection-recommends-rai` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/authorization-missing-check` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/phase1-curiosity-first-invitation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/phase1-hard-gate-requires-confirmation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/resume-null-disclaimer-before-work` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/resume-shown-suppresses-disclaimer` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/risk-prioritization-proportionate-ranking` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/security-signoff-boundary-no-certification` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/sensitive-data-handling-logging` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/stride-reasoning-category-selection` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-security-planner/trust-boundary-identification` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/artifact-signing-and-sbom-gap` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/build-provenance-gap` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/dependency-maintenance-pinning-gap` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/links-existing-security-plan-context` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/missing-evidence-handling-unverified-claim` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/phase1-curiosity-first-invitation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/phase1-hard-gate-requires-confirmation` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/phase5-prioritized-backlog-handoff` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `agent-conformance-sssc-planner/workflow-token-permissions-overbroad` (1 trials): one or more selected evals failed planning; Vally plan contains error diagnostics; selected plan has no complete trial items
- `baseline-equivalence-baseline/ambiguous-spec-fix-bug` (3 trials): baseline-equivalence-baseline/ambiguous-spec-fix-bug: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/ambiguous-spec-improve-style` (3 trials): baseline-equivalence-baseline/ambiguous-spec-improve-style: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/ambiguous-spec-make-it-faster` (3 trials): baseline-equivalence-baseline/ambiguous-spec-make-it-faster: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/ambiguous-spec-update-thing` (3 trials): baseline-equivalence-baseline/ambiguous-spec-update-thing: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/ambiguous-spec-vague-feature` (3 trials): baseline-equivalence-baseline/ambiguous-spec-vague-feature: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/code-error-explain-indexerror` (3 trials): baseline-equivalence-baseline/code-error-explain-indexerror: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/code-hello-world-python` (3 trials): baseline-equivalence-baseline/code-hello-world-python: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/code-idiom-translate-python-to-js` (3 trials): baseline-equivalence-baseline/code-idiom-translate-python-to-js: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/code-reverse-string-rust` (3 trials): baseline-equivalence-baseline/code-reverse-string-rust: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/code-walkthrough-fizzbuzz` (3 trials): baseline-equivalence-baseline/code-walkthrough-fizzbuzz: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/factual-arithmetic-basic` (3 trials): baseline-equivalence-baseline/factual-arithmetic-basic: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/factual-geo-france-capital` (3 trials): baseline-equivalence-baseline/factual-geo-france-capital: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/factual-history-moon-landing-year` (3 trials): baseline-equivalence-baseline/factual-history-moon-landing-year: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/factual-science-water-formula` (3 trials): baseline-equivalence-baseline/factual-science-water-formula: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/factual-units-mile-to-km` (3 trials): baseline-equivalence-baseline/factual-units-mile-to-km: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/instruction-bleed-greeting` (3 trials): baseline-equivalence-baseline/instruction-bleed-greeting: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/instruction-bleed-joke` (3 trials): baseline-equivalence-baseline/instruction-bleed-joke: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/instruction-bleed-off-policy-framing` (3 trials): baseline-equivalence-baseline/instruction-bleed-off-policy-framing: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/instruction-bleed-persona-name-leak` (3 trials): baseline-equivalence-baseline/instruction-bleed-persona-name-leak: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/instruction-bleed-system-prompt-mention` (3 trials): baseline-equivalence-baseline/instruction-bleed-system-prompt-mention: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/multi-turn-confirm-action` (3 trials): baseline-equivalence-baseline/multi-turn-confirm-action: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/multi-turn-context-recall` (3 trials): baseline-equivalence-baseline/multi-turn-context-recall: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/multi-turn-correct-misunderstanding` (3 trials): baseline-equivalence-baseline/multi-turn-correct-misunderstanding: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/multi-turn-followup-clarify` (3 trials): baseline-equivalence-baseline/multi-turn-followup-clarify: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/multi-turn-refine-summary` (3 trials): baseline-equivalence-baseline/multi-turn-refine-summary: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/scope-redirect-movie-recommendation` (3 trials): baseline-equivalence-baseline/scope-redirect-movie-recommendation: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/scope-redirect-recipe` (3 trials): baseline-equivalence-baseline/scope-redirect-recipe: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/scope-redirect-sports` (3 trials): baseline-equivalence-baseline/scope-redirect-sports: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/scope-redirect-travel-tips` (3 trials): baseline-equivalence-baseline/scope-redirect-travel-tips: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/scope-redirect-weather` (3 trials): baseline-equivalence-baseline/scope-redirect-weather: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/tool-trigger-find-license` (3 trials): baseline-equivalence-baseline/tool-trigger-find-license: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/tool-trigger-list-scripts` (3 trials): baseline-equivalence-baseline/tool-trigger-list-scripts: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/tool-trigger-memory-only` (3 trials): baseline-equivalence-baseline/tool-trigger-memory-only: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/tool-trigger-package-json` (3 trials): baseline-equivalence-baseline/tool-trigger-package-json: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-baseline/tool-trigger-readme-summary` (3 trials): baseline-equivalence-baseline/tool-trigger-readme-summary: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/ambiguous-spec-fix-bug` (3 trials): baseline-equivalence-customized/ambiguous-spec-fix-bug: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/ambiguous-spec-improve-style` (3 trials): baseline-equivalence-customized/ambiguous-spec-improve-style: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/ambiguous-spec-make-it-faster` (3 trials): baseline-equivalence-customized/ambiguous-spec-make-it-faster: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/ambiguous-spec-update-thing` (3 trials): baseline-equivalence-customized/ambiguous-spec-update-thing: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/ambiguous-spec-vague-feature` (3 trials): baseline-equivalence-customized/ambiguous-spec-vague-feature: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/code-error-explain-indexerror` (3 trials): baseline-equivalence-customized/code-error-explain-indexerror: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/code-hello-world-python` (3 trials): baseline-equivalence-customized/code-hello-world-python: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/code-idiom-translate-python-to-js` (3 trials): baseline-equivalence-customized/code-idiom-translate-python-to-js: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/code-reverse-string-rust` (3 trials): baseline-equivalence-customized/code-reverse-string-rust: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/code-walkthrough-fizzbuzz` (3 trials): baseline-equivalence-customized/code-walkthrough-fizzbuzz: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/factual-arithmetic-basic` (3 trials): baseline-equivalence-customized/factual-arithmetic-basic: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/factual-geo-france-capital` (3 trials): baseline-equivalence-customized/factual-geo-france-capital: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/factual-history-moon-landing-year` (3 trials): baseline-equivalence-customized/factual-history-moon-landing-year: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/factual-science-water-formula` (3 trials): baseline-equivalence-customized/factual-science-water-formula: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/factual-units-mile-to-km` (3 trials): baseline-equivalence-customized/factual-units-mile-to-km: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/instruction-bleed-greeting` (3 trials): baseline-equivalence-customized/instruction-bleed-greeting: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/instruction-bleed-joke` (3 trials): baseline-equivalence-customized/instruction-bleed-joke: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/instruction-bleed-off-policy-framing` (3 trials): baseline-equivalence-customized/instruction-bleed-off-policy-framing: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/instruction-bleed-persona-name-leak` (3 trials): baseline-equivalence-customized/instruction-bleed-persona-name-leak: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/instruction-bleed-system-prompt-mention` (3 trials): baseline-equivalence-customized/instruction-bleed-system-prompt-mention: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/multi-turn-confirm-action` (3 trials): baseline-equivalence-customized/multi-turn-confirm-action: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/multi-turn-context-recall` (3 trials): baseline-equivalence-customized/multi-turn-context-recall: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/multi-turn-correct-misunderstanding` (3 trials): baseline-equivalence-customized/multi-turn-correct-misunderstanding: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/multi-turn-followup-clarify` (3 trials): baseline-equivalence-customized/multi-turn-followup-clarify: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/multi-turn-refine-summary` (3 trials): baseline-equivalence-customized/multi-turn-refine-summary: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/scope-redirect-movie-recommendation` (3 trials): baseline-equivalence-customized/scope-redirect-movie-recommendation: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/scope-redirect-recipe` (3 trials): baseline-equivalence-customized/scope-redirect-recipe: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/scope-redirect-sports` (3 trials): baseline-equivalence-customized/scope-redirect-sports: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/scope-redirect-travel-tips` (3 trials): baseline-equivalence-customized/scope-redirect-travel-tips: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/scope-redirect-weather` (3 trials): baseline-equivalence-customized/scope-redirect-weather: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/tool-trigger-find-license` (3 trials): baseline-equivalence-customized/tool-trigger-find-license: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/tool-trigger-list-scripts` (3 trials): baseline-equivalence-customized/tool-trigger-list-scripts: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/tool-trigger-memory-only` (3 trials): baseline-equivalence-customized/tool-trigger-memory-only: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/tool-trigger-package-json` (3 trials): baseline-equivalence-customized/tool-trigger-package-json: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `baseline-equivalence-customized/tool-trigger-readme-summary` (3 trials): baseline-equivalence-customized/tool-trigger-readme-summary: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `behavior-conformance-skills/skill-dt-canonical-deck-build-failure` (3 trials): behavior-conformance-skills/skill-dt-canonical-deck-build-failure: tool-call simulation is unsupported; behavior-conformance-skills/skill-dt-canonical-deck-build-failure/skill-dt-canonical-deck-build-failure-power-point--df48f503: The exact anchored alternatives powershell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.
- `behavior-conformance-skills/skill-dt-figma-export-authority-consent` (3 trials): behavior-conformance-skills/skill-dt-figma-export-authority-consent stimulus: MCP servers are unsupported by this runner
- `behavior-conformance-skills/skill-dt-figma-export-conformance` (3 trials): behavior-conformance-skills/skill-dt-figma-export-conformance stimulus: MCP servers are unsupported by this runner
- `behavior-conformance-skills/skill-dt-figma-export-external-write-partial-failure` (3 trials): behavior-conformance-skills/skill-dt-figma-export-external-write-partial-failure stimulus: MCP servers are unsupported by this runner
- `behavior-conformance-skills/skill-dt-figma-export-fail-closed` (3 trials): behavior-conformance-skills/skill-dt-figma-export-fail-closed stimulus: MCP servers are unsupported by this runner
- `behavior-conformance-skills/skill-dt-figma-export-recovery-resume` (3 trials): behavior-conformance-skills/skill-dt-figma-export-recovery-resume stimulus: MCP servers are unsupported by this runner
- `behavior-conformance-skills/skill-dt-method-next-write-failure` (3 trials): behavior-conformance-skills/skill-dt-method-next-write-failure: tool-call simulation is unsupported; behavior-conformance-skills/skill-dt-method-next-write-failure/skill-dt-method-next-write-failure-simulated-state-bb8301d9: The exact anchored alternatives powershell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.
- `behavior-conformance-skills/skill-dt-rpi-integration-tiered-context-continuity` (3 trials): behavior-conformance-skills/skill-dt-rpi-integration-tiered-context-continuity: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `behavior-conformance-skills/skill-dt-rpi-integration-validation-and-dispatch-failure` (3 trials): behavior-conformance-skills/skill-dt-rpi-integration-validation-and-dispatch-failure: tool-call simulation is unsupported; behavior-conformance-skills/skill-dt-rpi-integration-validation-and-dispatch-failure: recoverable agent-duration limit awaits full delegated/background runtime acceptance
- `behavior-conformance-skills/skill-git-commit-cancellation` (3 trials): behavior-conformance-skills/skill-git-commit-cancellation/skill-git-commit-cancel-literal-stage: The exact anchored alternatives powershell, bash, shell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.; behavior-conformance-skills/skill-git-commit-cancellation/skill-git-commit-cancel-literal-rollback: The exact anchored alternatives powershell, bash, shell omit exec_command. Only explicit single-payload PowerShell wrappers on a Windows backend have verified shell normalization; other native commands remain exec_command. This predicate is not verified across every execution path.

## Final reconciliation

Independent raw JSONL reconciliation: **1,310 passed, 927 grading failures, 0 execution errors**, with **439 preflight-blocked trials**. All **2,676** planned trials are accounted for, with no duplicate executed trial IDs or unrun remainder. Pass rate among the 2,237 graded trials: **58.6%**. Queue duration: approximately **7 hours 43 minutes**. Blocked trials were not executed and do not establish acceptance. No failed cases were fixed or retried.

Evidence: `C:/repos/hve-core-codex-artifacts/luna-medium-20261014/final-reconciliation.json`.

| Eval | Passed | Failed | Errors | Blocked |
| --- | ---: | ---: | ---: | ---: |
| agent-behavior | 604 | 456 | 0 | 140 |
| agent-conformance-accessibility-planner | 0 | 11 | 0 | 0 |
| agent-conformance-dt-coach | 0 | 2 | 0 | 30 |
| agent-conformance-privacy-planner | 0 | 10 | 0 | 0 |
| agent-conformance-rai-planner | 0 | 1 | 0 | 9 |
| agent-conformance-security-planner | 0 | 1 | 0 | 11 |
| agent-conformance-sssc-planner | 0 | 1 | 0 | 9 |
| baseline-equivalence-baseline | 0 | 0 | 0 | 105 |
| baseline-equivalence-customized | 0 | 0 | 0 | 105 |
| behavior-conformance-instructions | 109 | 131 | 0 | 0 |
| behavior-conformance-skills | 576 | 303 | 0 | 30 |
| codex-port-edge-edge-path-01 | 0 | 1 | 0 | 0 |
| codex-port-edge-edge-path-02 | 0 | 1 | 0 | 0 |
| codex-port-edge-edge-path-03 | 1 | 0 | 0 | 0 |
| codex-port-edge-edge-path-04 | 1 | 0 | 0 | 0 |
| codex-port-edge-edge-path-05 | 1 | 0 | 0 | 0 |
| codex-port-edge-edge-path-06 | 0 | 1 | 0 | 0 |
| codex-port-edge-edge-path-08 | 0 | 1 | 0 | 0 |
| codex-port-edge-edge-path-09 | 1 | 0 | 0 | 0 |
| codex-port-edge-edge-path-10 | 1 | 0 | 0 | 0 |
| codex-port-edge-preservation | 2 | 0 | 0 | 0 |
| script-validation | 7 | 2 | 0 | 0 |
| skill-quality | 7 | 5 | 0 | 0 |

## Offline triage caveat

Subsequent offline inspection found missing fixture context, missing event timestamps, wording-sensitive regex failures, and out-of-scope Copilot/VS Code telemetry cases. The statement above that VS Code-specific tests were excluded is incorrect: 33 named Copilot telemetry trials executed (9 passed, 24 failed). Original totals remain unchanged. See [offline triage](luna-medium-offline-triage.md) before interpreting this run as migration acceptance.
