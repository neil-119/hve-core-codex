import test from 'node:test';
import assert from 'node:assert/strict';
import { computeMetrics, gradeTrajectory } from '@microsoft/vally';
import { normalizeEvent } from './index.mjs';

// These are the original agent-behavior scanner matcher predicates. The adapter
// must satisfy them through observed shell semantics, not changed grader names.
const graders = [{ type: 'tool-calls', config: { required: [{
  name: '^powershell$', command: 'scan_sensitive_content\\.py',
  result: '"status":"completed"',
}] } }];

function trajectory(command, platform = 'win32') {
  const item = { type: 'command_execution', id: 'native-scanner-1', command };
  const events = [
    ...normalizeEvent({ type: 'item.started', item }, 0, 'gpt-6-luna', { platform }),
    ...normalizeEvent({ type: 'item.completed', item: { ...item,
      exit_code: 0, status: 'completed',
      aggregated_output: '{"schema_version":"scan-sensitive-content-v1","status":"completed","findings":[]}',
    } }, 0, 'gpt-6-luna', { platform }),
  ];
  return { id: 'shell-grader-contract', stimulus: { name: 'scanner' },
    events, output: '', workDir: process.cwd(), metadata: {}, metrics: computeMetrics(events) };
}

test('a proven native PowerShell wrapper satisfies the original Vally scanner matcher', async () => {
  const command = '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "python scan_sensitive_content.py"';
  const actual = trajectory(command);
  assert.equal(actual.events.length, 2);
  assert.equal(actual.metrics.toolCallCount, 1);
  assert.deepEqual(actual.events.map(event => event.data.toolCallId), ['native-scanner-1', 'native-scanner-1']);
  assert.equal(actual.events[0].data.arguments.command, 'python scan_sensitive_content.py');
  assert.equal((await gradeTrajectory(actual, graders)).passed, true);
});

test('Windows-looking text on a non-Windows backend does not establish PowerShell semantics', async () => {
  const command = '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "python scan_sensitive_content.py"';
  const actual = trajectory(command, 'linux');
  assert.equal(actual.events[0].data.toolName, 'exec_command');
  assert.equal((await gradeTrajectory(actual, graders)).passed, false);
});

test('an ambiguous or unwrapped native command cannot claim the original PowerShell match', async () => {
  for (const command of [
    'python scan_sensitive_content.py',
    '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command "python" "scan_sensitive_content.py"',
  ]) {
    const actual = trajectory(command);
    assert.equal(actual.events.length, 2);
    assert.equal(actual.metrics.toolCallCount, 1);
    assert.equal(actual.events[0].data.toolName, 'exec_command');
    assert.equal((await gradeTrajectory(actual, graders)).passed, false);
  }
});
