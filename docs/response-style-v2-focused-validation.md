# Focused response-style v2 validation

Test: `agent-behavior / engagement-report-generator-ado-saved-query`. GPT-6 Luna medium via official WSL Codex CLI 0.162.1. Five unchanged trials, Microsoft prompt and graders; upstream pin `49dfb73866f8c5f81bf734d6ca808dac25a2a330`.

## Change

Response-style v2 adds task-independent wording examples: direct prohibitions, explicit applicability, named procedure inputs, required versus optional actions, and evidence boundaries. Examples use unrelated tasks. Navigation remains enabled. The task receives the overlay through developer instructions; the separate judge does not. All five trajectories record version 2. The runtime provenance binds the context file bytes.

## Results

| Overlay | Passed | Failed | Execution errors |
| --- | ---: | ---: | ---: |
| Navigation only | 0 | 5 | 0 |
| Navigation + style v1 | 1 | 4 | 0 |
| Navigation + style v2 | 0 | 5 | 0 |

All five v2 trials passed three of four component checks, including the prohibition check. All five failed only `executes-resolved-query`. Each response correctly describes resolving the saved query, executing it and hydrating work-item IDs, but says “Call wit_get_query_results_by_id once with that ID.” The regex requires “resolved”, “query ID” or “saved query” after the tool name within its bounded span. An earlier explicit reference to the query ID does not satisfy that ordering requirement.

This is not an overall improvement in pass rate: v2 scored 0/5 versus v1's 1/5. The prohibition wording became consistent in this sample, while pronoun use still fails the execution wording check. All original grades are preserved. These repeated targeted runs are prompt-development evidence, not an independent generalization benchmark.

No runner or grader-input errors. Offline regression: 212 Node tests passed. No full-suite rerun or additional paid retries.

## Evidence

Raw results: `/home/neilrao/.local/share/hve-core-codex/vally-response-style-v2-focused-20261014/00-live/`.

Machine summary: `C:/repos/hve-core-codex-artifacts/luna-medium-20261014/response-style-v2-focused-results.json`.
