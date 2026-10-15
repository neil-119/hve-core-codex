import test from 'node:test';
import assert from 'node:assert/strict';
import { computeMetrics, gradeTrajectory } from '@microsoft/vally';
import { appServerEvent } from './app-server-events.mjs';
import { normalizeEvent } from './index.mjs';

// Codex ThreadItemsListResponse FileChangeThreadItem uses changes with
// { path, kind, diff }. Vally's structured writer matcher extracts nested
// `path` fields for apply_patch write_only predicates.
const grader = [{ type: 'tool-calls', config: {
  required: [{ name: '^apply_patch$', write_only: true, pattern: '^src/app\\.py$' }],
  disallowed: [{ name: '^apply_patch$', write_only: true, pattern: '^docs/protected\\.md$' }],
} }];

function trajectoryForFileChange(path, diffPath) {
  const item = {
    id: 'native-file-change-1',
    type: 'fileChange',
    status: 'completed',
    changes: [{
      path,
      kind: { type: 'update' },
      diff: `--- a/${diffPath}\n+++ b/${diffPath}`,
    }],
  };
  const events = [
    ...normalizeEvent(appServerEvent({ method: 'item/started', params: { item } }), 0, 'gpt-6-luna'),
    ...normalizeEvent(appServerEvent({ method: 'item/completed', params: { item } }), 0, 'gpt-6-luna'),
  ];
  return {
    id: 'native-file-change-path-target',
    stimulus: { name: 'file-change-path-target' },
    events,
    output: '',
    workDir: process.cwd(),
    metadata: {},
    metrics: computeMetrics(events),
  };
}

test('native fileChange paths reach Vally write_only predicates without reading diff text as a target', async () => {
  const allowed = trajectoryForFileChange('src/app.py', 'docs/protected.md');
  assert.equal(allowed.events[0].data.toolName, 'apply_patch');
  assert.deepEqual(allowed.events[0].data.arguments.changes[0], {
    path: 'src/app.py',
    kind: { type: 'update' },
    diff: '--- a/docs/protected.md\n+++ b/docs/protected.md',
  });
  assert.equal((await gradeTrajectory(allowed, grader)).passed, true,
    'the structured path is allowed even when patch text mentions the protected path');

  const protectedPath = trajectoryForFileChange('docs/protected.md', 'src/app.py');
  assert.equal((await gradeTrajectory(protectedPath, grader)).passed, false,
    'the native change target is caught even when the diff mentions an allowed path');

  const unrelated = trajectoryForFileChange('docs/readme.md', 'src/app.py');
  assert.equal((await gradeTrajectory(unrelated, grader)).passed, false,
    'an unrelated path does not satisfy the required write target');
});
