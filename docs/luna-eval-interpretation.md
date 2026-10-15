# Interpreting the Luna Vally results

We are retaining the original full-suite baseline: **1,310 passed, 927 grading failures, 439 blocked, zero execution errors, zero unrun** out of 2,676 trials. That is **58.6% of the 2,237 graded trials** (48.95% of all planned trials). Blocked trials are not passes. Later targeted retries do not change these totals.

The observed workflows and targeted checks support using the Codex port. The upstream tests do not translate perfectly across model response styles: some reject a correct explanation because it does not match a narrow wording pattern. We accept this limitation and are ending prompt tuning and paid retries for these cases. This is a practical acceptance decision, not a claim that every HVE capability has passed validation.

## Concrete examples

| Case | Observed response or behavior | Why the original check rejected it |
| --- | --- | --- |
| Saved-query retrieval: prohibition | “Text search is not a substitute for resolving and running the saved query.” | The regex recognizes particular prohibition constructions such as “do not”, rather than this equivalent statement. |
| Saved-query retrieval: execution | “Call `wit_get_query_results_by_id` once with that ID”, immediately after resolving the saved query's ID. | The regex requires “resolved”, “query ID” or “saved query” after the tool name within a bounded span; the pronoun does not match. |
| Python skill on a TypeScript task | “No. `python-foundational` is for Python work, and this module is TypeScript.” | The check expects phrases such as “does not”, “not apply”, “inapplicable”, “different skill” or “N/A”. The response expresses the scope decision using other words. |
| Accessibility startup disclaimer | Disclaimer emitted on turn 0, followed by Discovery questions on turn 1. | The examined check evaluates turn 1 and rejects the absent repetition. This is a turn-scoping mismatch rather than a wording synonym. |

These examples come from inspected trajectories in the linked reports. They explain specific failures; they do not establish that all 927 grading failures are false negatives. Earlier investigations also found genuine missing-context and discovery problems, and some adapter contracts remain unverified or blocked.

The user's Haiku/Luna response-style explanation is a plausible interpretation of these examples. We did not run a matched Haiku baseline or establish that Microsoft intentionally targets Haiku mannerisms. The demonstrated limitation is the mismatch between literal checks and the observed Luna responses.

## Preserved follow-ups

- [Original full-suite results](luna-medium-full-suite.md): authoritative baseline totals.
- [Targeted adapter fixes](luna-medium-targeted-followups.md): latest results across 22 selected trials were 13 passes and nine failures, kept separate from the baseline.
- [Navigation experiment](navigation-focused-validation.md): 0/5 passed, but all five found the procedure after navigation guidance.
- [Response-style v1](response-style-focused-validation.md): 1/5 passed.
- [Response-style v2](response-style-v2-focused-validation.md): 0/5 passed; all five passed the prohibition component and failed the execution wording component.

Upstream prompts, graders and recorded grades remain unchanged. Response-style v2 remains the current overlay, explicitly versioned; it has not been validated across the full suite. No further tuning or reruns are planned for these wording checks. Desktop validation remains manual, and deferred environment/runtime checks remain separate from migration acceptance.
