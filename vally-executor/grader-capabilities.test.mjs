import test from 'node:test';
import assert from 'node:assert/strict';
import { auditGraderCapabilities } from './grader-capabilities.mjs';

const uxRouteProgram = `
const fs = require('node:fs');
try {
  const input = JSON.parse(fs.readFileSync(process.env.EVALUATE_GRADER_INPUT, 'utf8'));
  const output = String(input.trajectory?.output || '');
  if (/(\\bux[- ]coaching\\b|\\bmoment=problem-framing\\b|\\bproblem[- ]framing\\b)/i.test(output)) process.exit(0);
  // Routing is also proven when ux-coaching activates and its problem-framing reference is read.
  const events = Array.isArray(input.trajectory?.events) ? input.trajectory.events : [];
  let coaching = false;
  let reference = false;
  for (const event of events) {
    const name = String(event.data?.toolName || event.data?.name || '');
    const args = JSON.stringify(event.data?.arguments || event.data || {});
    if ((event.type === 'skill_activation' || /^skill$/i.test(name)) && /ux-coaching/i.test(args + name)) coaching = true;
    if (event.type === 'tool_call' && /^(?:view|read|read_file)$/i.test(name) && /problem-framing/i.test(args)) reference = true;
  }
  process.exit(coaching && reference ? 0 : 1);
} catch { process.exit(1); }
`;

test('flags the selected scanner shell-name requirements and preserves original regexes', () => {
  const spec = {
    stimuli: [
      { name: 'scan-clean', graders: [{ type: 'tool-calls', name: 'scan', config: {
        required: [{ name: '^powershell$', command: 'scan_sensitive_content\\.py', result: '"status":"completed"' }],
      } }] },
      { name: 'scan-failure', graders: [{ type: 'tool-calls', name: 'failure', config: {
        required: [{ name: '^powershell$', command: 'scan_sensitive_content\\.py', result: 'scanner-unavailable' }],
      } }] },
      { name: 'state-failure', graders: [{ type: 'tool-calls', name: 'writer', config: {
        required: [{ name: '^(bash|shell|powershell)$', command: 'WriteAllText', result: 'Synthetic failure' }],
      } }] },
    ],
  };
  const original = structuredClone(spec);

  const report = auditGraderCapabilities(spec, { evalName: 'agent-behavior' });

  assert.equal(report.status, 'known-unverified-predicates');
  assert.equal(report.acceptanceClaim, false);
  assert.equal(report.overallCompatibility, 'not-assessed');
  assert.equal(report.otherPredicatesAssessed, false);
  assert.equal(report.unverifiedPredicateCount, 3);
  assert.deepEqual(report.findings.map(item => item.originalRegex), [
    '^powershell$', '^powershell$', '^(bash|shell|powershell)$',
  ]);
  assert.deepEqual(spec, original, 'audit must not rewrite the upstream grader config');
});

test('flags the selected write-only shell disallow while retaining its full matcher', () => {
  const matcher = { name: '^(bash|powershell|run_in_terminal)$', write_only: true, pattern: 'metadata\\.json' };
  const report = auditGraderCapabilities({ stimuli: [{ name: 'native-edit', graders: [{
    type: 'tool-calls', name: 'edit-guard', config: { disallowed: [matcher] },
  }] }] });

  assert.equal(report.unverifiedPredicateCount, 1);
  assert.equal(report.findings[0].category, 'write-only-shell-disallow');
  assert.equal(report.findings[0].originalRegex, matcher.name);
  assert.deepEqual(report.findings[0].originalMatcher, matcher);
  assert.match(report.findings[0].reason, /exec_command/);
});

test('flags shell alternatives inside the selected broader failed-save name regex', () => {
  const regex = '^(?:powershell|bash|shell|create|edit|write|apply_patch|multiedit)$';
  const report = auditGraderCapabilities({ stimuli: [{ name: 'failed-save', graders: [{
    type: 'tool-calls', name: 'writer-error', config: { required: [{ name: regex, result: 'Synthetic failure' }] },
  }] }] });

  assert.equal(report.unverifiedPredicateCount, 1);
  assert.equal(report.findings[0].originalRegex, regex);
  assert.deepEqual(report.findings[0].originalMatcher, { name: regex, result: 'Synthetic failure' });
});

test('leaves native exec_command names and unrelated substring predicates alone', () => {
  const report = auditGraderCapabilities({ stimuli: [{ name: 'native-safe', graders: [
    { type: 'tool-calls', name: 'native-shell', config: { required: [{ name: '^exec_command$', command: 'check.py' }] } },
    { type: 'tool-calls', name: 'no-mural', config: { disallowed: [{ name: '(?i)mural' }] } },
  ] }] });

  assert.equal(report.status, 'no-targeted-unverified-predicate-found');
  assert.equal(report.unverifiedPredicateCount, 0);
  assert.equal(report.acceptanceClaim, false);
  assert.equal(report.overallCompatibility, 'not-assessed');
});

test('flags the selected UX skill-plus-reference-read event alternative and preserves its snippet', () => {
  const config = { program: 'node', args: ['--eval', uxRouteProgram] };
  const report = auditGraderCapabilities({ stimuli: [{ name: 'ux-ui-designer-problem-framing-route', graders: [{
    type: 'program', name: 'routes-to-problem-framing', config,
  }] }] });

  assert.equal(report.unverifiedPredicateCount, 1);
  assert.equal(report.findings[0].category, 'skill-activation-and-reference-read-alternative');
  assert.equal(report.findings[0].originalProgramSnippet, uxRouteProgram);
  assert.deepEqual(report.findings[0].originalConfig, config);
  assert.match(report.findings[0].reason, /output-based pass branch is preserved/);
});

test('does not flag programs without the exact skill-and-read conjunction', () => {
  const noCallOnly = `
    const events = input.trajectory.events;
    const forbidden = events.some(event => event.type === 'tool_call' && /mural/i.test(event.data?.toolName || ''));
    process.exit(forbidden ? 1 : 0);
  `;
  const skillOnly = `
    const events = input.trajectory.events;
    const loaded = events.some(event => event.type === 'skill_activation' || /^skill$/i.test(event.data?.toolName || ''));
    process.exit(loaded ? 0 : 1);
  `;
  const report = auditGraderCapabilities({ stimuli: [{ graders: [
    { type: 'program', name: 'mural-no-call', config: { args: ['--eval', noCallOnly] } },
    { type: 'program', name: 'skill-only', config: { args: ['--eval', skillOnly] } },
  ] }] });

  assert.equal(report.unverifiedPredicateCount, 0);
  assert.equal(report.acceptanceClaim, false);
});

test('rejects malformed spec structure instead of silently declaring no gaps', () => {
  assert.throws(() => auditGraderCapabilities(null), /requires a spec object/);
  assert.throws(() => auditGraderCapabilities({ stimuli: {} }), /stimuli must be an array/);
});


test('blocks anchored simple-read program predicates without changing the grader', () => {
  const spec = { stimuli: [{ name: 'fixture', graders: [{ type: 'program', name: 'scope', config: { program: 'node', args: ['--eval', "const trajectory = {}; const simpleRead = /^cat /; const command = 'mural';"] } }] }] };
  const before = structuredClone(spec);
  const audit = auditGraderCapabilities(spec);
  assert.equal(audit.findings[0].category, 'shell-wrapper-sensitive-program');
  assert.deepEqual(spec, before);
});
