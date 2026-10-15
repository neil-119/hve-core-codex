import { VALLY_AMBIENT_CONFIG_ARGS } from './skill-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { executeAppServer } from './app-server-execution.mjs';
import { CodexExecutor, normalizeEvent } from './index.mjs';
import { runEval, gradeTrajectory } from '@microsoft/vally';
import { settleAppServerWorkspace } from './settlement.mjs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

class Client extends EventEmitter {
  prompts = []; closed = false; threadStartParams; requests = [];
  constructor(usageByTurn = [], itemNotificationsByTurn = [], { mcpCatalog } = {}) {
    super(); this.usageByTurn = usageByTurn; this.itemNotificationsByTurn = itemNotificationsByTurn;
    this.mcpCatalog = mcpCatalog;
  }
  async request(method, params) {
    this.requests.push({ method, params });
    if (method === 'thread/start') {
      this.threadStartParams = params;
      return { thread: { id: 'root', sessionId: 'root-session' }, model: 'gpt-6-luna', reasoningEffort: 'max' };
    }
    if (method === 'mcpServerStatus/list') {
      if (!this.mcpCatalog) throw new Error('Unexpected MCP catalog request');
      return typeof this.mcpCatalog === 'function' ? this.mcpCatalog(params) : this.mcpCatalog;
    }
    if (method === 'turn/start') {
      this.prompts.push(params.input[0].text);
      const id = `turn-${this.prompts.length}`;
      setImmediate(() => {
        for (const usage of this.usageByTurn[this.prompts.length - 1] ?? [])
          this.emit('notification', { method: 'thread/tokenUsage/updated', params: {
            threadId: usage.threadId, tokenUsage: { total: usage.total },
          } });
        for (const notification of this.itemNotificationsByTurn[this.prompts.length - 1] ?? [])
          this.emit('notification', notification);
        this.emit('notification', { method: 'item/completed', params: { threadId: 'root', turnId: id,
          item: { id: `message-${id}`, type: 'agentMessage', text: params.input[0].text } } });
        this.emit('notification', { method: 'turn/completed', params: { threadId: 'root', turn: { id, status: 'completed', items: [] } } });
      });
      return { turn: { id } };
    }
    throw new Error(`Unexpected request ${method}`);
  }
  async shutdown() { this.closed = true; }
}
const options = { timeout: 500, model: 'gpt-6-luna', workDir: process.cwd() };
const config = { sandbox: 'workspace-write', reasoningEffort: 'max' };

function settlementProof(children = [], { rootId = 'root', sessionId = 'root-session' } = {}) {
  const snapshot = () => ({
    root: { threadId: rootId, sessionId, status: 'idle', latestTurnId: 'root-turn', latestTurnStatus: 'completed',
      model: 'gpt-6-luna', reasoningEffort: 'max', agentRole: null },
    descendants: children.map(child => ({
      threadId: child.threadId,
      parentThreadId: child.parentThreadId ?? rootId,
      sessionId: child.sessionId ?? sessionId,
      model: child.model ?? null,
      reasoningEffort: child.reasoningEffort ?? null,
      agentRole: child.agentRole ?? null,
      status: 'idle',
      turns: [{ turnId: `${child.threadId}-turn`, status: 'completed' }],
    })),
  });
  return { protocol: 'codex-app-server-v2', rootThreadId: rootId, rootTerminalStatus: 'completed', settled: true,
    interrupted: false, snapshots: [snapshot(), snapshot()] };
}

test('app-server execution awaits settlement and host grading before the next original turn', async () => {
  const client = new Client();
  const boundaries = [];
  const result = await executeAppServer({ name: 'multi', turns: [{ prompt: 'first' }, { prompt: 'second' }] },
    { ...options, timeout: 2000, onTurnComplete: async completion => {
      assert.equal(boundaries.length, client.prompts.length);
      boundaries.at(-1).completion = completion;
    } }, config, { client, normalizeEvent,
      settleWorkspace: async (_client, id, limits) => { assert.equal(id, 'root'); assert.ok(limits.timeoutMs > 0);
        boundaries.push({}); return { interrupted: false }; } });
  assert.deepEqual(client.prompts, ['first', 'second']);
  assert.equal(Object.hasOwn(client.threadStartParams, 'experimentalRawEvents'), false);
  assert.deepEqual(boundaries.map(value => value.completion), [
    { turn: 0, status: 'completed', final: false }, { turn: 1, status: 'completed', final: true },
  ]);
  assert.equal(result.output, 'second'); assert.equal(client.closed, true);
});

test('MCP startup is awaited before a model turn; a stuck startup obeys the hard deadline', async () => {
  for (const stuck of [false, true]) {
    let catalogs = 0;
    const client = new Client([], [], { mcpCatalog: () => ({ data: [{ name: 'fixture',
      runtimeStatus: ++catalogs === 1 || stuck ? 'starting' : 'connected',
      tools: { lookup: { name: 'lookup' } }, toolsError: null }], nextCursor: null }) });
    const operation = executeAppServer({ prompt: 'hello' }, { ...options, timeout: stuck ? 30 : 2000 }, config,
      { client, normalizeEvent, requestedMcpServers: { fixture: { command: 'node', args: ['fixture.mjs'] } },
        disabledMcpServerNames: [], settleWorkspace: async () => ({ interrupted: false }) });
    if (stuck) {
      await assert.rejects(operation, /catalog verification deadline exhausted/);
      assert.equal(client.prompts.length, 0);
    } else {
      await operation;
      assert.equal(catalogs, 2);
      assert.deepEqual(client.prompts, ['hello']);
    }
    assert.equal(client.closed, true);
  }
});

test('opted-in eval MCP is enabled in the exact thread config and verified per-thread before the first turn', async () => {
  const disabledNames = ['fixture', 'ambient', 'node_repl'];
  const catalog = { data: [
    { name: 'fixture', runtimeStatus: 'connected', tools: {
      lookup: { name: 'lookup', description: 'Fixture lookup' },
    }, toolsError: null },
    { name: 'ambient', runtimeStatus: 'disabled', tools: {}, toolsError: null },
  ], nextCursor: null };
  const mcpStart = { id: 'mcp-call-1', type: 'mcpToolCall', server: 'fixture', tool: 'lookup',
    status: 'inProgress', arguments: { key: 'value' } };
  const mcpComplete = { ...mcpStart, status: 'completed', result: {
    content: [{ type: 'text', text: 'found value' }], structuredContent: { value: 7 }, meta: { source: 'fixture' },
  } };
  const client = new Client([], [[
    { method: 'item/started', params: { threadId: 'root', turnId: 'turn-1', item: mcpStart } },
    { method: 'item/completed', params: { threadId: 'root', turnId: 'turn-1', item: mcpComplete } },
  ]], { mcpCatalog: params => {
    assert.deepEqual(params, { threadId: 'root', limit: 64, detail: 'toolsAndAuthOnly' });
    return catalog;
  } });
  const isolationArgs = ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS,
    ...disabledNames.flatMap(name => ['-c', `mcp_servers.${name}.enabled=false`])];
  let executorOptions;
  const executor = new CodexExecutor({ enableEvalMcp: true, appServerFactory: (_binary, options) => {
    executorOptions = options; return client;
  }, settleWorkspace: async () => ({ interrupted: false }), skillIsolationResolver: async () => ({
    configArgs: isolationArgs, mcpIsolation: { verified: true, disabledServerNames: disabledNames, advertisedToolCount: 0 },
    expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
  }) });
  const result = await executor.execute({ prompt: 'use the fixture MCP tool' }, {
    workDir: process.cwd(), timeout: 5000, skills: [],
    mcpServers: { fixture: { type: 'stdio', command: 'node', args: ['fixture-server.mjs'] } },
  });

  assert.deepEqual(client.threadStartParams.config.mcp_servers, {
    fixture: { command: 'node', args: ['fixture-server.mjs'], enabled: true },
    ambient: { enabled: false },
    node_repl: { enabled: false },
  });
  assert.deepEqual(executorOptions.skillIsolationArgs.slice(0, -2), isolationArgs);
  assert.equal(executorOptions.skillIsolationArgs.at(-2), '-c');
  assert.ok(executorOptions.skillIsolationArgs.at(-1).includes('Response style for HVE tasks:'));
  assert.equal(client.threadStartParams.config.mcp_servers.fixture.enabled, true,
    'an explicitly requested server wins a same-name ambient disable');
  assert.deepEqual(client.requests.map(request => request.method), [
    'thread/start', 'mcpServerStatus/list', 'turn/start',
  ]);
  assert.deepEqual(result.metadata.nativeMcpCatalog, { verified: true, threadId: 'root', servers: [
    { name: 'fixture', nativeToolNames: ['lookup'] },
  ] }, 'disabled ambient names and tools stay out of returned catalog metadata');
  const toolEvents = result.events.filter(event => event.type === 'tool_call' || event.type === 'tool_result');
  assert.deepEqual(toolEvents.map(event => [event.type, event.data.toolName, event.data.nativeMcp]), [
    ['tool_call', 'fixture-lookup', { server: 'fixture', tool: 'lookup' }],
    ['tool_result', 'fixture-lookup', { server: 'fixture', tool: 'lookup' }],
  ]);
  assert.deepEqual(toolEvents[1].data.result, mcpComplete.result);
});

test('eval MCP catalog verification rejects missing, unexpected, disconnected, or errored servers before a turn', async () => {
  const requested = { fixture: { type: 'stdio', command: 'node' } };
  const cases = [
    ['missing server', { data: [], nextCursor: null }, /does not exactly match/, []],
    ['unexpected ambient server', { data: [{ name: 'ambient', runtimeStatus: 'connected', tools: {}, toolsError: null }], nextCursor: null }, /unknown server/, []],
    ['disconnected server', { data: [{ name: 'fixture', runtimeStatus: 'failed', tools: {}, toolsError: null }], nextCursor: null }, /not connected/, []],
    ['tool discovery error', { data: [{ name: 'fixture', runtimeStatus: 'connected', tools: {}, toolsError: 'failed to list tools' }], nextCursor: null }, /tool catalog/, []],
    ['known ambient server reappeared connected', { data: [
      { name: 'fixture', runtimeStatus: 'connected', tools: {}, toolsError: null },
      { name: 'ambient', runtimeStatus: 'connected', tools: {}, toolsError: null },
    ], nextCursor: null }, /reappeared instead of remaining disabled/, ['ambient']],
    ['known ambient server has tools while disabled', { data: [
      { name: 'fixture', runtimeStatus: 'connected', tools: {}, toolsError: null },
      { name: 'ambient', runtimeStatus: 'disabled', tools: { lookup: { name: 'lookup' } }, toolsError: null },
    ], nextCursor: null }, /reappeared instead of remaining disabled/, ['ambient']],
  ];
  for (const [label, mcpCatalog, error, disabledServerNames] of cases) {
    const client = new Client([], [], { mcpCatalog });
    const isolationArgs = ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS,
      ...disabledServerNames.flatMap(name => ['-c', `mcp_servers.${name}.enabled=false`])];
    const executor = new CodexExecutor({ enableEvalMcp: true, appServerFactory: () => client,
      settleWorkspace: async () => ({ interrupted: false }), skillIsolationResolver: async () => ({
        configArgs: isolationArgs,
        mcpIsolation: { verified: true, disabledServerNames, advertisedToolCount: 0 },
        expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 0,
      }) });
    await assert.rejects(executor.execute({ prompt: label }, { workDir: process.cwd(), timeout: 5000,
      skills: [], mcpServers: requested }), error, label);
    assert.equal(client.prompts.length, 0, label);
    assert.equal(client.closed, true, label);
  }
});

test('descendant working-budget exhaustion returns partial trajectory and stops later turns', async () => {
  const client = new Client(); const boundaries = [];
  const result = await executeAppServer({ name: 'partial', turns: ['first', 'never'] },
    { ...options, maxAgentDurationMs: 400, onTurnComplete: async completion => boundaries.push(completion) },
    config, { client, normalizeEvent, settleWorkspace: async (_client, _id, limits) => {
      assert.ok(limits.maxAgentDurationMs > 0); return { interrupted: true };
    } });
  assert.equal(result.endReason, 'agent_timeout'); assert.deepEqual(client.prompts, ['first']);
  assert.deepEqual(boundaries, [{ turn: 0, status: 'interrupted', final: true }]);
});

test('unproven workspace settlement rejects without a grading callback', async () => {
  const client = new Client(); let callbacks = 0;
  await assert.rejects(executeAppServer({ name: 'broken', prompt: 'first' },
    { ...options, onTurnComplete: async () => callbacks++ }, config,
    { client, normalizeEvent, settleWorkspace: async () => { throw new Error('child state unknown'); } }), /child state unknown/);
  assert.equal(callbacks, 0); assert.equal(client.closed, true);
});

test('wrong effective model or hung setup cannot proceed to paid turns', async () => {
  for (const hung of [false, true]) {
    const client = new Client();
    client.request = () => hung ? new Promise(() => {}) : Promise.resolve({ thread: { id: 'root', sessionId: 'root-session' }, model: 'wrong', reasoningEffort: 'max' });
    await assert.rejects(executeAppServer({ prompt: 'never' }, { ...options, timeout: 20 }, config,
      { client, normalizeEvent, settleWorkspace: async () => ({ interrupted: false }) }), hung ? /deadline/ : /disagrees/);
    assert.deepEqual(client.prompts, []); assert.equal(client.closed, true);
  }
});

test('real Vally pipeline receives partial work through the production timeout adapter branch', async () => {
  const root = await mkdtemp(join(tmpdir(), 'hve-vally-appserver-'));
  const client = new Client();
  let isolatedArgs;
  const executor = new CodexExecutor({ appServerFactory: () => client,
    settleWorkspace: async () => ({ interrupted: true }),
    skillIsolationResolver: async () => ({ mcpIsolation: { verified: true, disabledServerNames: [], advertisedToolCount: 0 },
      configArgs: ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS],
      expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 3 }) });
  const appServerFactory = executor.appServerFactory;
  executor.appServerFactory = (binary, runOptions) => { isolatedArgs = runOptions.skillIsolationArgs; return appServerFactory(binary, runOptions); };
  let result;
  try {
    const stimulus = { name: 'partial-contract', prompt: 'first', turns: ['first', 'never'],
      graders: [{ type: 'output-matches', name: 'original-output-grader', config: { pattern: '^first$' } }] };
    result = await runEval({ prompt: stimulus.prompt, stimulus, executor, skills: [], workDir: root,
      timeout: 5000, maxAgentDurationMs: 400 });
    assert.equal(result.trajectory.endReason, 'agent_timeout');
    assert.deepEqual(client.prompts, ['first']);
    assert.deepEqual(result.trajectory.stimulus, stimulus);
    assert.equal((await gradeTrajectory(result.trajectory, stimulus.graders)).passed, true);
    assert.equal(executor.appServers.size, 0);
    assert.deepEqual(isolatedArgs.slice(0, -2), ['-c', 'skills.config=[]', '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS]);
    assert.equal(isolatedArgs.at(-2), '-c');
    assert.ok(isolatedArgs.at(-1).startsWith('developer_instructions='));
    assert.equal(result.trajectory.metadata.responseStyleVersion, '2');
    assert.deepEqual(result.trajectory.metadata.skillIsolation, {
      verified: true, expectedPaths: [], enabledPaths: [], disabledSkills: 0, elapsedMs: 3,
      mcpIsolation: { verified: true, disabledServerCount: 0, advertisedToolCount: 0, exclusiveToolInventoryVerified: false },
    });
  } finally { await result?.cleanup(); await rm(root, { recursive: true, force: true }); }
});

test('verified child usage is attributed by the child role/model and cumulative deltas span original turns', async () => {
  const child = { threadId: 'child', agentRole: 'observer', model: 'gpt-6-astra', reasoningEffort: 'high' };
  const root0 = { inputTokens: 100, outputTokens: 10, cachedInputTokens: 20 };
  const root1 = { inputTokens: 150, outputTokens: 15, cachedInputTokens: 20 };
  const child0 = { inputTokens: 80, outputTokens: 20, cachedInputTokens: 10 };
  const child1 = { inputTokens: 150, outputTokens: 25, cachedInputTokens: 10 };
  const client = new Client([
    [
      { threadId: 'root', total: root0 }, { threadId: 'root', total: root0 },
      { threadId: 'child', total: child0 }, { threadId: 'child', total: child0 },
    ],
    [
      { threadId: 'root', total: root1 },
      { threadId: 'child', total: child1 },
    ],
  ]);
  const result = await executeAppServer({ name: 'child-usage', turns: ['first', 'second'] }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([child]) });
  const tokenEvents = result.events.filter(event => event.type === 'token_usage');
  const rootEvents = tokenEvents.filter(event => event.agentId === undefined);
  const childEvents = tokenEvents.filter(event => event.agentId === 'child');
  assert.deepEqual(rootEvents.map(event => [event.turn, event.data.inputTokens, event.data.outputTokens,
    event.data.cacheReadTokens, event.data.model]), [
    [0, 100, 10, 20, 'gpt-6-luna'], [1, 50, 5, 0, 'gpt-6-luna'],
  ]);
  assert.deepEqual(childEvents.map(event => [event.turn, event.data.inputTokens, event.data.outputTokens,
    event.data.cacheReadTokens, event.data.model]), [
    [0, 80, 20, 10, 'gpt-6-astra'], [1, 70, 5, 0, 'gpt-6-astra'],
  ]);
  assert.equal(result.output, 'second');
  assert.equal(result.metadata.delegatedUsageVerified, true);
  assert.equal(result.metadata.usageScope, 'root-plus-verified-descendants');
  assert.deepEqual(result.metadata.delegatedUsage.childThreadIds, ['child']);
  assert.deepEqual(result.metadata.delegatedUsage.meteredChildThreadIds, ['child']);
  assert.deepEqual(result.metadata.delegatedUsage.childAgents, [{ threadId: 'child', parentThreadId: 'root',
    model: 'gpt-6-astra', reasoningEffort: 'high', agentRole: 'observer' }]);
  assert.ok(childEvents.every(event => event.agentRole === 'observer'));
});

test('foreign usage notifications are excluded from verified child totals', async () => {
  const client = new Client([[
    { threadId: 'foreign', total: { inputTokens: 900, outputTokens: 90, cachedInputTokens: 0 } },
    { threadId: 'child', total: { inputTokens: 80, outputTokens: 20, cachedInputTokens: 10 } },
  ]]);
  const result = await executeAppServer({ name: 'foreign-usage', prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([
      { threadId: 'child', agentRole: 'reviewer', model: 'gpt-6-astra', reasoningEffort: 'high' },
    ]) });
  const tokenEvents = result.events.filter(event => event.type === 'token_usage');
  assert.deepEqual(tokenEvents.map(event => [event.agentId, event.agentRole, event.data.inputTokens, event.data.model]), [
    ['child', 'reviewer', 80, 'gpt-6-astra'],
  ]);
  assert.deepEqual(result.metadata.delegatedUsage.excludedForeignThreadIds, ['foreign']);
  assert.equal(result.metadata.delegatedUsageVerified, true);
});

test('child thread identities keep same-role usage scopes unique and role is optional metadata', async () => {
  const children = [
    { threadId: 'child-a', agentRole: 'reviewer', model: 'gpt-6-astra', reasoningEffort: 'high' },
    { threadId: 'child-b', agentRole: 'reviewer', model: 'gpt-6-astra', reasoningEffort: 'high' },
    { threadId: 'child-no-role', model: 'gpt-6-luna', reasoningEffort: 'max' },
  ];
  const client = new Client([children.map((child, index) => ({ threadId: child.threadId, total: {
    inputTokens: 10 + index, outputTokens: 1, cachedInputTokens: 0,
  } }))]);
  const result = await executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof(children) });
  const usageEvents = result.events.filter(event => event.type === 'token_usage');
  assert.deepEqual(usageEvents.map(event => [event.agentId, event.agentRole ?? null]).sort((a, b) => a[0].localeCompare(b[0])), [
    ['child-a', 'reviewer'], ['child-b', 'reviewer'], ['child-no-role', null],
  ]);
  assert.equal(result.metadata.delegatedUsageVerified, true);
  assert.deepEqual(result.metadata.delegatedUsage.meteredChildThreadIds,
    ['child-a', 'child-b', 'child-no-role']);
  assert.equal(result.metadata.delegatedUsage.childAgents.find(child => child.threadId === 'child-no-role').agentRole, null);
});

test('missing child usage or child model stays explicit and never receives root attribution', async () => {
  const child = { threadId: 'child', agentRole: 'observer', model: 'gpt-6-astra' };
  const noUsageClient = new Client();
  const noUsage = await executeAppServer({ prompt: 'one' }, options, config,
    { client: noUsageClient, normalizeEvent, settleWorkspace: async () => settlementProof([child]) });
  assert.equal(noUsage.metadata.delegatedUsageVerified, false);
  assert.deepEqual(noUsage.metadata.delegatedUsage.incompleteChildThreadIds, ['child']);
  assert.equal(noUsage.events.some(event => event.type === 'token_usage' && event.agentId === 'child'), false);

  const missingModelClient = new Client([[
    { threadId: 'child', total: { inputTokens: 25, outputTokens: 5, cachedInputTokens: 0 } },
  ]]);
  const missingModel = await executeAppServer({ prompt: 'one' }, options, config,
    { client: missingModelClient, normalizeEvent, settleWorkspace: async () => settlementProof([
      { threadId: 'child', agentRole: 'observer', model: null },
    ]) });
  assert.equal(missingModel.metadata.delegatedUsageVerified, false);
  assert.deepEqual(missingModel.metadata.delegatedUsage.incompleteChildThreadIds, ['child']);
  assert.equal(missingModel.events.some(event => event.type === 'token_usage' && event.agentId === 'child'), false);
});

test('a proven root-only settlement has an explicit zero-descendant usage scope', async () => {
  const client = new Client();
  const result = await executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([]) });
  assert.equal(result.metadata.delegatedUsageVerified, true);
  assert.equal(result.metadata.usageScope, 'root-only-no-descendants');
  assert.deepEqual(result.metadata.delegatedUsage.childThreadIds, []);
  assert.deepEqual(result.metadata.delegatedUsage.incompleteChildThreadIds, []);
});

test('compatibility settlement stubs without ownership snapshots remain root-only and unverified', async () => {
  const client = new Client([[
    { threadId: 'child', total: { inputTokens: 25, outputTokens: 5, cachedInputTokens: 0 } },
  ]]);
  const result = await executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => ({ interrupted: false }) });
  assert.equal(result.metadata.delegatedUsageVerified, false);
  assert.equal(result.metadata.usageScope, 'root-only-unverified-settlement');
  assert.deepEqual(result.metadata.delegatedUsage.incompleteChildThreadIds, ['child']);
  assert.equal(result.events.some(event => event.type === 'token_usage' && event.agentId === 'child'), false);
});

test('settlement snapshots must match the exact session returned when the root thread started', async () => {
  const client = new Client([[
    { threadId: 'child', total: { inputTokens: 25, outputTokens: 5, cachedInputTokens: 0 } },
  ]]);
  const result = await executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([
      { threadId: 'child', agentRole: 'observer', model: 'gpt-6-astra' },
    ], { sessionId: 'different-root-session' }) });
  assert.equal(result.metadata.delegatedUsageVerified, false);
  assert.equal(result.metadata.usageScope, 'root-only-unverified-settlement');
  assert.deepEqual(result.metadata.delegatedUsage.incompleteChildThreadIds, ['child']);
  assert.equal(result.events.some(event => event.type === 'token_usage' && event.agentId === 'child'), false);
});

test('native settlement snapshots preserve thread/read model, effort, and role metadata', async () => {
  const threads = {
    root: { id: 'root', parentThreadId: null, sessionId: 'root-session', model: 'gpt-6-luna',
      reasoningEffort: 'max', agentRole: null },
    child: { id: 'child', parentThreadId: 'root', sessionId: 'root-session', model: 'gpt-6-astra',
      reasoningEffort: 'high', agentRole: 'observer' },
  };
  const client = { async request(method, params) {
    if (method === 'thread/list') return {
      data: params.archived === false ? [{ id: 'child', parentThreadId: 'root', sessionId: 'root-session' }] : [],
      nextCursor: null,
    };
    if (method === 'thread/read') return { thread: { ...threads[params.threadId], status: { type: 'idle' } } };
    if (method === 'thread/turns/list') return { data: [{ id: `${params.threadId}-turn`, status: 'completed',
      itemsView: 'full', items: [] }], nextCursor: null };
    if (method === 'thread/backgroundTerminals/list') return { data: [], nextCursor: null };
    throw new Error(`Unexpected method ${method}`);
  } };
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000 });
  const snapshot = evidence.snapshots.at(-1);
  assert.equal(snapshot.root.model, 'gpt-6-luna');
  assert.equal(snapshot.root.reasoningEffort, 'max');
  assert.equal(snapshot.root.agentRole, null);
  assert.deepEqual(snapshot.descendants[0], {
    threadId: 'child', parentThreadId: 'root', sessionId: 'root-session', model: 'gpt-6-astra',
    reasoningEffort: 'high', agentRole: 'observer', status: 'idle',
    turns: [{ turnId: 'child-turn', status: 'completed' }], agentCalls: [], commandItems: [], backgroundTerminals: [],
  });
});

test('decreasing cumulative usage from an owned descendant is rejected', async () => {
  const client = new Client([[
    { threadId: 'child', total: { inputTokens: 100, outputTokens: 10, cachedInputTokens: 5 } },
    { threadId: 'child', total: { inputTokens: 90, outputTokens: 10, cachedInputTokens: 5 } },
  ]]);
  await assert.rejects(executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([
      { threadId: 'child', agentRole: 'observer', model: 'gpt-6-astra' },
    ]) }), /Cumulative Codex usage decreased/);
  assert.equal(client.closed, true);
});

test('delegated usage buffering is bounded and records truncation', async () => {
  const updates = Array.from({ length: 8193 }, (_, index) => ({ threadId: 'foreign', total: {
    inputTokens: index, outputTokens: 0, cachedInputTokens: 0,
  } }));
  const client = new Client([updates]);
  const result = await executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([]) });
  assert.equal(result.metadata.delegatedUsage.bufferedEventCount, 8192);
  assert.equal(result.metadata.delegatedUsage.bufferOverflow, true);
  assert.equal(result.metadata.delegatedUsage.droppedEventCount, 1);
  assert.equal(result.metadata.delegatedUsageVerified, false);
});

test('delegated usage buffering also caps distinct thread identities', async () => {
  const updates = Array.from({ length: 1025 }, (_, index) => ({ threadId: `foreign-${index}`, total: {
    inputTokens: index, outputTokens: 0, cachedInputTokens: 0,
  } }));
  const client = new Client([updates]);
  const result = await executeAppServer({ prompt: 'one' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([]) });
  assert.equal(result.metadata.delegatedUsage.excludedForeignThreadIds.length, 1024);
  assert.equal(result.metadata.delegatedUsage.bufferedEventCount, 1024);
  assert.equal(result.metadata.delegatedUsage.droppedEventCount, 1);
  assert.equal(result.metadata.delegatedUsage.bufferOverflow, true);
  assert.equal(result.metadata.delegatedUsageVerified, false);
});

test('captured child message and reasoning notifications resolve only after stable ownership and model proof', async () => {
  const child = { threadId: 'child', agentRole: 'observer', model: 'gpt-6-astra', reasoningEffort: 'high' };
  const notifications = [
    { method: 'item/started', params: { threadId: 'child', turnId: 'child-turn', item: {
      id: 'reasoning-1', type: 'reasoning', summary: [], content: [],
    } } },
    { method: 'item/completed', params: { threadId: 'child', turnId: 'child-turn', item: {
      id: 'reasoning-1', type: 'reasoning', summary: [], content: [],
    } } },
    { method: 'item/started', params: { threadId: 'child', turnId: 'child-turn', item: {
      id: 'message-1', type: 'agentMessage', text: 'child summary', phase: 'final_answer',
      memoryCitation: [], delivery: null, questions: [],
    } } },
    { method: 'item/completed', params: { threadId: 'child', turnId: 'child-turn', item: {
      id: 'message-1', type: 'agentMessage', text: 'child summary', phase: 'final_answer',
      memoryCitation: [], delivery: null, questions: [],
    } } },
  ];
  const client = new Client([], [notifications]);
  const result = await executeAppServer({ prompt: 'root request' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([child]) });
  const childEvents = result.events.filter(event => event.agentId === 'child');
  assert.equal(childEvents.length, 4);
  assert.deepEqual(childEvents.map(event => event.type), ['custom', 'reasoning', 'custom', 'assistant_message']);
  assert.ok(childEvents.every(event => event.agentRole === 'observer' && event.turn === 0));
  assert.equal(childEvents.find(event => event.type === 'assistant_message').data.content, 'child summary');
  assert.equal(result.output, 'root request');
  assert.equal(result.metadata.delegatedEvents.observedItemNotificationCount, 4);
  assert.equal(result.metadata.delegatedEvents.bufferedItemCount, 4);
  assert.equal(result.metadata.delegatedEvents.resolvedItemNotificationCount, 4);
  assert.equal(result.metadata.delegatedEvents.unresolvedItemCount, 0);
  assert.equal(result.metadata.delegatedEvents.completeDelegatedTrajectoryVerified, false);
});

test('owned child tool call and result keep unique thread identity and original interleaving order', async () => {
  const children = [
    { threadId: 'child-a', agentRole: 'reviewer', model: 'gpt-6-astra', reasoningEffort: 'high' },
    { threadId: 'child-b', agentRole: 'reviewer', model: 'gpt-6-astra', reasoningEffort: 'high' },
  ];
  const notifications = [
    { method: 'item/started', params: { threadId: 'child-a', turnId: 'a-turn', item: {
      id: 'call-1', type: 'commandExecution', command: 'python -c "print(1)"', cwd: '/fixture',
    } } },
    { method: 'item/completed', params: { threadId: 'root', turnId: 'root-turn', item: {
      id: 'root-between', type: 'agentMessage', text: 'root between children', phase: 'commentary',
    } } },
    { method: 'item/started', params: { threadId: 'child-b', turnId: 'b-turn', item: {
      id: 'message-b', type: 'agentMessage', text: 'sibling response', phase: 'final_answer',
    } } },
    { method: 'item/completed', params: { threadId: 'child-a', turnId: 'a-turn', item: {
      id: 'call-1', type: 'commandExecution', command: 'python -c "print(1)"', cwd: '/fixture',
      exitCode: 0, aggregatedOutput: '1\n', status: 'completed',
    } } },
    { method: 'item/completed', params: { threadId: 'child-b', turnId: 'b-turn', item: {
      id: 'message-b', type: 'agentMessage', text: 'sibling response', phase: 'final_answer',
    } } },
  ];
  const client = new Client([], [notifications]);
  const result = await executeAppServer({ prompt: 'root final' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof(children) });
  const childCalls = result.events.filter(event => event.type === 'tool_call' && event.data.toolCallId === 'call-1');
  const childResults = result.events.filter(event => event.type === 'tool_result' && event.data.toolCallId === 'call-1');
  assert.equal(childCalls.length, 1); assert.equal(childResults.length, 1);
  assert.deepEqual([childCalls[0].agentId, childResults[0].agentId], ['child-a', 'child-a']);
  assert.deepEqual([childCalls[0].agentRole, childResults[0].agentRole], ['reviewer', 'reviewer']);
  assert.equal(childResults[0].data.result, '1\n');
  assert.equal(childResults[0].data.success, true);
  const timeline = result.events.filter(event => event.agentId ||
    event.type === 'assistant_message' && ['root between children', 'root final'].includes(event.data?.content))
    .map(event => [event.agentId ?? 'root', event.type, event.data?.content ?? event.data?.toolCallId ?? null]);
  assert.deepEqual(timeline, [
    ['child-a', 'tool_call', 'call-1'],
    ['root', 'assistant_message', 'root between children'],
    ['child-b', 'custom', null],
    ['child-a', 'tool_result', 'call-1'],
    ['child-b', 'assistant_message', 'sibling response'],
    ['root', 'assistant_message', 'root final'],
  ]);
  assert.equal(result.output, 'root final');
  assert.notEqual(childCalls[0].agentId, result.events.find(event => event.data?.content === 'sibling response')?.agentId);
});

test('foreign child item notifications are filtered after a valid root ownership snapshot', async () => {
  const child = { threadId: 'child', model: 'gpt-6-astra', reasoningEffort: 'high' };
  const client = new Client([], [[
    { method: 'item/completed', params: { threadId: 'foreign', turnId: 'foreign-turn', item: {
      id: 'foreign-message', type: 'agentMessage', text: 'not owned', phase: 'final_answer',
    } } },
    { method: 'item/completed', params: { threadId: 'child', turnId: 'child-turn', item: {
      id: 'owned-message', type: 'agentMessage', text: 'owned', phase: 'final_answer',
    } } },
  ]]);
  const result = await executeAppServer({ prompt: 'root' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([child]) });
  assert.deepEqual(result.events.filter(event => event.agentId).map(event => [event.agentId, event.data.content]), [
    ['child', 'owned'],
  ]);
  assert.deepEqual(result.metadata.delegatedEvents.excludedForeignThreadIds, ['foreign']);
  assert.equal(result.metadata.delegatedEvents.excludedForeignItemCount, 1);
  assert.equal(result.metadata.delegatedEvents.unresolvedItemCount, 0);
  assert.equal(result.metadata.delegatedEvents.completeDelegatedTrajectoryVerified, false);
});

test('missing ownership or actual child model leaves placeholders unresolved without root attribution', async () => {
  const notification = { method: 'item/completed', params: { threadId: 'child', turnId: 'child-turn', item: {
    id: 'message', type: 'agentMessage', text: 'must not be emitted unverified', phase: 'final_answer',
  } } };
  const noOwnership = await executeAppServer({ prompt: 'root' }, options, config,
    { client: new Client([], [[notification]]), normalizeEvent, settleWorkspace: async () => ({ interrupted: false }) });
  assert.equal(noOwnership.events.some(event => event.agentId), false);
  assert.deepEqual(noOwnership.metadata.delegatedEvents.unresolvedThreadIds, ['child']);
  assert.deepEqual(noOwnership.metadata.delegatedEvents.unresolvedOwnedThreadIds, []);
  assert.equal(noOwnership.metadata.delegatedEvents.unresolvedItemCount, 1);

  const noModel = await executeAppServer({ prompt: 'root' }, options, config,
    { client: new Client([], [[notification]]), normalizeEvent,
      settleWorkspace: async () => settlementProof([{ threadId: 'child', model: null }]) });
  assert.equal(noModel.events.some(event => event.agentId), false);
  assert.deepEqual(noModel.metadata.delegatedEvents.unresolvedOwnedThreadIds, ['child']);
  assert.equal(noModel.metadata.delegatedEvents.unresolvedOwnedItemCount, 1);
  assert.equal(noModel.metadata.delegatedEvents.completeDelegatedTrajectoryVerified, false);
});

test('unmapped and malformed child items are reported as incomplete', async () => {
  const client = new Client([], [[
    { method: 'item/completed', params: { threadId: 'child', turnId: 'turn', item: { id: 'future', type: 'futureNativeItem' } } },
    { method: 'item/completed', params: { threadId: 'child', turnId: 'turn', item: null } },
  ]]);
  const result = await executeAppServer({ prompt: 'root' }, options, config,
    { client, normalizeEvent, settleWorkspace: async () => settlementProof([
      { threadId: 'child', model: 'gpt-6-astra', reasoningEffort: 'high' },
    ]) });
  assert.equal(result.events.some(event => event.agentId), false);
  assert.equal(result.metadata.delegatedEvents.unknownItemCount, 2);
  assert.equal(result.metadata.delegatedEvents.malformedNotificationCount, 1);
  assert.deepEqual(result.metadata.delegatedEvents.unknownThreadIds, ['child']);
  assert.deepEqual(result.metadata.delegatedEvents.unresolvedOwnedThreadIds, ['child']);
  assert.equal(result.metadata.delegatedEvents.completeDelegatedTrajectoryVerified, false);
});

test('child item buffer caps item count, serialized bytes, and distinct thread identities', async () => {
  const longOptions = { ...options, timeout: 10_000 };
  const countNotifications = Array.from({ length: 4097 }, (_, index) => ({
    method: 'item/started', params: { threadId: 'child', turnId: 'turn', item: {
      id: `message-${index}`, type: 'agentMessage', text: 'x', phase: 'commentary',
    } },
  }));
  const countResult = await executeAppServer({ prompt: 'root' }, longOptions, config,
    { client: new Client([], [countNotifications]), normalizeEvent,
      settleWorkspace: async () => settlementProof([{ threadId: 'child', model: 'gpt-6-astra' }]) });
  assert.equal(countResult.metadata.delegatedEvents.bufferedItemCount, 4096);
  assert.equal(countResult.metadata.delegatedEvents.droppedItemCount, 1);
  assert.equal(countResult.metadata.delegatedEvents.overflow, true);
  assert.deepEqual(countResult.metadata.delegatedEvents.overflowReasons, ['item-count']);
  assert.ok(countResult.metadata.delegatedEvents.bufferedItemBytes <= 8 * 1024 * 1024);
  assert.equal(countResult.metadata.delegatedEvents.unresolvedOwnedItemCount, 1);
  assert.equal(countResult.events.filter(event => event.agentId === 'child').length, 4096);

  const byteResult = await executeAppServer({ prompt: 'root' }, longOptions, config,
    { client: new Client([], [[{ method: 'item/completed', params: { threadId: 'child', turnId: 'turn', item: {
      id: 'large', type: 'agentMessage', text: 'x'.repeat(8 * 1024 * 1024 + 1), phase: 'final_answer',
    } } }]]), normalizeEvent,
      settleWorkspace: async () => settlementProof([{ threadId: 'child', model: 'gpt-6-astra' }]) });
  assert.equal(byteResult.metadata.delegatedEvents.bufferedItemCount, 0);
  assert.equal(byteResult.metadata.delegatedEvents.bufferedItemBytes, 0);
  assert.equal(byteResult.metadata.delegatedEvents.droppedItemCount, 1);
  assert.deepEqual(byteResult.metadata.delegatedEvents.overflowReasons, ['item-bytes']);
  assert.equal(byteResult.metadata.delegatedEvents.unresolvedOwnedItemCount, 1);
  assert.equal(byteResult.events.some(event => event.agentId), false);

  const threadNotifications = Array.from({ length: 1025 }, (_, index) => ({
    method: 'item/completed', params: { threadId: `foreign-${index}`, turnId: 'turn', item: {
      id: `message-${index}`, type: 'agentMessage', text: 'x', phase: 'final_answer',
    } },
  }));
  const threadResult = await executeAppServer({ prompt: 'root' }, longOptions, config,
    { client: new Client([], [threadNotifications]), normalizeEvent,
      settleWorkspace: async () => settlementProof([]) });
  assert.equal(threadResult.metadata.delegatedEvents.bufferedItemCount, 1024);
  assert.equal(threadResult.metadata.delegatedEvents.droppedItemCount, 1);
  assert.equal(threadResult.metadata.delegatedEvents.excludedForeignThreadIds.length, 1024);
  assert.deepEqual(threadResult.metadata.delegatedEvents.overflowReasons, ['thread-count']);
  assert.equal(threadResult.metadata.delegatedEvents.completeDelegatedTrajectoryVerified, false);
});
