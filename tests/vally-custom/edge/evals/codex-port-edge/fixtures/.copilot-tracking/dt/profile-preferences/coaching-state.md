<!-- markdownlint-disable-file -->
# Synthetic evaluation fixture only

project:
  name: "Synthetic Profile Preferences"
  slug: "profile-preferences"
  created: "2026-10-10"
  initial_request: "[SYNTHETIC EVAL FIXTURE] Add a profile-preferences page so employees can change notification cadence and display settings."
  initial_classification: "fluid"

current:
  method: 3
  space: "problem"
  phase: "Problem Space exit readiness"
  disclaimerShownAt: "2026-10-10T12:00:00Z"

methods_completed:
  - 1
  - 2
  - 3

transition_log:
  - from_method: null
    to_method: 1
    rationale: "[SYNTHETIC EVAL FIXTURE] Project initialized."
    date: "2026-10-10"
  - from_method: 1
    to_method: 2
    rationale: "[SYNTHETIC EVAL FIXTURE] Scope and stakeholders were recorded."
    date: "2026-10-10"
  - from_method: 2
    to_method: 3
    rationale: "[SYNTHETIC EVAL FIXTURE] Scripted research inputs were synthesized."
    date: "2026-10-10"

hint_calibration:
  level: 1
  pattern_notes: ""

session_log:
  - date: "2026-10-10"
    method: 3
    summary: "[SYNTHETIC EVAL FIXTURE] Methods 1-3 are complete; no lateral handoff choice has been recorded before the current user request."

artifacts:
  - path: ".copilot-tracking/dt/profile-preferences/method-01-stakeholder-map.md"
    method: 1
    type: "stakeholder-map"
  - path: ".copilot-tracking/dt/profile-preferences/method-01-assumptions-and-constraints.md"
    method: 1
    type: "assumptions-log"
  - path: ".copilot-tracking/dt/profile-preferences/method-02-research-plan.md"
    method: 2
    type: "research-plan"
  - path: ".copilot-tracking/dt/profile-preferences/method-02-research-notes.md"
    method: 2
    type: "interview-notes"
  - path: ".copilot-tracking/dt/profile-preferences/method-03-synthesis-themes.md"
    method: 3
    type: "synthesis-themes"
  - path: ".copilot-tracking/dt/profile-preferences/method-03-problem-definition.md"
    method: 3
    type: "problem-definition"
