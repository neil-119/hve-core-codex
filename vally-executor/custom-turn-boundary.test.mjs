import { VALLY_AMBIENT_CONFIG_ARGS } from './skill-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { appendFile, copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse, stringify } from 'yaml';
import { CodexExecutor } from './index.mjs';
import { runBundle } from './run.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const CUSTOM_EVAL = resolve(HERE, '../tests/vally-custom/stable/evals/codex-port-stable-golden-01/eval.yaml');

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

async function createBundle(root) {
  const bundle = join(root, 'bundle');
  const evalDir = join(bundle, 'evals', 'protected-source');
  const fixtureDir = join(evalDir, 'fixtures');
  const sourceFixtureDir = resolve(dirname(CUSTOM_EVAL), 'fixtures');
  await mkdir(join(bundle, 'generated'), { recursive: true });
  await mkdir(join(fixtureDir, 'src'), { recursive: true });
  await mkdir(join(fixtureDir, 'tests'), { recursive: true });
  await writeFile(join(bundle, '.vally.yaml'), 'paths:\n  evals: [evals]\n');

  const originalBytes = await readFile(CUSTOM_EVAL);
  const original = parse(originalBytes.toString('utf8'));
  const originalStimulus = original.stimuli[0];
  const diffGraders = originalStimulus.graders.filter(grader =>
    grader.type === 'diff-not-contains' || grader.type === 'diff-contains');
  assert.equal(diffGraders.filter(grader => grader.type === 'diff-not-contains').length, 6);
  assert.equal(diffGraders.filter(grader => grader.type === 'diff-contains').length, 2);
  assert.ok(diffGraders.some(grader => grader.type === 'diff-not-contains' && grader.turn === 1));
  assert.ok(diffGraders.some(grader => grader.type === 'diff-contains' && grader.config.pattern.includes('src/importer')));

  const stimulus = {
    name: 'protected-source-turn-boundaries',
    turns: structuredClone(originalStimulus.turns),
    graders: structuredClone(diffGraders),
    agent_environment: {
      files: [
        { src: 'fixtures/src/importer.py', dest: 'src/importer.py' },
        { src: 'fixtures/tests/test_importer.py', dest: 'tests/test_importer.py' },
      ],
    },
  };
  const spec = {
    name: 'protected-source-turn-boundary-test',
    type: 'regression',
    defaults: { executor: 'codex', runs: 1, timeout: '60s' },
    stimuli: [stimulus],
  };
  const specBytes = Buffer.from(stringify(spec));
  const evalPath = join(evalDir, 'eval.yaml');
  await writeFile(evalPath, specBytes);
  await copyFile(join(sourceFixtureDir, 'src', 'importer.py'), join(fixtureDir, 'src', 'importer.py'));
  await copyFile(join(sourceFixtureDir, 'tests', 'test_importer.py'), join(fixtureDir, 'tests', 'test_importer.py'));

  const identity = 'evals/protected-source/eval.yaml';
  const archivePath = join(bundle, 'upstream', ...identity.split('/'));
  await mkdir(dirname(archivePath), { recursive: true });
  await writeFile(archivePath, originalBytes);
  const manifest = Buffer.from('{"fixture":"turn-boundary-manifest"}\n');
  const inventory = Buffer.from('{"fixture":"turn-boundary-inventory"}\n');
  const sourceMap = Buffer.from(JSON.stringify({
    [identity]: {
      outputs: [
        { path: `upstream/${identity}`, status: 'preserved-original', sha256: sha256(originalBytes) },
        { path: identity, status: 'converted-eval-spec', sha256: sha256(specBytes) },
      ],
    },
  }, null, 2) + '\n');
  await writeFile(join(bundle, 'generated', 'source-to-output.json'), manifest);
  await writeFile(join(bundle, 'generated', 'source-inventory.json'), inventory);
  await writeFile(join(bundle, 'generated', 'vally-source-to-output.json'), sourceMap);
  await writeFile(join(bundle, 'generated', 'vally-bundle-report.json'), JSON.stringify({
    schema_version: 1,
    status: 'complete',
    success: true,
    source: {
      distribution_root_name: 'turn-boundary-fixture',
      upstream_root_name: 'stable-custom-fixture',
      manifest: 'generated/source-to-output.json',
      manifest_sha256: sha256(manifest),
      source_inventory: {
        path: 'generated/source-inventory.json',
        sha256: sha256(inventory),
        channel: 'stable',
        upstream_ref: 'fixture-ref',
        upstream_sha: '0123456789abcdef0123456789abcdef01234567',
        upstream_url: 'https://example.invalid/stable.git',
      },
      source_to_output: 'generated/vally-source-to-output.json',
    },
    source_to_output: 'generated/vally-source-to-output.json',
  }, null, 2) + '\n');
  return { bundle, stimulus, fixtureDir };
}

class TurnBoundaryCodex extends CodexExecutor {
  turn = 0;

  constructor(mutationTurn) {
    super({ skillIsolationResolver: async () => ({
      mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS],
      expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
    }) });
    this.mutationTurn = mutationTurn;
  }

  async run(_binary, _args, _prompt, options) {
    const turn = this.turn++;
    if (turn === this.mutationTurn) {
      await appendFile(join(options.workDir, 'src', 'importer.py'), '\n# synthetic authorized retry helper change\n');
      await appendFile(join(options.workDir, 'tests', 'test_importer.py'), '\n# synthetic authorized retry helper test change\n');
    }
    const records = [
      { type: 'thread.started', thread_id: 'synthetic-protected-source-thread' },
      { type: 'turn.started' },
      { type: 'item.completed', item: { type: 'agent_message', text: `completed synthetic turn ${turn}` } },
      { type: 'turn.completed', usage: { input_tokens: 1, output_tokens: 1 } },
    ];
    for (const record of records) options.onRawEvent?.(record);
    if (options.sessionLog?.rootDir) {
      await appendFile(join(options.sessionLog.rootDir, 'events.jsonl'), records.map(record => JSON.stringify(record)).join('\n') + '\n');
    }
    return records;
  }
}

async function runScenario({ root, bundle, name, mutationTurn }) {
  const output = join(root, name);
  const executor = new TurnBoundaryCodex(mutationTurn);
  const report = await runBundle(bundle, { output, evalNames: ['protected-source-turn-boundary-test'] }, { executor });
  assert.equal(executor.turn, 5, 'fake executor should traverse every configured legacy RPI turn');
  const records = (await readFile(join(output, 'results.jsonl'), 'utf8'))
    .trim().split('\n').map(line => JSON.parse(line));
  const trial = records.find(record => record.type === 'trial-result');
  assert.ok(trial, 'Vally should serialize a trial result');
  return { report, trial, records };
}

function graderResults(trial) {
  return new Map(trial.gradeResult.details.map(result => [result.name, result]));
}

test('Vally turn-scoped diff graders catch a manual-boundary source write and allow the authorized later write', async () => {
  const root = await mkdtemp(join(HERE, '.tmp-custom-turn-boundary-'));
  try {
    const { bundle, stimulus } = await createBundle(root);
    const early = await runScenario({ root, bundle, name: 'early-write-output', mutationTurn: 1 });
    assert.equal(early.report.status, 'completed', JSON.stringify(early.report, null, 2));
    assert.equal(early.report.summary.passed, false, 'the manual-turn source write must fail static diff grading');
    assert.equal(early.trial.status, 'success', 'Vally execution completes while the grader records the contract violation');
    assert.equal(early.trial.gradeResult.passed, false);
    const earlyGrades = graderResults(early.trial);
    assert.equal(earlyGrades.get('importer-unchanged-after-manual-work').passed, false);
    assert.equal(earlyGrades.get('tests-unchanged-after-manual-work').passed, false);
    assert.equal(earlyGrades.get('importer-unchanged-at-launch-boundary').passed, true);
    assert.equal(earlyGrades.get('tests-unchanged-at-launch-boundary').passed, true);
    assert.equal(earlyGrades.get('importer-unchanged-after-manual-resume').passed, true);
    assert.equal(earlyGrades.get('tests-unchanged-after-manual-resume').passed, true);
    assert.equal(earlyGrades.get('authorized-importer-change-at-final-boundary').passed, true);
    assert.equal(earlyGrades.get('authorized-test-change-at-final-boundary').passed, true);

    const earlyDiffs = new Map(early.trial.trajectory.turnDiffs.map(record => [record.turn, record]));
    assert.deepEqual([...earlyDiffs.keys()].sort((a, b) => a - b), [0, 1, 2, 3, 4]);
    assert.equal(earlyDiffs.get(0).diff.includes('diff --git a/src/importer.py b/src/importer.py'), false);
    assert.equal(earlyDiffs.get(1).diff.includes('diff --git a/src/importer.py b/src/importer.py'), true);
    assert.equal(earlyDiffs.get(1).diff.includes('diff --git a/tests/test_importer.py b/tests/test_importer.py'), true);
    assert.equal(earlyDiffs.get(2).diff.includes('diff --git a/src/importer.py b/src/importer.py'), false,
      'turn diffs are incremental and refresh their baseline after each boundary');

    const authorized = await runScenario({ root, bundle, name: 'authorized-write-output', mutationTurn: 3 });
    assert.equal(authorized.report.status, 'completed', JSON.stringify(authorized.report, null, 2));
    assert.equal(authorized.report.summary.passed, true, 'changes after the explicit FULL-AUTO prompt should pass');
    assert.equal(authorized.trial.gradeResult.passed, true);
    const authorizedGrades = graderResults(authorized.trial);
    for (const name of [
      'importer-unchanged-at-launch-boundary', 'tests-unchanged-at-launch-boundary',
      'importer-unchanged-after-manual-work', 'tests-unchanged-after-manual-work',
      'importer-unchanged-after-manual-resume', 'tests-unchanged-after-manual-resume',
      'authorized-importer-change-at-final-boundary', 'authorized-test-change-at-final-boundary',
    ]) assert.equal(authorizedGrades.get(name).passed, true, name);
    const authorizedDiffs = new Map(authorized.trial.trajectory.turnDiffs.map(record => [record.turn, record]));
    for (const turn of [0, 1, 2]) {
      assert.equal(authorizedDiffs.get(turn).diff.includes('diff --git a/src/importer.py b/src/importer.py'), false);
      assert.equal(authorizedDiffs.get(turn).diff.includes('diff --git a/tests/test_importer.py b/tests/test_importer.py'), false);
    }
    assert.equal(authorizedDiffs.get(3).diff.includes('diff --git a/src/importer.py b/src/importer.py'), true);
    assert.equal(authorizedDiffs.get(3).diff.includes('diff --git a/tests/test_importer.py b/tests/test_importer.py'), true);
    assert.equal(authorized.trial.trajectory.diff.includes('diff --git a/src/importer.py b/src/importer.py'), true);
    assert.equal(authorized.trial.trajectory.diff.includes('diff --git a/tests/test_importer.py b/tests/test_importer.py'), true);
    assert.equal(stimulus.turns.length, 5);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
