import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { buildBundlePlan } from './plan.mjs';

async function makeBundle(t, { declaredSpecs = [] } = {}) {
  const root = await mkdtemp(join(process.cwd(), '.tmp-vally-plan-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bundle = join(root, 'bundle');
  await mkdir(join(bundle, 'evals'), { recursive: true });
  await mkdir(join(bundle, 'generated'), { recursive: true });
  await writeFile(join(bundle, '.vally.yaml'), 'paths:\n  evals: [evals]\n');

  const outputs = Object.fromEntries(declaredSpecs.map(identity => [identity, {
    outputs: [{ path: identity, status: 'converted-eval-spec' }],
  }]));
  await writeFile(join(bundle, 'generated', 'vally-source-to-output.json'), JSON.stringify(outputs));
  await writeFile(join(bundle, 'generated', 'vally-bundle-report.json'), JSON.stringify({
    status: 'complete',
    success: true,
    source_to_output: 'generated/vally-source-to-output.json',
    summary: { eval_specs_converted: declaredSpecs.length },
  }));
  return bundle;
}

async function writeEval(bundle, identity, { name, runs = 1 } = {}) {
  const path = join(bundle, ...identity.split('/'));
  await mkdir(join(path, '..'), { recursive: true });
  await writeFile(path, [
    `name: ${name}`,
    'type: regression',
    'defaults:',
    '  executor: codex',
    `  runs: ${runs}`,
    'stimuli:',
    '  - name: smoke',
    '    prompt: Check the converted fixture.',
    '    graders:',
    '      - type: output-matches',
    '        config:',
    '          pattern: pass',
    '',
  ].join('\n'));
}

test('plans only declared converted evals and ignores malformed injected resource evals', async t => {
  const identity = 'evals/main/eval.yaml';
  const bundle = await makeBundle(t, { declaredSpecs: [identity] });
  await writeEval(bundle, identity, { name: 'main', runs: 2 });
  await writeEval(bundle,
    'evals/main/plugins/hve-core-codex/resources/evals/support/eval.yaml',
    { name: 'injected-support', runs: 500 });
  const malformedSupport = join(bundle,
    'evals/main/plugins/hve-core-codex/resources/evals/broken/eval.yaml');
  await mkdir(join(malformedSupport, '..'), { recursive: true });
  await writeFile(malformedSupport, 'name: [malformed\n');

  const { discovered, plan } = await buildBundlePlan(bundle);

  assert.equal(discovered.scannedCount, 3, 'Vally still performs its official recursive discovery');
  assert.equal(discovered.evals.length, 1);
  assert.equal(discovered.evals[0].spec.name, 'main');
  assert.deepEqual(discovered.errors, [], 'unlisted resource files cannot poison the converted suite');
  assert.equal(plan.totalItems, 2, 'the declared eval retains its original configured trial count');
});

test('a declared converted eval that Vally cannot discover is reported as an error', async t => {
  const existing = 'evals/main/eval.yaml';
  const missing = 'evals/missing/eval.yaml';
  const bundle = await makeBundle(t, { declaredSpecs: [existing, missing] });
  await writeEval(bundle, existing, { name: 'main', runs: 2 });

  const { discovered, plan } = await buildBundlePlan(bundle);

  assert.equal(discovered.evals.length, 1);
  assert.equal(plan.totalItems, 2);
  assert.ok(discovered.errors.some(error =>
    error.path === missing && error.reason.includes('was not discovered')));
});

test('a malformed declared converted eval keeps Vally parse errors visible', async t => {
  const valid = 'evals/main/eval.yaml';
  const malformed = 'evals/broken/eval.yaml';
  const bundle = await makeBundle(t, { declaredSpecs: [valid, malformed] });
  await writeEval(bundle, valid, { name: 'main', runs: 2 });
  const malformedPath = join(bundle, ...malformed.split('/'));
  await mkdir(join(malformedPath, '..'), { recursive: true });
  await writeFile(malformedPath, 'name: [malformed\n');

  const { discovered, plan } = await buildBundlePlan(bundle);

  assert.equal(discovered.evals.length, 1);
  assert.equal(plan.totalItems, 2);
  assert.ok(discovered.errors.some(error => error.path === malformedPath));
  assert.ok(discovered.errors.some(error =>
    error.path === malformed && error.reason.includes('was not discovered')));
});

test('scope filter excludes Copilot telemetry before planning and reports exact exclusions', async t => {
  const identity = 'evals/main/eval.yaml';
  const bundle = await makeBundle(t, { declaredSpecs: [identity] });
  const path = join(bundle, identity);
  await mkdir(join(path, '..'), { recursive: true });
  const grader = [{ type: 'output-matches', config: { pattern: 'pass' } }];
  await writeFile(path, JSON.stringify({ name: 'scope', defaults: { executor: 'codex', runs: 3 }, stimuli: [
    { name: 'skill-copilot-otel-metrics-synthetic', prompt: 'Excluded', graders: grader },
    { name: 'codex-case', prompt: 'Included', graders: grader },
  ] }));
  const planned = await buildBundlePlan(bundle);
  assert.equal(planned.plan.totalItems, 3);
  assert.equal(planned.excludedStimuli.length, 1);
  assert.ok(planned.plan.items.every(item => item.stimulus.name === 'codex-case'));
  await assert.rejects(buildBundlePlan(bundle, { stimulusNames: ['skill-copilot-otel-metrics-synthetic'] }), /out-of-scope/);
});
