# Focused navigation-guidance validation

Tested one previously failing case, `agent-behavior / engagement-report-generator-ado-saved-query`, using GPT-6 Luna medium through official WSL Codex CLI 0.162.1. Retained its five declared trials, original prompt, graders and deadlines. Upstream pin: `49dfb73866f8c5f81bf734d6ca808dac25a2a330`.

## Change

Added generic instructions to inspect hidden HVE directories and read relevant procedures before claiming guidance is missing. The shared text is in `vally-executor/navigation.json` and is included in generated integration instructions. This experiment explicitly enabled `--navigation-context` for the task model; the judge did not receive it. No answer examples, expected phrases or case-specific hints were added. Ordinary evaluation runs keep this option off.

## Results

| Run | Passed | Failed | Execution errors |
| --- | ---: | ---: | ---: |
| Previous focused baseline | 0 | 5 | 0 |
| With navigation guidance | 0 | 5 | 0 |

All five new trials read the research procedure and described resolving the query path, executing the saved query and hydrating returned work-item IDs. Previously, two of five responses claimed relevant guidance was missing. This small sample suggests improved discovery; it does not establish a general success rate or a passing test.

The original wording graders still reject every response: four fail the content-search substitution pattern, three fail the resolved-query execution pattern, and two fail both. For example, “Text search is not a substitute for resolving and running the saved query” fails the original prohibition regex. Grades remain unchanged; semantic review is recorded separately.

All five trajectories record navigation context and fixture-instruction delivery. There were no grader-input errors. Final source integrity reported unchanged, with no changed paths or integrity errors.

Offline validation: 390 Python tests passed, 6 skipped, 211 subtests; 211 Node tests passed. Ruff passed for changed Python files.

## Evidence

- Raw results and final report: `/home/neilrao/.local/share/hve-core-codex/vally-navigation-focused-20261014/00-live/`
- Machine summary: `C:/repos/hve-core-codex-artifacts/luna-medium-20261014/navigation-focused-results.json`
- Previous baseline: `/home/neilrao/.local/share/hve-core-codex/vally-followups-context-v3-budget-20261014/00-live/results.jsonl`

No full-suite rerun or additional paid retries were performed.
