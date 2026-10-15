# Focused response-style validation

Case: `agent-behavior / engagement-report-generator-ado-saved-query`. Model: GPT-6 Luna medium, official WSL Codex CLI 0.162.1. Five original trials; unchanged Microsoft prompt, graders and deadlines. Upstream pin: `49dfb73866f8c5f81bf734d6ca808dac25a2a330`.

Enabled navigation guidance and shared response-style version `1` for the task model. The separate judge receives neither overlay. This result is distinct from the pre-style baseline.

| Configuration | Passed | Failed | Execution errors |
| --- | ---: | ---: | ---: |
| Navigation only | 0 | 5 | 0 |
| Navigation plus response style v1 | 1 | 4 | 0 |

All five trajectories record response-style version 1 and fixture-instruction delivery. All five responses describe resolving the saved query, executing it and hydrating returned work-item IDs, with a prohibition on substituting content/text search. None claim relevant guidance is missing. No grader-input errors occurred.

Four trials still fail the original regex graders: three fail `executes-resolved-query`, three fail `no-content-search-substitution`, and two fail both. For example, “The procedure explicitly prohibits substituting content search for a configured saved query” is rejected by the prohibition regex. These remain official failures. A 1/5 result in this small sample does not establish a reliable improvement from the style overlay.

Raw evidence: `/home/neilrao/.local/share/hve-core-codex/vally-response-style-focused-20261014/00-live/`.

Machine summary: `C:/repos/hve-core-codex-artifacts/luna-medium-20261014/response-style-focused-results.json`.

Offline style-wiring checks passed all 212 Node tests before this run. No full-suite rerun or extra paid retries were performed.
