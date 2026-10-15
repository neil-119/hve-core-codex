# How HVE Core works in Codex

This is an unofficial port of [Microsoft HVE Core](https://github.com/microsoft/hve-core). Microsoft supplies the workflows and guidance. This repository translates and packages them for Codex.

## What happens to the original files?

Our converter reads a pinned Microsoft release or commit. It inventories the files, copies the originals, and creates Codex versions. The same source and settings produce the same output. A map records where each source file went and what needed adapting. Our Git repository contains the builder and tests; upstream files and converted documents are fetched and generated only during a build, outside this repository. Actions uses the same Bash build script and distributes the resulting archive.

- **Skills** become reusable procedures that Codex can discover or you can call by name.
- **Agents** get callable entrypoints and native profiles for delegated work.
- **Prompts** become callable skills.
- **File-specific instructions** become a registry. Hooks match file paths and give Codex the relevant guidance.
- **References, scripts, templates, and schemas** travel with the instructions that need them.

The installer can share these pieces across all your repositories with `--global`, or give one project its own copy with `--target`. It tracks the files it owns. Your plans and workflow outputs still stay in the project you are working on. You review the installation and trust its hooks through Codex's normal controls. The port does not grant permissions or approve work for you.

## What happens when I use it?

For a task such as “add CSV import,” you can start the RPI workflow: research, plan, implement. Its agents use the same HVE procedures, produce tracking documents, and preserve the human review gates. You can also invoke an individual stage or skill.

Codex supplies the model, tools, subagents, and sandbox. HVE supplies the instructions for doing the work. Our files connect those instructions to Codex's formats and tool names.

## What do we do differently?

Copilot and Codex use different agent profiles, tool names, discovery rules, and hook interfaces. We translate those connections and provide a managed installation overlay alongside a portable plugin.

Stable follows Microsoft's latest published release. Edge follows a pinned commit on `main`. The weekly GitHub workflow checks the latest stable and latest edge, and only proposes or publishes changed versions when the configured review and release gates permit it. GitHub publication still needs repository setup and authorization.

We keep Microsoft's **Vally** test engine, original tests, graders, and scoring. Our backend runs tasks and judges through the official Codex CLI and normalizes its saved events into Vally's format. `--codex-binary` chooses which installed Codex executable to use; it does not select a custom Codex fork. Custom Codex checks cover things such as installation, skill loading, hooks, and native role loading.

Tests use disposable practice projects. A review test needs real local Git history; a coding test needs a working test runner. We prepare those before asking Codex to work. This test setup does not modify your project's history or install a different Codex.

## Why was this more than copying files?

Instructions can refer to other instructions, scripts, agents, and state files. Those links must still work after moving the files. Tests also need the right dependencies and native profiles, without accidentally treating copied reference tests as new tests or overwriting project files.

Utilities also need to know which folder is your project and which folder contains their installed code. We add Codex guidance for those paths while retaining the original scripts and safety checks.

Some host features have no proven equivalent. Tool restrictions can remain advisory, skills hidden from manual invocation in Copilot may be visible in Codex, and judges may retain extra native tools. We preserve the guidance and warn about these differences.

Passing conversion tests proves that the translation and package are consistent. It does not prove every workflow succeeds with every model. Live failures, unverified child permissions, external-service requirements, and Codex desktop checks remain visible in [validation](docs/validation.md) and [compatibility status](docs/vally-port-status.md). VS Code-specific testing is outside this project's acceptance scope.
