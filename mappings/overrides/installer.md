# HVE Core Codex installer workflow

This is the Codex equivalent of the upstream HVE installer. Preserve the Installer/Validator personas, seven phases, initial consent, explicit target selection, configuration review, safe reruns, and optional customization. Package resources retain the original supporting scripts for provenance; the Codex installation path uses the audited `hve-codex` CLI.

## Phase 1: Environment Detection

Confirm the user's intent to install and the selected destination. If the conversation already explicitly authorizes installation into a named target, retain that authorization; otherwise present the concrete action and wait for consent. Inspect OS, Python 3.11+, `hve-codex --version`, and `codex --version`. Report missing prerequisites and installation instructions; do not install dependencies silently.

Inspect existing HVE registrations and managed state with `hve-codex doctor` for the selected scope and target. Keep one managed installation route per scope. When an installation already exists, offer an update or an explicitly selected replacement before installing another copy. Never stack registrations to repair missing discovery. Copilot team-default settings and native component copies are host-specific; use Codex managed project/user scope instead.

## Phase 2: Installation Path Selection

Offer the portable plugin plus managed overlay as the full Codex installation, or standalone skills plus the same overlay when plugins are unsupported. Explain that plugin-only installation omits custom agents and scoped rules. The upstream VSIX quick-install path is host-specific and is replaced by Codex marketplace installation; do not install a Copilot extension as a substitute.

## Phase 3: Environment Detection and Decision Matrix

Ask for project or user scope if unresolved. A project installation requires an explicitly selected repository. A user installation uses the selected personal discovery root and Codex home. Choose `--no-plugin` for hosts without plugin support and `--no-hooks` when hooks are unsupported or declined. Explain the resulting limitations. Retain the user's shell preference where relevant.

## Phase 4: Installation Methods

Run `hve-codex install --scope project --target <selected-repo> --mode full --dry-run`, or the equivalent user-scope preview. Add the selected fallback flags. Present the planned changes, conflicts and backups. Preserve any source-required consent checkpoint before applying the reviewed installation with the same arguments minus `--dry-run`. Stop on conflicts rather than overwriting custom files.

## Phase 5: Validation

Switch to the Validator persona. Run `hve-codex doctor` with the selected scope and target. For plugins, distinguish marketplace registration, `codex plugin add <plugin@marketplace>`, enabled state and actual skill discovery. Verify representative entry points in the client only when the user authorizes the runtime test. Do not infer behavior parity from structural validation. Never bypass project or hook trust.

## Phase 6: Post-Installation Setup

Present `$hve-catalog`, `hve-codex rules match <paths...>`, update and uninstall commands. Explain hooks as advisory and offer the explicit-rule fallback. Present connector names, authentication prerequisites and the original read/write scopes. Installing this plugin does not provision cloud services. Offer gitignore adjustments only when needed and authorized; preserve tracking artifacts and user policy.

For earlier registrations or location settings, show each proposed removal and obtain explicit consent for that file before cleanup. Leave unrelated entries intact. Confirm that the selected Codex route discovers representative skills and agents before removing an older route, then repeat discovery after cleanup in a new client session. If discovery is unverified or consent is declined, retain the old entries and report them. Use managed update/uninstall previews for owned files; never delete a clone, settings file or tracking directory as an automatic recovery step.

## Phase 7: Agent Customization

Offer optional local agent/rule customization. The managed installer currently supports the complete distribution only; upstream selective component installation has no automatic Codex equivalent. If the user needs only selected components, explain that limitation and agree on a separate, explicit customization scope rather than claiming a partial managed install. Explain owned-file update conflicts and backups; do not edit managed content without the user's requested change. Document customized roles and limitations. On rerun use doctor/update previews rather than starting an unrelated installation. Report the selected scope, installed components, performed validation and all remaining blockers.
