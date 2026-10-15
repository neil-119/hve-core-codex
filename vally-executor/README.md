# Codex executor for Vally

This package replaces Vally's agent execution backend. Vally still prepares fixtures, runs trials, applies its original graders, and produces results.

The implementation is in progress. Offline tests cover plugin loading, configuration validation, event metrics, two-turn execution through the real Vally pipeline, original static grading, failed-turn rejection, subprocess JSONL transport, recursive skill staging, partial failure logs, resume identity, bounded termination, and Vally planning. They use fake Codex responses and spend no model credits.

```powershell
cd vally-executor
npm ci --ignore-scripts
npm test
```

`node plan.mjs <converted-bundle>` discovers and validates converted specs through Vally's own planner without starting trials. A valid offline plan is not runtime acceptance; the report keeps `runtimeReady: false` while the live adapter work is incomplete.

Converted specs select `defaults.executor.name: codex`. Executor config accepts `binary`, `sandbox` (`read-only` or `workspace-write`), and `reasoningEffort` (default `max`). The default model is `gpt-6-luna`. This keeps Max in the Codex-specific configuration because Vally 0.17.0's generic reasoning-effort schema does not accept it.

Use `hve-codex eval vally-prepare`, `vally-plan`, and `vally-run` to convert, inspect, and execute the original upstream cases. Each selected case keeps its original fixtures, graders, scoring, and trial count. The runner saves Vally's JSONL, Markdown, and JUnit reports alongside a compact launcher report. A selected passing case does not establish a passing full suite.

`vally-run --codex-binary <path>` selects the official Codex executable to launch, such as a WSL installation. It does not select a different Vally engine or require a custom Codex build. Without this option, the adapter uses `codex` on PATH.

Use `vally-run --judge-backend codex` to run the original Vally LLM graders through the official Codex app-server with Luna Max. The judge receives bounded `view` and `grep` evidence tools and submits the original schema-validated verdict tool. Extra native tools may remain available; exact tool isolation is an accepted, unverified limitation recorded in reports. The backend defaults to `none`, so paid judges require explicit selection. No custom Codex build is used.

Before a trial starts, the executor uses native skill discovery to verify the staged skills and temporarily disable unrelated account skills. These overrides apply only to the owned Codex process. Offline adapter tests cover this isolation and native event normalization; live acceptance is tracked separately.

Pending: MCP configuration and simulation, attachments, and delegated timeout acceptance. The executor rejects known unsupported options; its existence does not prove upstream suite acceptance. Hooks retain the CLI's normal trust requirements.

Runs with a working-time limit use the app-server transport. `turn.mjs` correlates thread and turn identities, includes delayed start responses in the working budget, and waits for a terminal notification after interruption. `settlement.mjs` checks descendants and thread-owned background commands before Vally grades the workspace. The simplest native interruption check passes; delegated cleanup remains unverified. Runs without a working limit currently use `codex exec`.

Vally's LLM graders use a separate client from the agent executor. With `--judge-backend codex`, both use Luna Max; reports distinguish requested Vally effort from the effective native effort and preserve the tool-isolation warning. Without a selected or injected judge, LLM-grader cases stop in preflight.

See [Vally's executor interface](https://microsoft.github.io/vally/guides/writing-custom-executors/).
