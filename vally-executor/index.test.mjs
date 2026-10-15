import { VALLY_AMBIENT_CONFIG_ARGS, resolveSkillIsolation } from './skill-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, mkdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createExecutorRegistry, loadExecutorPlugin, runEval, computeMetrics, gradeTrajectory } from '@microsoft/vally';
import { buildAppServerArgs, CodexExecutor, parseConfig, normalizeEvent } from './index.mjs';
import { inspectBundle } from './plan.mjs';

test('Vally loads the executor through its real plugin loader', async () => {
  const registry = createExecutorRegistry();
  await loadExecutorPlugin(new URL('./index.mjs', import.meta.url).pathname.replace(/^\/([A-Z]:)/i, '$1'), registry);
  assert.equal(registry.get('codex').supportsMultiTurn, true);
  assert.equal(registry.get('codex').supportsSimulation, false);
});

test('Vally simulation is rejected before isolation or app-server setup', async () => {
  let launches = 0;
  let isolationChecks = 0;
  const executor = new CodexExecutor({ appServerFactory: () => { launches++; throw new Error('must not launch'); },
    skillIsolationResolver: async () => { isolationChecks++; throw new Error('must not inspect isolation'); } });
  assert.equal(executor.supportsSimulation, false);
  await assert.rejects(executor.execute({ name: 'simulation-gate', prompt: 'fixture', simulation: {} }, {
    workDir: process.cwd(), timeout: 1000,
  }), /does not support Vally simulation/);
  assert.equal(launches, 0);
  assert.equal(isolationChecks, 0);
});

test('eval-level MCP is disabled by default and requires an explicit constructor opt-in', async () => {
  let launches = 0, isolationChecks = 0;
  const server = { fixture: { type: 'stdio', command: 'node' } };
  const disabled = new CodexExecutor({ appServerFactory: () => { launches++; throw new Error('must not launch'); },
    skillIsolationResolver: async () => { isolationChecks++; throw new Error('must not inspect isolation'); } });
  assert.equal(disabled.supportsEvalMcp, false);
  await assert.rejects(disabled.execute({ prompt: 'must not execute' }, {
    workDir: process.cwd(), timeout: 1000, mcpServers: server,
  }), /eval-level MCP support is disabled/);
  assert.equal(launches, 0);
  assert.equal(isolationChecks, 0);
  assert.equal(new CodexExecutor({ enableEvalMcp: true }).supportsEvalMcp, true);
  assert.throws(() => new CodexExecutor({ enableEvalMcp: 'yes' }), /enableEvalMcp must be a boolean/);
});

test('SSE and simulation fail closed before app-server setup', async () => {
  let setupCalls = 0;
  const executor = new CodexExecutor({ enableEvalMcp: true,
    appServerFactory: () => { setupCalls++; throw new Error('must not launch'); },
    skillIsolationResolver: async () => { setupCalls++; throw new Error('must not inspect isolation'); } });
  await assert.rejects(executor.execute({ prompt: 'SSE is not wired' }, {
    workDir: process.cwd(), timeout: 1000, mcpServers: {
      events: { type: 'sse', url: 'https://mcp.example.test/events' },
    },
  }), /does not support Vally SSE MCP server 'events'/);
  await assert.rejects(executor.execute({ prompt: 'simulation is unsupported', simulation: {} }, {
    workDir: process.cwd(), timeout: 1000,
  }), /does not support Vally simulation/);
  assert.equal(setupCalls, 0);
});

test('configuration rejects unsafe or unknown options', () => {
  assert.throws(() => parseConfig({ sandbox: 'danger-full-access' }));
  assert.throws(() => parseConfig({ bypassHooks: true }));
  assert.equal(parseConfig().reasoningEffort, 'max');
});

test('app-server process receives the exact process-local skill and project-doc overrides', () => {
  const overrides = ['-c', 'skills.config=[{path="C:/fixture/.agents/skills/only/SKILL.md",enabled=true}]',
    '-c', 'project_doc_max_bytes=0'];
  assert.deepEqual(buildAppServerArgs(overrides), ['app-server', '--stdio', ...overrides]);
  assert.throws(() => buildAppServerArgs(['-c', 42]), /array of strings/);
});

test('native tool failure and usage remain measurable by Vally', () => {
  const events = [
    ...normalizeEvent({ type: 'item.started', item: { type: 'command_execution', id: 'x', command: 'pytest' } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.completed', item: { type: 'command_execution', id: 'x', exit_code: 1, aggregated_output: 'failed' } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'turn.completed', usage: { input_tokens: 15, output_tokens: 5, cached_input_tokens: 10 } }, 0, 'gpt-6-luna'),
  ];
  assert.equal(events[1].data.success, false);
  const metrics = computeMetrics(events);
  assert.equal(metrics.toolCallCount, 1);
  assert.equal(metrics.tokenUsage.totalTokens, 20);
  assert.equal(metrics.tokenUsage.cacheReadTokens, 10);
});

test('tool-calls grader matches scanner JSON in raw command stdout', async () => {
  const scannerJson = '{"status":"completed","phase":"Validate","findings":[]}';
  const events = [
    ...normalizeEvent({ type: 'item.started', item: { type: 'command_execution', id: 'scan-1',
      command: 'python scan.py' } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.completed', item: { type: 'command_execution', id: 'scan-1',
      command: 'python scan.py', exit_code: 0, status: 'completed', aggregated_output: scannerJson } }, 0, 'gpt-6-luna'),
  ];
  assert.equal(events[1].data.result, scannerJson);
  assert.equal(events[1].data.exit_code, 0);
  assert.equal(events[1].data.status, 'completed');
  const trajectory = { id: 'scanner-fixture', stimulus: { name: 'scanner-fixture' }, events,
    output: '', workDir: process.cwd(), metadata: {}, metrics: computeMetrics(events) };
  const graders = [{ type: 'tool-calls', config: { required: [{ name: '^exec_command$',
    command: '^python scan\\.py$', result: '"status":"completed"' }] } }];
  const actual = await gradeTrajectory(trajectory, graders);
  assert.equal(actual.passed, true, JSON.stringify(actual));

  // The former { output, exit_code } wrapper JSON-escaped scanner stdout and
  // made the source's raw JSON result pattern fail.
  const legacyTrajectory = { ...trajectory, events: events.map(event => event.type === 'tool_result'
    ? { ...event, data: { ...event.data, result: { output: scannerJson, exit_code: 0 } } } : event) };
  const legacy = await gradeTrajectory(legacyTrajectory, graders);
  assert.equal(legacy.passed, false, JSON.stringify(legacy));
});

test('native web_search items become measurable tool calls with their observed result payload', () => {
  const events = [
    ...normalizeEvent({ type: 'item.started', item: { type: 'web_search', id: 'web-1', action: { type: 'other' } } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.completed', item: { type: 'web_search', id: 'web-1', query: 'fixture query',
      action: { type: 'search', queries: ['fixture query'] }, results: [{ title: 'fixture result' }] } }, 0, 'gpt-6-luna'),
  ];
  assert.equal(events[0].data.toolName, 'web_search');
  assert.equal(events[0].data.toolCallId, 'web-1');
  assert.equal(events[1].data.success, true);
  assert.equal(events[1].data.result.results[0].title, 'fixture result');
  const metrics = computeMetrics(events);
  assert.equal(metrics.toolCallCount, 1);
  assert.equal(metrics.toolCallBreakdown.web_search, 1);
});

test('native dynamic tool calls preserve the exact tool, arguments, and completion evidence', () => {
  const events = [
    ...normalizeEvent({ type: 'item.started', item: { type: 'dynamic_tool_call', id: 'dynamic-1',
      tool: 'lookup', namespace: 'fixture', arguments: { key: 'value' }, status: 'inProgress' } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.completed', item: { type: 'dynamic_tool_call', id: 'dynamic-1',
      tool: 'lookup', namespace: 'fixture', arguments: { key: 'value' }, status: 'failed', success: false,
      durationMs: 12, contentItems: [{ type: 'inputText', text: 'denied' }] } }, 0, 'gpt-6-luna'),
  ];
  assert.equal(events[0].type, 'tool_call');
  assert.equal(events[0].data.toolName, 'lookup');
  assert.equal(events[0].data.namespace, 'fixture');
  assert.deepEqual(events[0].data.arguments, { key: 'value' });
  assert.equal(events[1].type, 'tool_result');
  assert.equal(events[1].data.success, false);
  assert.equal(events[1].data.result.status, 'failed');
  assert.deepEqual(events[1].data.result.contentItems, [{ type: 'inputText', text: 'denied' }]);
  assert.equal(computeMetrics(events).toolCallBreakdown.lookup, 1);
});

class FakeCodex extends CodexExecutor {
  calls = [];
  constructor(options = {}) {
    super({ navigationContext: options.navigationContext ?? false, skillIsolationResolver: options.skillIsolationResolver ?? (async ({ workDir, skills = [] }) => {
      const paths = skills.map(skill => resolve(join(workDir, '.agents', 'skills', skill.name, 'SKILL.md')));
      return { mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS],
        expectedPaths: paths, enabledPaths: paths, disabledSkills: 0, elapsedMs: 0 };
    }) });
  }
  async run(binary, args, prompt, options, timeout) {
    this.calls.push({ binary, args, prompt, timeout, workDir: options.workDir });
    return [{ type: 'thread.started', thread_id: 'fixture-session' }, { type: 'turn.started' },
      { type: 'item.completed', item: { type: 'agent_message', text: `result:${prompt}` } },
      { type: 'turn.completed', usage: { input_tokens: 3, output_tokens: 2 } }];
  }
}

test('real Vally pipeline preserves multi-turn stimulus and workspace callbacks without a model', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-contract-'));
  const executor = new FakeCodex();
  let result;
  try {
    const stimulus = { name: 'upstream-fixture', prompt: 'one', turns: ['one', 'two'], graders: [
      { type: 'output-matches', name: 'original-grader', config: { pattern: '^result:two$' } },
    ] };
    result = await runEval({ prompt: stimulus.prompt, stimulus, skills: [], executor, workDir: root, timeout: 5000 });
    assert.deepEqual(result.trajectory.stimulus, stimulus);
    assert.equal(result.trajectory.output, 'result:two');
    assert.deepEqual(result.trajectory.metadata.skillIsolation, {
      verified: true, expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
      mcpIsolation: { verified: true, disabledServerCount: 0, advertisedToolCount: 0, exclusiveToolInventoryVerified: false },
    });
    assert.equal(result.trajectory.events.filter(e => e.type === 'user_message').length, 2);
    assert.equal(executor.calls[0].args.includes('resume'), false);
    assert.ok(executor.calls[0].args.includes('skills.config=[]'));
    assert.ok(executor.calls[0].args.includes('project_doc_max_bytes=0'));
    assert.equal(executor.calls[1].args.includes('fixture-session'), true);
    assert.equal(result.trajectory.metrics.tokenUsage.totalTokens, 10);
    const grade = await gradeTrajectory(result.trajectory, stimulus.graders);
    assert.equal(grade.passed, true);
    const failed = await gradeTrajectory({ ...result.trajectory, output: 'wrong' }, stimulus.graders);
    assert.equal(failed.passed, false);
  } finally { await result?.cleanup(); await rm(root, { recursive: true, force: true }); }
});

test('failed turn is rejected rather than presented to graders as successful output', async () => {
  const executor = new FakeCodex();
  executor.run = async () => [{ type: 'thread.started', thread_id: 'x' }, { type: 'turn.failed', error: { message: 'fixture failure' } }];
  await assert.rejects(executor.execute({ name: 'failure', prompt: 'one' }, { timeout: 1000, workDir: '.' }), /did not complete/);
});

test('executor accepts real resolver output and forwards all ambient isolation switches', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-resolver-contract-'));
  let closed = 0;
  const executor = new FakeCodex({ skillIsolationResolver: options => resolveSkillIsolation({ ...options,
    clientFactory: () => ({
      async initialize() {},
      async request(method) {
        if (method === 'mcpServerStatus/list') return { data: [] };
        assert.equal(method, 'skills/list');
        return { data: [{ cwd: root, skills: [], errors: [] }] };
      },
      async shutdown() { closed++; },
    }),
  }) });
  try {
    await executor.execute({ name: 'real-resolver', prompt: 'fixture' }, { workDir: root, timeout: 2000, skills: [] });
    assert.equal(closed, 2);
    const args = executor.calls[0].args;
    const offset = args.indexOf('project_doc_max_bytes=0') + 1;
    assert.deepEqual(args.slice(offset, offset + VALLY_AMBIENT_CONFIG_ARGS.length), [...VALLY_AMBIENT_CONFIG_ARGS]);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('executor rejects missing or changed ambient isolation switches before native execution', async () => {
  for (const suffix of [[], ['-c', 'features.apps=true', ...VALLY_AMBIENT_CONFIG_ARGS.slice(2)]]) {
    const executor = new FakeCodex({ skillIsolationResolver: async () => ({
      mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...suffix],
      expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
    }) });
    await assert.rejects(executor.execute({ name: 'invalid-isolation', prompt: 'fixture' }, {
      workDir: process.cwd(), timeout: 2000, skills: [],
    }), /invalid process-local config/);
    assert.equal(executor.calls.length, 0);
  }
});

test('executor requires MCP catalog proof and the exact verified server-disable arguments', async () => {
  const base = ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS];
  const proof = { verified: true, disabledServerNames: ['synthetic_server'], advertisedToolCount: 0 };
  for (const [mcpIsolation, suffix] of [[undefined, []], [{ ...proof, verified: false }, []],
    [{ ...proof, advertisedToolCount: 1 }, []], [proof, []],
    [proof, ['-c', 'mcp_servers.synthetic_server.enabled=true']]]) {
    const executor = new FakeCodex({ skillIsolationResolver: async () => ({
      configArgs: [...base, ...suffix], mcpIsolation,
      expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
    }) });
    await assert.rejects(executor.execute({ name: 'mcp-proof', prompt: 'fixture' }, {
      workDir: process.cwd(), timeout: 2000, skills: [],
    }), /incomplete catalog verification|invalid process-local config/);
    assert.equal(executor.calls.length, 0);
  }
  const executor = new FakeCodex({ skillIsolationResolver: async () => ({
    configArgs: [...base, '-c', 'mcp_servers.synthetic_server.enabled=false'], mcpIsolation: proof,
    expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
  }) });
  const result = await executor.execute({ name: 'mcp-proof', prompt: 'fixture' }, {
    workDir: process.cwd(), timeout: 2000, skills: [],
  });
  assert.equal(executor.calls.length, 1);
  assert.equal(result.metadata.skillIsolation.mcpIsolation.disabledServerCount, 1);
  assert.equal(result.metadata.skillIsolation.mcpIsolation.exclusiveToolInventoryVerified, false);
});

test('skill isolation consumes the same hard deadline as native execution', async () => {
  let receivedTimeout;
  let receivedEnv;
  let launched = false;
  const executionEnv = { CODEX_TEST_SENTINEL: 'same-native-env' };
  const executor = new CodexExecutor({ skillIsolationResolver: async ({ timeoutMs, env }) => {
    receivedTimeout = timeoutMs;
    receivedEnv = env;
    await new Promise(resolve => setTimeout(resolve, 25));
    return { configArgs: [] };
  } });
  executor.run = async () => { launched = true; return []; };
  await assert.rejects(executor.execute({ name: 'deadline', prompt: 'no call' }, {
    workDir: '.', timeout: 5, skills: [], env: executionEnv,
  }), /hard deadline exhausted during skill isolation/);
  assert.equal(receivedTimeout, 5);
  assert.equal(receivedEnv, executionEnv);
  assert.equal(launched, false);
});

test('executor rejects isolation evidence that does not match the selected enabled skill paths', async () => {
  let launched = false;
  const executor = new CodexExecutor({ skillIsolationResolver: async () => ({
    mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS],
    expectedPaths: [join(process.cwd(), '.agents', 'skills', 'selected', 'SKILL.md')],
    enabledPaths: [], disabledSkills: 2, elapsedMs: 1,
  }) });
  executor.run = async () => { launched = true; return []; };
  await assert.rejects(executor.execute({ name: 'mismatch', prompt: 'no call' }, {
    workDir: process.cwd(), timeout: 1000, skills: [],
  }), /exactly the selected skills/);
  assert.equal(launched, false);
});

test('process transport reads stdin and rejects malformed JSONL and nonzero exits', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-transport-'));
  const script = join(root, 'fake-codex.cjs');
  const executor = new CodexExecutor();
  try {
    await writeFile(script, `let input=''; process.stdin.on('data', c => input+=c); process.stdin.on('end', () => console.log(JSON.stringify({type:'fixture',prompt:input})));`);
    const records = await executor.run(process.execPath, [script], 'exact prompt', { workDir: root }, 2000);
    assert.equal(records[0].prompt, 'exact prompt');
    await writeFile(script, `process.stdout.write('invalid json');`);
    await assert.rejects(executor.run(process.execPath, [script], '', { workDir: root }, 2000), /Invalid Codex JSONL/);
    await writeFile(script, `process.stderr.write('fixture failure'); process.exit(2);`);
    await assert.rejects(executor.run(process.execPath, [script], '', { workDir: root }, 2000), /exited 2/);
    assert.equal(executor.children.size, 0);
  } finally { await executor.shutdown(); await rm(root, { recursive: true, force: true }); }
});

test('Vally stages complete skill resources for Codex before execution', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-skills-'));
  const source = join(root, 'converted-skill');
  const executor = new FakeCodex();
  let result;
  try {
    await mkdir(join(source, 'scripts'), { recursive: true });
    await writeFile(join(source, 'SKILL.md'), '---\nname: converted-skill\ndescription: Fixture skill\n---\nUse scripts/helper.py.\n');
    await writeFile(join(source, 'scripts', 'helper.py'), 'print("fixture")\n');
    const fakeRun = executor.run.bind(executor);
    executor.run = async (binary, args, prompt, options, timeout) => {
      assert.equal(await readFile(join(options.workDir, '.agents/skills/converted-skill/scripts/helper.py'), 'utf8'), 'print("fixture")\n');
      assert.equal(options.skills[0].name, 'converted-skill');
      return fakeRun(binary, args, prompt, options, timeout);
    };
    result = await runEval({ prompt: 'fixture', skills: [], executor, workDir: root, baseDir: root,
      environment: { skills: [source], files: [{ src: source, dest: '.agents/skills/converted-skill' }] }, timeout: 5000 });
    assert.equal(result.trajectory.endReason, 'completed');
  } finally { await result?.cleanup(); await rm(root, { recursive: true, force: true }); }
});

test('resume fails closed when Codex changes thread identity', async () => {
  const executor = new FakeCodex();
  await assert.rejects(executor.execute({ name: 'resume', prompt: 'one' }, {
    workDir: '.', timeout: 1000, sessionID: 'different-session',
  }), /different thread/);
});

test('partial raw logs and callbacks survive a failed subprocess', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-partial-'));
  const executor = new CodexExecutor();
  const script = join(root, 'fixture.cjs');
  const observed = [];
  try {
    await writeFile(script, `console.log(JSON.stringify({type:'turn.started'})); setTimeout(() => process.exit(2), 80);`);
    await assert.rejects(executor.run(process.execPath, [script], '', {
      workDir: root, sessionLog: { rootDir: root }, onRawEvent: raw => observed.push(raw),
    }, 2000), /exited 2/);
    assert.equal(observed.length, 1);
    assert.equal(observed[0].type, 'turn.started');
    assert.ok(Number.isFinite(Date.parse(observed[0].receivedAt)));
    assert.equal((await readFile(join(root, 'events.jsonl'), 'utf8')).trim(), '{"type":"turn.started"}');
  } finally { await executor.shutdown(); await rm(root, { recursive: true, force: true }); }
});

test('timeout rejects within a bounded interval and closes the owned process', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-timeout-'));
  const executor = new CodexExecutor();
  const script = join(root, 'fixture.cjs');
  const began = Date.now();
  try {
    await writeFile(script, 'setInterval(() => {}, 10000);');
    await assert.rejects(executor.run(process.execPath, [script], '', { workDir: root }, 80), /timed out/);
    assert.ok(Date.now() - began < 4000);
    assert.equal(executor.children.size, 0);
  } finally { await executor.shutdown(); await rm(root, { recursive: true, force: true }); }
});

test('native collaboration and file changes contribute to Vally tool metrics', () => {
  const events = [
    ...normalizeEvent({ type: 'item.started', item: { type: 'collab_tool_call', id: 'a', tool: 'spawn_agent', prompt: 'review' } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.completed', item: { type: 'collab_tool_call', id: 'a', tool: 'spawn_agent', status: 'completed' } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.started', item: { type: 'file_change', id: 'b', changes: [{ path: 'x.py', kind: 'update' }] } }, 0, 'gpt-6-luna'),
    ...normalizeEvent({ type: 'item.completed', item: { type: 'file_change', id: 'b', status: 'failed' } }, 0, 'gpt-6-luna'),
  ];
  assert.equal(computeMetrics(events).toolCallCount, 2);
  assert.equal(events[3].data.success, false);
});

test('offline bundle inspection uses Vally to preserve configured trial count', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-plan-'));
  try {
    await mkdir(join(root, 'evals'));
    await writeFile(join(root, '.vally.yaml'), 'paths:\n  evals: [evals]\n');
    await writeFile(join(root, 'evals', 'eval.yaml'), 'name: fixture\ntype: capability\ndefaults:\n  runs: 3\n  executor: codex\nstimuli:\n  - name: original\n    prompt: fixture\n    graders:\n      - type: output-matches\n        config:\n          pattern: fixture\n');
    const report = await inspectBundle(root);
    assert.equal(report.modelCalls, 0);
    assert.equal(report.plannedTrials, 3);
    assert.equal(report.valid, true);
    assert.equal(report.runtimeReady, false);
  } finally { await rm(root, { recursive: true, force: true }); }
});


test('observed receipt timestamps survive normalization and establish elapsed time', () => {
  const start = normalizeEvent({ type: 'turn.started', receivedAt: '2026-10-14T00:00:00.000Z' }, 0, 'gpt-6-luna')[0];
  const end = normalizeEvent({ type: 'turn.completed', receivedAt: '2026-10-14T00:00:02.500Z' }, 0, 'gpt-6-luna')[0];
  assert.equal(end.timestamp - start.timestamp, 2500);
  assert.equal(JSON.parse(JSON.stringify(start)).timestamp, '2026-10-14T00:00:00.000Z');
});

test('fixture instructions use native developer guidance without changing user prompts', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-context-'));
  const executor = new FakeCodex();
  try {
    await mkdir(join(root, '.github'));
    await writeFile(join(root, '.github/copilot-instructions.md'), 'Return the conservative status NOT_ASSESSED.');
    const result = await executor.execute({ prompt: 'Original prompt' }, { workDir: root, timeout: 5000, skills: [] });
    assert.equal(executor.calls[0].prompt, 'Original prompt');
    const delivered = JSON.parse(executor.calls[0].args.find(arg => arg.startsWith('developer_instructions=')).slice('developer_instructions='.length));
    assert.ok(delivered.startsWith('Response style for HVE tasks:'));
    assert.ok(delivered.endsWith('Return the conservative status NOT_ASSESSED.'));
    assert.equal(result.metadata.responseStyleVersion, '2');
    assert.equal(result.metadata.fixtureInstructionsDelivered, true);
    assert.ok(executor.calls[0].args.includes('project_doc_max_bytes=0'));
  } finally { await executor.shutdown(); await rm(root, { recursive: true, force: true }); }
});


test('optional navigation context teaches discovery without rewriting the task', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-navigation-'));
  const executor = new FakeCodex({ navigationContext: true });
  try {
    const result = await executor.execute({ prompt: 'Original task' }, { workDir: root, timeout: 5000, skills: [] });
    const guidance = executor.calls[0].args.find(arg => arg.startsWith('developer_instructions='));
    assert.ok(guidance.includes('rg --files --hidden'));
    assert.ok(guidance.includes('not a complete repository inventory'));
    assert.equal(executor.calls[0].prompt, 'Original task');
    assert.equal(result.metadata.navigationContext, true);
  } finally { await executor.shutdown(); await rm(root, { recursive: true, force: true }); }
});

 test('response style is always delivered without navigation or fixture instructions', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-response-style-'));
  const executor = new FakeCodex();
  try {
    const result = await executor.execute({ turns: [{ prompt: 'First original prompt' }, { prompt: 'Second original prompt' }] }, { workDir: root, timeout: 5000, skills: [] });
    for (const call of executor.calls) {
      const guidance = call.args.find(arg => arg.startsWith('developer_instructions='));
      assert.ok(guidance.includes('State the requested conclusion explicitly'));
      assert.ok(!guidance.includes('HVE navigation:'));
    }
    assert.deepEqual(executor.calls.map(call => call.prompt), ['First original prompt', 'Second original prompt']);
    assert.equal(result.metadata.responseStyleVersion, '2');
    assert.equal(result.metadata.fixtureInstructionsDelivered, false);
  } finally { await executor.shutdown(); await rm(root, { recursive: true, force: true }); }
});
