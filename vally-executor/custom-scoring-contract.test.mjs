import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parse } from 'yaml';
import { createDefaultGraderRegistry, gradeTrajectory, resolveGradePass } from '@microsoft/vally';

test('custom Vally scoring cannot average away a failed prompt or critical fixture check', async () => {
  const spec = parse(await readFile(new URL('../tests/vally-custom/edge/evals/codex-port-edge-edge-path-09/eval.yaml', import.meta.url), 'utf8'));
  assert.equal(spec.scoring, undefined);
  const stimulus = spec.stimuli[0];
  const workDir = await mkdtemp(join(tmpdir(), 'hve-vally-custom-scoring-'));
  let promptPassed = false;
  const registry = createDefaultGraderRegistry();
  registry.register({ metadata: { name: 'prompt', behavior: {}, costProfile: 'free', determinism: 'static' },
    async grade(input) {
      assert.equal(input.config.threshold, 0.7);
      return { name: 'prompt', kind: 'code', passed: promptPassed, score: promptPassed ? 0.8 : 0,
        evidence: 'Synthetic judge result; no model call' };
    },
  });
  const trajectory = { id: 'synthetic-scoring', stimulus, workDir, diff: '', events: [], output: '', metadata: {} };
  try {
    await writeFile(join(workDir, 'request.md'), 'Synthetic fixture request');
    const rejectedPrompt = await gradeTrajectory(trajectory, stimulus.graders, { registry });
    assert.equal(rejectedPrompt.score, 0.75);
    assert.equal(resolveGradePass(rejectedPrompt, 0.7), true, 'Prior aggregate threshold hid the failure');
    assert.equal(resolveGradePass(rejectedPrompt, spec.scoring?.threshold), false);
    promptPassed = true;
    const accepted = await gradeTrajectory(trajectory, stimulus.graders, { registry });
    assert.equal(resolveGradePass(accepted, spec.scoring?.threshold), true);
    await rm(join(workDir, 'request.md'));
    const rejectedFixture = await gradeTrajectory(trajectory, stimulus.graders, { registry });
    assert.ok(rejectedFixture.score >= 0.7);
    assert.equal(resolveGradePass(rejectedFixture, spec.scoring?.threshold), false);
  } finally { await rm(workDir, { recursive: true, force: true }); }
});
