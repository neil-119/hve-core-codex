import { VALLY_AMBIENT_CONFIG_ARGS } from './skill-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { appendFile, copyFile, cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CodexExecutor } from './index.mjs';
import { runBundle } from './run.mjs';
import { normalizeCapturedSourcePath, remapCapturedTrialId, verifyRunEvidence } from './verify.mjs';

const UPSTREAM_SHA = '0123456789abcdef0123456789abcdef01234567';
const ADAPTER_ROOT = dirname(fileURLToPath(import.meta.url));

test('captured Windows source paths remap safely onto the current host path style', () => {
  const sourceRoot = 'C:\\captured\\bundle';
  const sourceFile = 'C:\\captured\\bundle\\evals\\suite\\eval.yaml';
  assert.equal(normalizeCapturedSourcePath(sourceRoot, sourceFile, 'fixture'), 'evals/suite/eval.yaml');
  assert.throws(() => normalizeCapturedSourcePath(sourceRoot, 'C:\\outside\\eval.yaml', 'fixture'), /inside its captured source root/);
  const itemId = `${sourceFile}::default::model-a::stimulus-a::trial-0`;
  const destination = join(process.cwd(), 'remapped-bundle');
  assert.equal(remapCapturedTrialId(itemId, sourceRoot, destination, 'fixture'),
    `${join(destination, 'evals', 'suite', 'eval.yaml')}::default::model-a::stimulus-a::trial-0`);
});

function hash(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

async function fixtureRoot() {
  const root = await mkdtemp(join(process.cwd(), '.tmp-vally-verify-test-'));
  const bundle = join(root, 'bundle');
  await mkdir(join(bundle, 'evals'), { recursive: true });
  await mkdir(join(bundle, 'generated'), { recursive: true });
  await writeFile(join(bundle, '.vally.yaml'), 'paths:\n  evals: [evals]\n');
  const manifest = JSON.stringify({ 'fixture/source': { resources: ['plugins/fixture/source.md'] } }, null, 2) + '\n';
  const inventory = JSON.stringify({
    schema_version: 1,
    upstream_sha: UPSTREAM_SHA,
    upstream_ref: 'fixture-ref',
    upstream_url: 'https://example.invalid/fixture.git',
    channel: 'synthetic',
    files: [],
    artifacts: [],
  }, null, 2) + '\n';
  await writeFile(join(bundle, 'generated', 'source-to-output.json'), manifest);
  await writeFile(join(bundle, 'generated', 'source-inventory.json'), inventory);
  await writeFile(join(bundle, 'generated', 'vally-source-to-output.json'), '{}\n');
  await writeFile(join(bundle, 'generated', 'vally-bundle-report.json'), JSON.stringify({
    schema_version: 1,
    status: 'complete',
    success: true,
    source: {
      distribution_root_name: 'fixture-distribution',
      upstream_root_name: 'fixture-upstream',
      manifest: 'generated/source-to-output.json',
      manifest_sha256: hash(Buffer.from(manifest)),
      source_inventory: {
        path: 'generated/source-inventory.json',
        sha256: hash(Buffer.from(inventory)),
        channel: 'synthetic',
        upstream_ref: 'fixture-ref',
        upstream_sha: UPSTREAM_SHA,
        upstream_url: 'https://example.invalid/fixture.git',
      },
    },
    source_to_output: 'generated/vally-source-to-output.json',
  }, null, 2) + '\n');
  return { root, bundle, output: join(root, 'run') };
}

function fixtureEval(name, stimuli, runs = 1, scoring) {
  const lines = [
    `name: ${JSON.stringify(name)}`,
    'type: regression',
    'defaults:',
    '  executor: codex',
    `  runs: ${runs}`,
  ];
  if (scoring) {
    lines.push('scoring:');
    lines.push(`  threshold: ${scoring.threshold}`);
  }
  lines.push('stimuli:');
  for (const stimulus of stimuli) {
    lines.push(`  - name: ${JSON.stringify(stimulus.name)}`);
    lines.push(`    prompt: ${JSON.stringify(stimulus.prompt ?? 'fixture prompt')}`);
    lines.push('    graders:');
    lines.push('      - type: output-matches');
    lines.push('        config:');
    lines.push(`          pattern: ${JSON.stringify(stimulus.pattern ?? '^fixture response$')}`);
  }
  return `${lines.join('\n')}\n`;
}

async function writeEval(bundle, filename, name, stimuli, runs = 1, scoring) {
  const directory = join(bundle, 'evals', filename);
  await mkdir(directory, { recursive: true });
  const raw = fixtureEval(name, stimuli, runs, scoring);
  const path = join(directory, 'eval.yaml');
  await writeFile(path, raw);
  const identity = `evals/${filename}/eval.yaml`;
  const archive = join(bundle, 'upstream', ...identity.split('/'));
  await mkdir(join(bundle, 'upstream', ...identity.split('/').slice(0, -1)), { recursive: true });
  await writeFile(archive, raw);
  const mapPath = join(bundle, 'generated', 'vally-source-to-output.json');
  const sourceMap = JSON.parse(await readFile(mapPath, 'utf8'));
  const digest = hash(Buffer.from(raw));
  sourceMap[identity] = { source: identity, outputs: [
    { path: `upstream/${identity}`, status: 'preserved-original', sha256: digest },
    { path: identity, status: 'converted-eval-spec', sha256: digest },
  ] };
  await writeFile(mapPath, JSON.stringify(sourceMap, null, 2) + '\n');
}

class FakeCodex extends CodexExecutor {
  calls = 0;
  wrongResponseCall;

  constructor({ wrongResponseCall } = {}) {
    super({ skillIsolationResolver: async ({ workDir, skills = [] }) => {
      const paths = skills.map(skill => join(workDir, '.agents', 'skills', skill.name, 'SKILL.md'));
      return { mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS],
        expectedPaths: paths, enabledPaths: paths, disabledSkills: 0, elapsedMs: 0 };
    } });
    this.wrongResponseCall = wrongResponseCall;
  }

  async run(_binary, _args, _prompt, options) {
    this.calls++;
    const response = this.calls === this.wrongResponseCall ? 'incorrect response' : 'fixture response';
    const rows = [
      { type: 'thread.started', thread_id: `thread-${this.calls}` },
      { type: 'turn.started' },
      { type: 'item.completed', item: { type: 'agent_message', text: response } },
      { type: 'turn.completed', usage: { input_tokens: 5, output_tokens: 3 } },
    ];
    if (options.sessionLog?.rootDir)
      await appendFile(join(options.sessionLog.rootDir, 'events.jsonl'), rows.map(row => JSON.stringify(row)).join('\n') + '\n');
    for (const row of rows) options.onRawEvent?.(row);
    return rows;
  }
}

async function executeFixture({ stimuli = [{ name: 'one' }], runs = 1, scoring, wrongResponseCall, evalNames, filename = 'sample' } = {}) {
  const fixture = await fixtureRoot();
  await writeEval(fixture.bundle, filename, filename, stimuli, runs, scoring);
  const report = await runBundle(fixture.bundle, { output: fixture.output, evalNames }, { executor: new FakeCodex({ wrongResponseCall }) });
  return { ...fixture, report };
}

async function verifyFixture(fixture, bundle = fixture.bundle, adapterRoot = ADAPTER_ROOT) {
  return await verifyRunEvidence({ bundle, run: fixture.output, expectedUpstreamSha: UPSTREAM_SHA, approvedAdapterRoot: adapterRoot });
}

async function copyAdapterRuntimeFixture(adapterCopy) {
  await mkdir(adapterCopy, { recursive: true });
  for (const name of ['run.mjs', 'plan.mjs', 'index.mjs', 'app-server.mjs', 'app-server-execution.mjs',
    'app-server-events.mjs', 'turn.mjs', 'settlement.mjs', 'skill-isolation.mjs', 'grader-capabilities.mjs', 'shell-normalization.mjs', 'mcp-config-translator.mjs', 'bundle-resource-integrity.mjs', 'judge.mjs', 'judge-evidence.mjs', 'package.json', 'package-lock.json', 'navigation.json'])
    await copyFile(join(ADAPTER_ROOT, name), join(adapterCopy, name));
  const vallyPackage = join(adapterCopy, 'node_modules', '@microsoft', 'vally');
  await mkdir(vallyPackage, { recursive: true });
  await copyFile(join(ADAPTER_ROOT, 'node_modules', '@microsoft', 'vally', 'package.json'), join(vallyPackage, 'package.json'));
  await cp(join(ADAPTER_ROOT, 'node_modules', '@microsoft', 'vally', 'dist'), join(vallyPackage, 'dist'),
    { recursive: true, dereference: false, errorOnExist: true, force: false });
}

test('independently verifies a full Vally run from source pins, re-planned identities, JSONL, and scoring', async () => {
  const fixture = await executeFixture();
  try {
    assert.equal(fixture.report.status, 'completed', JSON.stringify(fixture.report, null, 2));
    const verification = await verifyFixture(fixture);
    assert.equal(verification.verified, true, JSON.stringify(verification, null, 2));
    assert.equal(verification.fullSuiteExecution, true);
    assert.deepEqual(verification.counts, { fullPlanned: 1, selectedPlanned: 1, actualResults: 1, successfulResults: 1 });
    assert.equal(verification.scoring.passed, true);
    assert.equal(verification.blockers.length, 0);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('copied fixture tampering fails independent verification and blocks a subsequent run', async () => {
  const fixture = await fixtureRoot();
  try {
    await writeEval(fixture.bundle, 'sample', 'sample', [{ name: 'one' }]);
    const resource = 'evals/sample/fixture/input.txt';
    await mkdir(join(fixture.bundle, 'evals/sample/fixture'), { recursive: true });
    await writeFile(join(fixture.bundle, resource), 'original');
    const mapPath = join(fixture.bundle, 'generated/vally-source-to-output.json');
    const map = JSON.parse(await readFile(mapPath, 'utf8'));
    map['fixture/input.txt'] = { outputs: [
      { path: 'evals/sample/fixture', status: 'local-resource-directory' },
      { path: resource, status: 'local-resource-copy', sha256: hash(Buffer.from('original')) },
    ] };
    await writeFile(mapPath, JSON.stringify(map));
    fixture.report = await runBundle(fixture.bundle, { output: fixture.output }, { executor: new FakeCodex() });
    assert.equal((await verifyFixture(fixture)).verified, true);
    await writeFile(join(fixture.bundle, resource), 'tampered');
    const verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('resource hash differs')));
    const executor = new FakeCodex();
    const report = await runBundle(fixture.bundle, { output: join(fixture.root, 'tampered-run') }, { executor });
    assert.equal(executor.calls, 0, 'no executor task starts with altered fixture bytes');
    assert.notEqual(report.status, 'completed');
  } finally { await rm(fixture.root, { recursive: true, force: true }); }
});

test('missing or unsupported source-bound provenance is denied by default', async () => {
  const fixture = await executeFixture();
  try {
    const reportPath = join(fixture.output, 'run-report.json');
    const report = JSON.parse(await readFile(reportPath, 'utf8'));
    delete report.runProvenance;
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
    let verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('missing or unsupported source-bound provenance')));

    report.runProvenance = fixture.report.runProvenance;
    report.runProvenance.version += 1;
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
    verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('missing or unsupported source-bound provenance')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('a selected subset cannot verify as full-bundle execution even when every selected trial passes', async () => {
  const fixture = await fixtureRoot();
  try {
    await writeEval(fixture.bundle, 'first', 'first', [{ name: 'one' }], 2);
    await writeEval(fixture.bundle, 'second', 'second', [{ name: 'two' }], 1);
    fixture.report = await runBundle(fixture.bundle, { output: fixture.output, evalNames: ['first'] }, { executor: new FakeCodex() });
    const verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.equal(verification.counts.fullPlanned, 3);
    assert.equal(verification.counts.selectedPlanned, 2);
    assert.ok(verification.blockers.some(message => message.includes('not the complete original bundle suite')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('exact-count timeout/error rows remain unverified', async () => {
  const fixture = await fixtureRoot();
  try {
    await writeEval(fixture.bundle, 'three', 'three', [
      { name: 'one' }, { name: 'two' }, { name: 'three' },
    ]);
    const executor = new FakeCodex();
    const originalRun = executor.run.bind(executor);
    executor.run = async (...args) => {
      if (executor.calls === 2) throw new Error('synthetic hard timeout');
      return originalRun(...args);
    };
    fixture.report = await runBundle(fixture.bundle, { output: fixture.output }, { executor });
    const verification = await verifyFixture(fixture);
    assert.equal(fixture.report.completedTrials, 3, 'the wrapper counts the terminal timeout result');
    assert.equal(fixture.report.runProvenance?.execution?.exactCoverage, true);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('did not complete successfully')) ||
      verification.blockers.some(message => message.includes('run-summary')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('moved bundle remains verifiable by relative source identity', async () => {
  const fixture = await executeFixture({ stimuli: [{ name: 'one' }, { name: 'two' }] });
  const moved = join(fixture.root, 'moved-bundle');
  try {
    await cp(fixture.bundle, moved, { recursive: true, dereference: false, errorOnExist: true, force: false });
    const verification = await verifyFixture(fixture, moved);
    assert.equal(verification.verified, true, JSON.stringify(verification, null, 2));
    assert.equal(verification.fullSuiteExecution, true);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('tampered converted spec and result rows are rejected', async () => {
  const fixture = await executeFixture();
  try {
    const specPath = join(fixture.bundle, 'evals', 'sample', 'eval.yaml');
    await appendFile(specPath, '# modified after execution\n');
    let verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('converted eval spec hash/size differs')));
    const resultPath = join(fixture.output, 'results.jsonl');
    const lines = (await readFile(resultPath, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    const trial = lines.find(row => row.type === 'trial-result');
    trial.status = 'error';
    await writeFile(resultPath, lines.map(row => JSON.stringify(row)).join('\n') + '\n');
    verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('did not complete successfully') || message.includes('status/grade result')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('approved adapter source hashes must match the run provenance', async () => {
  const fixture = await executeFixture();
  const adapterCopy = join(fixture.root, 'adapter-copy');
  try {
    await copyAdapterRuntimeFixture(adapterCopy);
    await writeFile(join(adapterCopy, 'run.mjs'), 'tampered adapter\n');
    const verification = await verifyFixture(fixture, fixture.bundle, adapterCopy);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('approved runtime adapter changed')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('tampered installed Vally planner/grader JavaScript is rejected by source hash', async () => {
  const fixture = await executeFixture();
  const adapterCopy = join(fixture.root, 'adapter-copy');
  try {
    await copyAdapterRuntimeFixture(adapterCopy);
    const sourceFile = fixture.report.runProvenance.source.runtimeDependencyFiles[0];
    assert.ok(sourceFile.path.startsWith('node_modules/@microsoft/vally/dist/'));
    await appendFile(join(adapterCopy, ...sourceFile.path.split('/')), '\n// test-only tamper\n');
    const verification = await verifyFixture(fixture, fixture.bundle, adapterCopy);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('approved Vally runtime JavaScript changed since the run')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('Vally threshold scoring can pass with an individual failing grade', async () => {
  const fixture = await executeFixture({
    stimuli: [{ name: 'threshold', pattern: '^fixture response$' }],
    runs: 4,
    scoring: { threshold: 0.7 },
    wrongResponseCall: 1,
  });
  try {
    assert.equal(fixture.report.status, 'completed', JSON.stringify(fixture.report, null, 2));
    const rows = (await readFile(join(fixture.output, 'results.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    const trialRows = rows.filter(row => row.type === 'trial-result');
    const summary = rows.find(row => row.type === 'run-summary');
    assert.equal(trialRows.filter(row => row.gradeResult?.passed === false).length, 1);
    assert.equal(summary.passed, true);
    assert.equal(summary.evals[0].threshold, 0.7);
    assert.equal(summary.evals[0].overallScore, 0.75);
    const verification = await verifyFixture(fixture);
    assert.equal(verification.verified, true, JSON.stringify(verification, null, 2));
    assert.equal(verification.scoring.passed, true);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test('symlinked run artifacts are rejected without reading outside the declared root', async t => {
  const fixture = await executeFixture();
  try {
    await rm(join(fixture.output, 'results.jsonl'));
    await writeFile(join(fixture.root, 'outside.jsonl'), '{}\n');
    try { await symlink(join(fixture.root, 'outside.jsonl'), join(fixture.output, 'results.jsonl'), 'file'); }
    catch (error) {
      if (process.platform === 'win32' && ['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) {
        t.skip('host does not allow creating a test symlink');
        return;
      }
      throw error;
    }
    const verification = await verifyFixture(fixture);
    assert.equal(verification.verified, false);
    assert.ok(verification.blockers.some(message => message.includes('symbolic link')));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});
