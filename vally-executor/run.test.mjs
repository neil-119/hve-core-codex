import { VALLY_AMBIENT_CONFIG_ARGS } from './skill-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { appendFile, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { CodexExecutor } from './index.mjs';
import { buildBundlePlan, inspectBundle } from './plan.mjs';
import { compactCliSummary, parseRunnerArgs, planBlockers, runBundle } from './run.mjs';

async function fixtureRoot() {
  const root = await mkdtemp(join(process.cwd(), '.tmp-vally-run-test-'));
  const bundle = join(root, 'bundle');
  await mkdir(join(bundle, 'evals'), { recursive: true });
  await mkdir(join(bundle, 'generated'), { recursive: true });
  await writeFile(join(bundle, '.vally.yaml'), 'paths:\n  evals: [evals]\n');
  const manifest = JSON.stringify({ fixture: 'source-manifest' }, null, 2) + '\n';
  const inventory = JSON.stringify({ fixture: 'source-inventory' }, null, 2) + '\n';
  await writeFile(join(bundle, 'generated', 'source-to-output.json'), manifest);
  await writeFile(join(bundle, 'generated', 'source-inventory.json'), inventory);
  await writeFile(join(bundle, 'generated', 'vally-source-to-output.json'), '{}\n');
  await writeFile(join(bundle, 'generated', 'vally-bundle-report.json'), JSON.stringify({
    schema_version: 1,
    status: 'complete',
    success: true,
    source: {
      distribution_root_name: 'synthetic-fixture',
      upstream_root_name: 'synthetic-source',
      manifest: 'generated/source-to-output.json',
      manifest_sha256: createHash('sha256').update(manifest).digest('hex'),
      source_inventory: {
        path: 'generated/source-inventory.json',
        sha256: createHash('sha256').update(inventory).digest('hex'),
        channel: 'synthetic',
        upstream_ref: 'fixture-ref',
        upstream_sha: '0123456789abcdef0123456789abcdef01234567',
        upstream_url: 'https://example.invalid/synthetic.git',
      },
    },
    source_to_output: 'generated/vally-source-to-output.json',
  }, null, 2) + '\n');
  return { root, bundle, output: join(root, 'output') };
}

async function writePinnedBinary(root, contents = 'synthetic pinned Codex executable fixture', filename = 'pinned-codex.exe') {
  const path = join(root, filename);
  await writeFile(path, contents);
  return { path, sha256: createHash('sha256').update(contents).digest('hex') };
}

function writeMcpServers(lines, indent, servers) {
  lines.push(`${indent}mcpServers:`);
  for (const [server, config] of Object.entries(servers)) {
    lines.push(`${indent}  ${server}:`);
    for (const [key, value] of Object.entries(config))
      lines.push(`${indent}    ${key}: ${JSON.stringify(value)}`);
  }
}

function fixtureEval(name, stimuli, runs = 1, defaultOverrides = {}, evalEnvironment) {
  const lines = [
    `name: ${JSON.stringify(name)}`,
    'type: regression',
    'defaults:',
    '  executor: codex',
    `  runs: ${runs}`,
    ...Object.entries(defaultOverrides).map(([key, value]) => `  ${key}: ${JSON.stringify(value)}`),
  ];
  if (evalEnvironment?.mcpServers) {
    lines.push('agent_environment:');
    writeMcpServers(lines, '  ', evalEnvironment.mcpServers);
  }
  lines.push('stimuli:');
  for (const stimulus of stimuli) {
    lines.push(`  - name: ${JSON.stringify(stimulus.name)}`);
    lines.push(`    prompt: ${JSON.stringify(stimulus.prompt ?? 'fixture prompt')}`);
    if (stimulus.rubric) lines.push(`    rubric: ${JSON.stringify(stimulus.rubric)}`);
    if (stimulus.attachments) lines.push(`    attachments: ${JSON.stringify(stimulus.attachments)}`);
    if (stimulus.simulation) {
      lines.push('    simulation:');
      lines.push(`      max_iterations: ${stimulus.simulation.max_iterations ?? 1}`);
    }
    if (stimulus.environment) {
      lines.push('    agent_environment:');
      if (stimulus.environment.mcpServers) writeMcpServers(lines, '      ', stimulus.environment.mcpServers);
    }
    lines.push('    graders:');
    for (const grader of stimulus.graders ?? [{ type: 'output-matches', config: { pattern: '^fixture response$' } }]) {
      lines.push(`      - type: ${grader.type}`);
      if (grader.config) {
        lines.push('        config:');
        for (const [key, value] of Object.entries(grader.config))
          lines.push(`          ${key}: ${JSON.stringify(value)}`);
      }
    }
  }
  return `${lines.join('\n')}\n`;
}

test('known native grader predicate gaps block execution and remain visible in offline planning', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  try {
    await writeEval(bundle, 'native-gap.yaml', 'native-gap', [{ name: 'scanner', graders: [{
      type: 'tool-calls', config: { required: [{ name: '^powershell$', command: 'scan_sensitive_content' }] },
    }] }], 2);
    const planned = await inspectBundle(bundle);
    assert.equal(planned.valid, false);
    assert.equal(planned.graderCapabilityAudits.length, 1);
    assert.equal(planned.graderCapabilityAudits[0].findings[0].originalRegex, '^powershell$');
    const report = await runBundle(bundle, { output }, { executor });
    assert.equal(report.status, 'preflight-failed');
    assert.equal(report.runtimeInvoked, false);
    assert.equal(executor.calls.length, 0);
    assert.ok(report.blockers.some(value => value.includes('exec_command')));
    assert.equal(report.graderCapabilityAudits[0].acceptanceClaim, false);
    assert.ok(report.runProvenance.source.runtimeAdapters.some(file => file.path === 'adapter/grader-capabilities.mjs'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

async function writeEval(bundle, filename, name, stimuli, runs = 1, defaultOverrides = {}, evalEnvironment) {
  const evalDirectory = join(bundle, 'evals', filename.replace(/\.ya?ml$/i, ''));
  await mkdir(evalDirectory, { recursive: true });
  const raw = fixtureEval(name, stimuli, runs, defaultOverrides, evalEnvironment);
  const evalPath = join(evalDirectory, 'eval.yaml');
  await writeFile(evalPath, raw);
  const identity = `evals/${filename.replace(/\\/g, '/').replace(/\.ya?ml$/i, '')}/eval.yaml`;
  const archivePath = join(bundle, 'upstream', ...identity.split('/'));
  await mkdir(join(bundle, 'upstream', ...identity.split('/').slice(0, -1)), { recursive: true });
  await writeFile(archivePath, raw);
  const sourceMapPath = join(bundle, 'generated', 'vally-source-to-output.json');
  const sourceMap = JSON.parse(await readFile(sourceMapPath, 'utf8'));
  const digest = createHash('sha256').update(raw).digest('hex');
  sourceMap[identity] = { source: identity, outputs: [
    { path: `upstream/${identity}`, status: 'preserved-original', sha256: digest },
    { path: identity, status: 'converted-eval-spec', sha256: digest },
  ] };
  await writeFile(sourceMapPath, JSON.stringify(sourceMap, null, 2) + '\n');
}

class FakeCodex extends CodexExecutor {
  calls = [];
  failPrompt;

  constructor(options = {}) {
    super({ skillIsolationResolver: options.skillIsolationResolver ?? (async ({ workDir, skills = [] }) => {
      const paths = skills.map(skill => resolve(join(workDir, '.agents', 'skills', skill.name, 'SKILL.md')));
      return { mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS],
        expectedPaths: paths, enabledPaths: paths, disabledSkills: 0, elapsedMs: 0 };
    }) });
    this.failPrompt = options.failPrompt;
  }

  async run(_binary, _args, prompt, options) {
    this.calls.push({ prompt, model: options.model, workDir: options.workDir });
    if (prompt === this.failPrompt) throw new Error('synthetic executor failure');
    const records = [
      { type: 'thread.started', thread_id: `synthetic-${this.calls.length}` },
      { type: 'turn.started' },
      { type: 'item.completed', item: { type: 'agent_message', text: 'fixture response' } },
      { type: 'turn.completed', usage: { input_tokens: 3, output_tokens: 2 } },
    ];
    if (options.sessionLog?.rootDir) {
      await appendFile(join(options.sessionLog.rootDir, 'events.jsonl'), records.map(record => JSON.stringify(record)).join('\n') + '\n');
    }
    for (const record of records) options.onRawEvent?.(record);
    return records;
  }
}

test('dry-run persists the Vally plan without invoking execution or judges', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  const calls = { backend: 0, judge: 0 };
  const backend = { async prepare() { calls.backend++; }, async runTrial() { calls.backend++; }, async shutdown() { calls.backend++; } };
  const judgeClient = { async judge() { calls.judge++; }, async shutdown() { calls.judge++; } };
  try {
    await writeEval(bundle, 'smoke.yaml', 'smoke', [{ name: 'one', prompt: 'offline' }], 2);
    const report = await runBundle(bundle, { output, dryRun: true }, { executor, backend, judgeClient });
    assert.equal(report.status, 'dry-run', JSON.stringify(report, null, 2));
    assert.equal(report.plannedTrials, 2);
    assert.equal(report.runtimeInvoked, false);
    assert.equal(report.exitCode, 0);
    assert.equal(executor.calls.length, 0);
    assert.deepEqual(calls, { backend: 0, judge: 0 });
    assert.deepEqual(await readdir(output), ['run-report.json']);
    const saved = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8'));
    assert.equal(saved.status, 'dry-run');
    assert.equal(saved.plannedTrials, 2);
    assert.equal(saved.files.runReport, join(output, 'run-report.json'));
    assert.equal(saved.runProvenance.schema, 'hve.vally-run-provenance');
    assert.equal(saved.runProvenance.version, 2);
    assert.equal(saved.runProvenance.source.bundleReport.sha256.length, 64);
    const sourceManifestBytes = await readFile(join(bundle, 'generated', 'source-to-output.json'));
    const sourceInventoryBytes = await readFile(join(bundle, 'generated', 'source-inventory.json'));
    const sourceMapBytes = await readFile(join(bundle, 'generated', 'vally-source-to-output.json'));
    assert.equal(saved.runProvenance.source.manifest.sha256,
      createHash('sha256').update(sourceManifestBytes).digest('hex'));
    assert.equal(saved.runProvenance.source.manifest.verified, true);
    assert.equal(saved.runProvenance.source.sourceInventory.sha256,
      createHash('sha256').update(sourceInventoryBytes).digest('hex'));
    assert.equal(saved.runProvenance.source.sourceInventory.verified, true);
    assert.equal(saved.runProvenance.source.sourceToOutputMap.sha256,
      createHash('sha256').update(sourceMapBytes).digest('hex'));
    assert.equal(saved.runProvenance.source.verification.manifestHashVerified, true);
    assert.equal(saved.runProvenance.source.verification.sourceInventoryHashVerified, true);
    assert.equal(saved.runProvenance.source.declaredSourcePin.sourceInventory.upstreamSha,
      '0123456789abcdef0123456789abcdef01234567');
    assert.equal(saved.runProvenance.source.specFiles.length, 1);
    assert.equal(saved.runProvenance.source.specFiles[0].selected, true);
    assert.equal(saved.runProvenance.source.specFiles[0].sha256.length, 64);
    assert.ok(saved.runProvenance.source.runtimeAdapters.some(file => file.path === 'adapter/run.mjs'));
    assert.ok(saved.runProvenance.source.runtimeAdapters.some(file => file.path === 'adapter/judge.mjs'));
    assert.ok(saved.runProvenance.source.runtimeAdapters.some(file => file.path === 'adapter/judge-evidence.mjs'));
    assert.ok(saved.runProvenance.source.runtimeAdapters.every(file => file.sha256.length === 64));
    const runtimeJs = saved.runProvenance.source.runtimeDependencyFiles;
    assert.ok(runtimeJs.length > 0);
    assert.ok(runtimeJs.every(file => file.path.startsWith('node_modules/@microsoft/vally/dist/') && file.sha256.length === 64));
    assert.deepEqual(runtimeJs.map(file => file.path), [...runtimeJs.map(file => file.path)].sort());
    assert.equal(saved.runProvenance.suite.fullBundle.plannedTrialCount, 2);
    assert.equal(saved.runProvenance.suite.selected.plannedTrialCount, 2);
    assert.equal(saved.runProvenance.suite.selected.selectionIsFullBundle, true);
    assert.equal(saved.runProvenance.suite.selected.expectedTrials.length, 2);
    assert.ok(saved.runProvenance.suite.selected.expectedTrials.every(trial => trial.grading.graderConfigSha256.length === 64));
    assert.equal(saved.runProvenance.execution.state, 'not-run');
    assert.equal(saved.runProvenance.execution.fullSuiteExecution, false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('Codex judge selection is visible in dry-run planning without constructing a real client', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  const backendCalls = [];
  const backend = { async prepare() { backendCalls.push('prepare'); }, async runTrial() { backendCalls.push('trial'); }, async shutdown() { backendCalls.push('shutdown'); } };
  try {
    await writeEval(bundle, 'judge.yaml', 'judge-needed', [{
      name: 'one', rubric: ['The output is correct.'],
      graders: [{ type: 'prompt', config: {} }],
    }]);
    const report = await runBundle(bundle, { output, dryRun: true, judgeBackend: 'codex' }, { executor, backend });
    assert.equal(report.status, 'dry-run', JSON.stringify(report, null, 2));
    assert.equal(report.llmGraderApplications, 1);
    assert.equal(report.judgeBackendSelected, true);
    assert.equal(report.actualJudgeBackendSelected, false);
    assert.equal(report.judgeClientKind, 'planning-fake');
    assert.equal(report.judgePreparationStatus, 'planning-only');
    assert.deepEqual(report.judgeCompatibility, { toolIsolation: 'unverified', acceptedLimitation: true });
    assert.match(report.judgeCompatibilityWarning, /tool isolation is unverified/);
    assert.deepEqual(backendCalls, []);
    assert.equal(executor.calls.length, 0);
    const saved = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8'));
    assert.equal(saved.runProvenance.judge.requestedBackend, 'codex');
    assert.equal(saved.runProvenance.judge.backendSelected, true);
    assert.equal(saved.runProvenance.judge.actualBackendSelected, false);
    assert.equal(saved.runProvenance.judge.clientKind, 'planning-fake');
    assert.deepEqual(saved.runProvenance.judge.compatibility,
      { toolIsolation: 'unverified', acceptedLimitation: true });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('selecting a Codex backend rejects an injected judge client as ambiguous', async () => {
  const { root, bundle, output } = await fixtureRoot();
  try {
    await writeEval(bundle, 'smoke.yaml', 'smoke', [{ name: 'one', prompt: 'offline' }]);
    const report = await runBundle(bundle, { output, dryRun: true, judgeBackend: 'codex' }, {
      judgeClient: { supportsWorkspaceDelivery: true, async judge() {}, async shutdown() {} },
    });
    assert.equal(report.status, 'preflight-failed');
    assert.ok(report.blockers.some(blocker => blocker.includes('cannot be combined with judgeBackend')));
    assert.equal(report.actualJudgeBackendSelected, false);
    assert.deepEqual(report.judgeCompatibility, { toolIsolation: 'unverified', acceptedLimitation: true });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('missing preserved source manifest or inventory cannot produce a newly certified bundle run', async () => {
  const { root, bundle, output } = await fixtureRoot();
  try {
    await writeEval(bundle, 'smoke.yaml', 'smoke', [{ name: 'one', prompt: 'offline' }]);
    await rm(join(bundle, 'generated', 'source-to-output.json'));
    await rm(join(bundle, 'generated', 'source-inventory.json'));

    const report = await runBundle(bundle, { output, dryRun: true });
    assert.equal(report.status, 'preflight-failed');
    assert.ok(report.blockers.some(blocker => blocker.includes('could not verify bundle manifest file')));
    assert.ok(report.blockers.some(blocker => blocker.includes('could not verify bundle sourceInventory file')));
    const provenance = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runProvenance;
    assert.equal(provenance.source.manifest.present, false);
    assert.equal(provenance.source.sourceInventory.present, false);
    assert.equal(provenance.source.verification.sourcePinValid, false);
    assert.equal(provenance.suite.selected.selectionIsFullBundle, true);
    assert.equal(provenance.execution.fullSuiteExecution, false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('preflight blocks LLM graders when standard Codex judge support is unavailable', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  let backendCalls = 0;
  const backend = { async prepare() { backendCalls++; }, async runTrial() { backendCalls++; }, async shutdown() { backendCalls++; } };
  try {
    await writeEval(bundle, 'judge.yaml', 'judge-needed', [{
      name: 'one', prompt: 'fixture', rubric: ['The output is correct.'],
      graders: [{ type: 'prompt', config: {} }],
    }]);
    const report = await runBundle(bundle, { output }, { executor, backend });
    assert.equal(report.status, 'preflight-failed', JSON.stringify(report, null, 2));
    assert.equal(report.runtimeInvoked, false);
    assert.ok(report.blockers.some(blocker => blocker.includes('standard Codex judge support is unavailable')));
    assert.equal(executor.calls.length, 0);
    assert.equal(backendCalls, 0);
    assert.equal(JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).status, 'preflight-failed');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('a Vally-compatible judge client can be injected programmatically', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  const calls = { judge: 0, shutdown: 0 };
  try {
    await writeEval(bundle, 'judge.yaml', 'judge-needed', [{
      name: 'one', rubric: ['The output is correct.'],
      graders: [{ type: 'prompt', config: {} }],
    }]);
    const judgeClient = {
      supportsWorkspaceDelivery: true,
      async judge() {
        calls.judge++;
        return {
          args: {
            rubric_scores: [{ criterion: 'The output is correct.', score: 5, reasoning: 'synthetic injected judge' }],
            overall_score: 5,
            overall_reasoning: 'synthetic injected judge',
          },
          tokenUsage: { inputTokens: 2, outputTokens: 1, model: 'fixture-judge' },
          latencyMs: 1,
          remindersUsed: 0,
          evidenceUsage: { files: [] },
        };
      },
      async shutdown() { calls.shutdown++; },
    };
    const report = await runBundle(bundle, { output }, { executor, judgeClient });
    assert.equal(report.status, 'completed', JSON.stringify(report, null, 2));
    assert.equal(report.judgeClientProvided, true);
    assert.equal(report.judgePreparationStatus, 'injected');
    assert.deepEqual(calls, { judge: 1, shutdown: 1 });
    assert.equal(executor.calls.length, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('failure closing an injected Vally judge client fails the run', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  let shutdownCalls = 0;
  try {
    await writeEval(bundle, 'judge.yaml', 'judge-needed', [{
      name: 'one', rubric: ['The output is correct.'],
      graders: [{ type: 'prompt', config: {} }],
    }]);
    const report = await runBundle(bundle, { output }, {
      executor,
      judgeClient: {
        supportsWorkspaceDelivery: true,
        async judge() {
          return {
            args: {
              rubric_scores: [{ criterion: 'The output is correct.', score: 5, reasoning: 'synthetic injected judge' }],
              overall_score: 5, overall_reasoning: 'synthetic injected judge',
            },
            latencyMs: 1, remindersUsed: 0,
          };
        },
        async shutdown() { shutdownCalls++; throw new Error('injected shutdown failure'); },
      },
    });
    assert.equal(report.status, 'failed');
    assert.equal(shutdownCalls, 1);
    assert.match(report.failure, /injected shutdown failure/);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('preflight rejects simulation, attachments, and MCP configurations before runtime', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  try {
    const evalDir = join(bundle, 'evals', 'unsupported');
    await mkdir(evalDir, { recursive: true });
    await writeFile(join(evalDir, 'fixture.txt'), 'synthetic attachment');
    await writeEval(bundle, 'unsupported', 'unsupported', [
      { name: 'simulation', prompt: 'fixture', simulation: { max_iterations: 1 } },
      { name: 'attachment', prompt: 'fixture', attachments: ['fixture.txt'] },
      { name: 'mcp', prompt: 'fixture', environment: { mcpServers: { fixture: { type: 'stdio', command: 'node' } } } },
    ]);
    const report = await runBundle(bundle, { output, dryRun: true }, { executor });
    assert.equal(report.status, 'preflight-failed', JSON.stringify(report, null, 2));
    assert.ok(report.blockers.some(blocker => blocker.includes('tool-call simulation')));
    assert.ok(report.blockers.some(blocker => blocker.includes('prompt attachments')));
    assert.ok(report.blockers.some(blocker => blocker.includes('MCP')));
    assert.equal(executor.calls.length, 0);
    assert.equal(JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runtimeInvoked, false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('workspace assignment retains same-named stimuli from separate evals and all trials', async () => {
  const { root, bundle, output } = await fixtureRoot();
  try {
    await writeEval(bundle, 'first.yaml', 'first', [{ name: 'shared', prompt: 'first' }], 2);
    await writeEval(bundle, 'second.yaml', 'second', [{ name: 'shared', prompt: 'second' }], 2);
    const report = await runBundle(bundle, { output, dryRun: true });
    assert.equal(report.status, 'dry-run', JSON.stringify(report, null, 2));
    assert.equal(report.plannedTrials, 4);
    const { buildBundlePlan } = await import('./plan.mjs');
    const { plan } = await buildBundlePlan(bundle, { workspaceRoot: join(output, 'workspace-check') });
    assert.equal(plan.totalItems, 4);
    assert.equal(new Set(plan.items.map(item => item.run.trialWorkspace)).size, 4);
    assert.ok(plan.items.every(item => item.run.trialWorkspace.startsWith(join(output, 'workspace-check'))));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('selected trial provenance identifies omitted bundle trials and retains source grader configs', async () => {
  const { root, bundle, output } = await fixtureRoot();
  try {
    await writeEval(bundle, 'first.yaml', 'first', [{ name: 'one', prompt: 'first' }], 2);
    await writeEval(bundle, 'second.yaml', 'second', [{ name: 'two', prompt: 'second' }]);
    const report = await runBundle(bundle, { output, evalNames: ['first'], dryRun: true });
    assert.equal(report.status, 'dry-run', JSON.stringify(report, null, 2));
    const provenance = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runProvenance;
    assert.equal(provenance.suite.fullBundle.plannedTrialCount, 3);
    assert.equal(provenance.suite.selected.plannedTrialCount, 2);
    assert.equal(provenance.suite.selected.selectionIsFullBundle, false);
    assert.equal(provenance.suite.selected.omittedItemIds.length, 1);
    assert.equal(provenance.suite.fullBundle.expectedItemIds.length, 3);
    assert.ok(provenance.suite.selected.expectedTrials.every(trial => trial.evalName === 'first'));
    assert.equal(provenance.suite.selected.graderConfigurations.length, 1);
    assert.deepEqual(provenance.suite.selected.graderConfigurations[0].configs, [
      { type: 'output-matches', config: { pattern: '^fixture response$' } },
    ]);
    assert.equal(provenance.suite.selected.expectedTrials[0].grading.graderConfigSha256,
      createHash('sha256').update('[{"config":{"pattern":"^fixture response$"},"type":"output-matches"}]').digest('hex'));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('custom specs bind their converted bytes to separately hashed raw custom archives', async () => {
  const { root, bundle } = await fixtureRoot();
  const output = join(root, 'custom-output');
  const mismatchOutput = join(root, 'custom-hash-mismatch-output');
  try {
    const sourceIdentity = 'evals/codex-port/synthetic/eval.yaml';
    const convertedSpec = fixtureEval('custom-synthetic', [{ name: 'custom-check', prompt: 'fixture' }]);
    const rawOriginal = `${convertedSpec}# raw custom source bytes\n`;
    await writeEval(bundle, 'codex-port/synthetic.yaml', 'custom-synthetic', [
      { name: 'custom-check', prompt: 'fixture' },
    ]);
    const archivePath = join(bundle, 'custom', ...sourceIdentity.split('/'));
    await mkdir(join(bundle, 'custom', ...sourceIdentity.split('/').slice(0, -1)), { recursive: true });
    await writeFile(archivePath, rawOriginal);
    const sourceMapPath = join(bundle, 'generated', 'vally-source-to-output.json');
    const sourceMap = JSON.parse(await readFile(sourceMapPath, 'utf8'));
    sourceMap[sourceIdentity] = {
      source: sourceIdentity,
      origin: 'codex-custom',
      outputs: [
        { path: `custom/${sourceIdentity}`, status: 'preserved-original', sha256: createHash('sha256').update(rawOriginal).digest('hex') },
        { path: sourceIdentity, status: 'converted-eval-spec', sha256: createHash('sha256').update(convertedSpec).digest('hex') },
      ],
    };
    await writeFile(sourceMapPath, JSON.stringify(sourceMap, null, 2) + '\n');
    await rm(join(bundle, 'upstream', ...sourceIdentity.split('/')), { force: true });
    const bundleReportPath = join(bundle, 'generated', 'vally-bundle-report.json');
    const bundleReport = JSON.parse(await readFile(bundleReportPath, 'utf8'));
    bundleReport.source.custom = {
      root_name: 'synthetic-custom-root',
      files: [{ path: sourceIdentity, sha256: createHash('sha256').update(rawOriginal).digest('hex') }],
    };
    await writeFile(bundleReportPath, JSON.stringify(bundleReport, null, 2) + '\n');

    const report = await runBundle(bundle, { output, dryRun: true, evalNames: ['custom-synthetic'] });
    assert.equal(report.status, 'dry-run', JSON.stringify(report, null, 2));
    let provenance = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runProvenance;
    assert.deepEqual(provenance.source.declaredSourcePin.customDeclaration, bundleReport.source.custom);
    assert.equal(provenance.source.specFiles.find(file => file.evalName === 'custom-synthetic').origin, 'codex-custom');
    assert.equal(provenance.source.customArchives[0].sourcePath, sourceIdentity);
    assert.equal(provenance.source.customArchives[0].sha256, bundleReport.source.custom.files[0].sha256);
    assert.equal(provenance.source.customArchives[0].verified, true);

    bundleReport.source.custom.files[0].sha256 = 'f'.repeat(64);
    await writeFile(bundleReportPath, JSON.stringify(bundleReport, null, 2) + '\n');
    const mismatch = await runBundle(bundle, { output: mismatchOutput, dryRun: true, evalNames: ['custom-synthetic'] });
    assert.equal(mismatch.status, 'preflight-failed');
    assert.ok(mismatch.blockers.some(blocker => blocker.includes('custom source archive hash does not match')));
    provenance = JSON.parse(await readFile(join(mismatchOutput, 'run-report.json'), 'utf8')).runProvenance;
    assert.equal(provenance.source.verification.sourcePinValid, false);
    assert.equal(provenance.source.customArchives[0].verified, false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('local Vally backend writes native reports and bounded trial artifacts with a fake executor', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex();
  try {
    await writeEval(bundle, 'smoke.yaml', 'smoke', [{ name: 'one', prompt: 'offline' }]);
    const report = await runBundle(bundle, { output }, { executor });
    assert.equal(report.status, 'completed', JSON.stringify(report, null, 2));
    assert.equal(report.runtimeInvoked, true);
    assert.equal(report.completedTrials, 1);
    assert.equal(report.remainingTrials, 0);
    assert.equal(report.exitCode, 0);
    assert.equal(report.summary.passed, true);
    assert.deepEqual(Object.keys(report.summary).sort(), ['evals', 'hadExecutionErrors', 'passed']);
    assert.equal(report.summary.evals.length, 1);
    assert.equal(report.summary.evals[0].stimuliRun, 1);
    assert.equal(report.summary.evals[0].passed, true);
    assert.ok(!JSON.stringify(report.summary).includes('fixture response'), 'compact summary must not include trajectory text');
    assert.ok(!Object.hasOwn(report.runProvenance.execution.actualTrials[0], 'trajectory'), 'provenance must not include raw trajectories');
    assert.equal(executor.calls.length, 1);
    assert.equal(executor.calls[0].model, 'gpt-6-luna');
    for (const file of ['results.jsonl', 'report.md', 'junit.xml', 'run-report.json'])
      assert.ok((await readFile(join(output, file), 'utf8')).length > 0, `${file} should be written`);
    const vallyJsonl = await readFile(join(output, 'results.jsonl'), 'utf8');
    assert.ok(vallyJsonl.includes('fixture response'), 'Vally JSONL must retain the full trajectory evidence');
    assert.ok(vallyJsonl.includes('"type":"run-summary"'), 'Vally JSONL summary must remain intact');
    const sessionDirectories = await readdir(join(output, 'session-logs'));
    assert.equal(sessionDirectories.length, 1);
    const attempts = await readdir(join(output, 'session-logs', sessionDirectories[0]));
    assert.ok(attempts.includes('attempt-single'));
    assert.ok((await readFile(join(output, 'session-logs', sessionDirectories[0], 'attempt-single', 'events.jsonl'), 'utf8')).includes('turn.completed'));
    assert.ok((await readdir(join(output, 'workspaces'))).length === 1);
    assert.ok((await readdir(join(output, 'workspace-patches'))).length === 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('first Vally trial failure stops later work and finalizes a failed partial summary', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex({ failPrompt: 'fail first' });
  try {
    await writeEval(bundle, 'smoke.yaml', 'smoke', [
      { name: 'first', prompt: 'fail first' },
      { name: 'second', prompt: 'must not run' },
    ]);
    const report = await runBundle(bundle, { output }, { executor });
    assert.equal(report.status, 'failed', JSON.stringify(report, null, 2));
    assert.equal(report.runtimeInvoked, true);
    assert.equal(report.completedTrials, 1);
    assert.equal(report.remainingTrials, 1);
    assert.equal(report.exitCode, 1);
    assert.equal(report.summary.passed, false);
    assert.equal(report.summary.hadExecutionErrors, true);
    assert.equal(executor.calls.length, 1);
    assert.equal(executor.calls[0].prompt, 'fail first');
    const records = (await readFile(join(output, 'results.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.ok(records.some(record => record.type === 'trial-result' && record.status === 'error'));
    assert.ok(records.some(record => record.type === 'run-summary'));
    assert.ok((await readFile(join(output, 'run-report.json'), 'utf8')).includes('synthetic executor failure'));
    const provenance = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runProvenance;
    assert.equal(provenance.execution.resultsRecorded, 1);
    assert.equal(provenance.execution.failedResults, 1);
    assert.equal(provenance.execution.missingItemIds.length, 1);
    assert.equal(provenance.execution.exactCoverage, false);
    assert.equal(provenance.execution.fullSuiteExecution, false);
    assert.equal(provenance.source.integrity.unchanged, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('partial timeout-style error is recorded against all planned identities without claiming full success', async () => {
  const { root, bundle, output } = await fixtureRoot();
  const executor = new FakeCodex({ failPrompt: 'synthetic hard timeout' });
  try {
    await writeEval(bundle, 'three.yaml', 'three', [
      { name: 'alpha', prompt: 'first ok' },
      { name: 'bravo', prompt: 'second ok' },
      { name: 'charlie', prompt: 'synthetic hard timeout' },
    ]);
    const report = await runBundle(bundle, { output }, { executor });
    assert.equal(report.status, 'failed');
    assert.equal(report.completedTrials, 3, 'wrapper records each terminal trial result, including the timeout error');
    const provenance = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runProvenance;
    assert.equal(provenance.suite.selected.plannedTrialCount, 3);
    assert.equal(provenance.execution.resultsRecorded, 3);
    assert.equal(provenance.execution.successfulResults, 2);
    assert.equal(provenance.execution.failedResults, 1);
    assert.equal(provenance.execution.exactCoverage, true);
    assert.equal(provenance.execution.allExecutionStatusesSuccessful, false);
    assert.equal(provenance.execution.finalScoringPassed, false);
    assert.equal(provenance.execution.fullSuiteExecution, false);
    assert.match(provenance.execution.actualTrials.find(trial => trial.status !== 'success').error, /synthetic executor failure/);
    const records = (await readFile(join(output, 'results.jsonl'), 'utf8')).trim().split('\n').map(line => JSON.parse(line));
    assert.ok(records.some(record => record.type === 'trial-result' && record.status === 'error'));
    assert.ok(records.some(record => record.type === 'run-summary'));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('source changes during runtime fail closed and remain visible in provenance', async () => {
  const { root, bundle, output } = await fixtureRoot();
  let changed = false;
  const evalPath = join(bundle, 'evals', 'smoke', 'eval.yaml');
  class MutatingCodex extends FakeCodex {
    async run(...args) {
      const records = await super.run(...args);
      if (!changed) {
        changed = true;
        await appendFile(evalPath, '# mutation after source pin\n');
      }
      return records;
    }
  }
  try {
    await writeEval(bundle, 'smoke.yaml', 'smoke', [{ name: 'one', prompt: 'offline' }]);
    const report = await runBundle(bundle, { output }, { executor: new MutatingCodex() });
    assert.equal(report.status, 'failed');
    assert.equal(report.exitCode, 1);
    const provenance = JSON.parse(await readFile(join(output, 'run-report.json'), 'utf8')).runProvenance;
    assert.equal(provenance.source.integrity.unchanged, false);
    assert.ok(provenance.source.integrity.changedPaths.some(path => path.startsWith('evals/')));
    assert.equal(provenance.execution.fullSuiteExecution, false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('CLI accepts repeatable selectors and refuses hidden run-count overrides', () => {
  assert.deepEqual(parseRunnerArgs([
    '--bundle', 'bundle', '--output', 'out', '--eval', 'alpha', '--eval', 'beta', '--stimulus', 'smoke', '--dry-run',
  ]), {
    bundle: 'bundle', output: 'out', evalNames: ['alpha', 'beta'], stimulusNames: ['smoke'], dryRun: true,
    judgeBackend: 'none',
  });
  assert.throws(() => parseRunnerArgs(['--bundle', 'bundle', '--output', 'out', '--runs', '1']), /unsupported argument: --runs/);
  assert.throws(() => parseRunnerArgs(['--bundle', 'bundle', '--output', 'out', '--unknown', 'x']), /unsupported argument/);
});

test('CLI accepts only one supported judge backend and defaults it to none', () => {
  const args = ['--bundle', 'bundle', '--output', 'out'];
  assert.equal(parseRunnerArgs(args).judgeBackend, 'none');
  assert.equal(parseRunnerArgs([...args, '--judge-backend', 'codex']).judgeBackend, 'codex');
  assert.equal(parseRunnerArgs([...args, '--judge-backend', 'none']).judgeBackend, 'none');
  assert.throws(() => parseRunnerArgs([...args, '--judge-backend', 'other']), /must be none or codex/);
  assert.throws(() => parseRunnerArgs([...args, '--judge-backend']), /requires a value/);
  assert.throws(() => parseRunnerArgs([...args, '--judge-backend', 'codex', '--judge-backend', 'none']), /duplicate/);
});

test('CLI accepts an explicit Codex executable without shell parsing', () => {
  const args = ['--bundle', 'bundle', '--output', 'out'];
  assert.equal(parseRunnerArgs([...args, '--codex-binary', '/home/user/Codex CLI/codex']).codexBinary,
    '/home/user/Codex CLI/codex');
  assert.throws(() => parseRunnerArgs([...args, '--codex-binary']), /requires a value/);
  assert.throws(() => parseRunnerArgs([...args, '--codex-binary', ' ']), /nonempty executable path/);
  assert.throws(() => parseRunnerArgs([...args, '--codex-binary', 'codex', '--codex-binary', 'other']), /duplicate/);
});

test('CLI stdout projection stays bounded for full-suite provenance and points to the saved report', () => {
  const ids = Array.from({ length: 1516 }, (_, index) => `planned-trial-${index}`);
  const report = {
    mode: 'vally-run', status: 'completed', exitCode: 0, plannedTrials: 1516,
    completedTrials: 1516, remainingTrials: 0, runtimeInvoked: true,
    files: { runReport: 'C:/runs/full/run-report.json' },
    summary: { passed: true, hadExecutionErrors: false, evals: ids.map(evalName => ({ evalName })) },
    runProvenance: {
      schema: 'hve.vally-run-provenance', version: 2,
      source: {
        declaredSourcePin: { sourceInventory: { upstreamSha: 'a'.repeat(40) } },
        sourceInventory: { sha256: 'b'.repeat(64) }, manifest: { sha256: 'c'.repeat(64) },
        bundleReport: { sha256: 'd'.repeat(64) },
        verification: { sourcePinValid: true },
        integrity: { unchanged: true, changedPaths: [], errors: [] },
      },
      suite: {
        fullBundle: { plannedTrialCount: 1516, expectedItemIds: ids },
        selected: { plannedTrialCount: 1516, selectionIsFullBundle: true, omittedItemIds: [], expectedTrials: ids },
      },
      execution: { fullSuiteExecution: true, actualTrials: ids },
    },
  };

  const projected = compactCliSummary(report, 'e'.repeat(64));
  const serialized = JSON.stringify(projected);
  assert.ok(serialized.length < 2048, `stdout projection should be bounded, got ${serialized.length} chars`);
  assert.equal(projected.runProvenance.schema, 'hve.vally-run-provenance');
  assert.equal(projected.runProvenance.source.upstreamSha, 'a'.repeat(40));
  assert.equal(projected.runProvenance.source.sourceInventorySha256, 'b'.repeat(64));
  assert.equal(projected.runProvenance.suite.selectedTrialCount, 1516);
  assert.equal(projected.runProvenance.suite.fullBundleTrialCount, 1516);
  assert.equal(projected.runProvenance.execution.fullSuiteExecution, true);
  assert.deepEqual(projected.runProvenance.source.integrity, {
    unchanged: true, changedPathCount: 0, errorCount: 0,
  });
  assert.deepEqual(projected.savedReport, {
    path: 'C:/runs/full/run-report.json', sha256: 'e'.repeat(64),
  });
  assert.ok(!serialized.includes('planned-trial-0'));
  assert.ok(!serialized.includes('expectedTrials'));
});

test('runner accepts explicit medium effort and rejects invalid or duplicate effort', () => {
  assert.equal(parseRunnerArgs(['--bundle', 'bundle', '--output', 'output', '--reasoning-effort', 'medium']).reasoningEffort, 'medium');
  assert.throws(() => parseRunnerArgs(['--bundle', 'bundle', '--output', 'output', '--reasoning-effort', 'invalid']), /Unsupported reasoning/);
  assert.throws(() => parseRunnerArgs(['--bundle', 'bundle', '--output', 'output', '--reasoning-effort', 'medium', '--reasoning-effort', 'max']), /duplicate/);
});
