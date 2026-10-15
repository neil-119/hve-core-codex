import test from 'node:test';
import assert from 'node:assert/strict';
import { settleAppServerWorkspace, SettlementError } from './settlement.mjs';

const SOURCE_KINDS = [
  'cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
  'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown',
];

function fixture({ childTurnStatus = 'completed', childParentThreadId = 'root', childSessionId = 'session-root',
  listedChildSessionId = 'session-root',
  childStatus, terminals = {}, rootTurnStatus = 'completed', paginated = false, paginationLoop = false,
  rootItemsView = 'full', rootItems = [], childItems = [] } = {}) {
  const calls = [];
  const state = {
    root: { id: 'root', parentThreadId: null, sessionId: 'session-root', status: { type: 'idle' },
      turns: [{ id: 'root-turn', status: rootTurnStatus, itemsView: rootItemsView, items: rootItems }] },
    child: { id: 'child', parentThreadId: childParentThreadId, sessionId: childSessionId,
      status: childStatus ?? { type: childTurnStatus === 'inProgress' ? 'active' : 'idle',
        ...(childTurnStatus === 'inProgress' ? { activeFlags: [] } : {}) },
      turns: [{ id: 'child-turn', status: childTurnStatus, itemsView: 'full', items: childItems }] },
    terminals: structuredClone(terminals),
  };
  const client = {
    calls,
    state,
    async request(method, params) {
      calls.push({ method, params });
      if (method === 'thread/list') {
        assert.equal(params.ancestorThreadId, 'root');
        assert.deepEqual(params.sourceKinds, SOURCE_KINDS);
        if (paginationLoop && params.archived === false) {
          return params.cursor
            ? { data: [], nextCursor: 'loop' }
            : { data: [{ id: 'child', parentThreadId: 'root', sessionId: listedChildSessionId }], nextCursor: 'loop' };
        }
        if (params.archived === false && paginated) {
          return params.cursor
            ? { data: [], nextCursor: null }
            : { data: [{ id: 'child', parentThreadId: 'root', sessionId: listedChildSessionId }], nextCursor: 'child-page-2' };
        }
        if (params.archived === false && state.child) {
          return { data: [{ id: 'child', parentThreadId: 'root', sessionId: listedChildSessionId }], nextCursor: null };
        }
        return { data: [], nextCursor: null };
      }
      if (method === 'thread/read') {
        const thread = params.threadId === 'root' ? state.root : params.threadId === 'child' ? state.child : null;
        if (!thread) throw new Error(`Unexpected thread ${params.threadId}`);
        return { thread: { id: thread.id, parentThreadId: thread.parentThreadId,
          sessionId: thread.sessionId, status: thread.status } };
      }
      if (method === 'thread/turns/list') {
        const thread = params.threadId === 'root' ? state.root : state.child;
        return { data: structuredClone(thread.turns), nextCursor: null };
      }
      if (method === 'thread/items/list') {
        const thread = params.threadId === 'root' ? state.root : state.child;
        const turn = thread.turns.find(value => value.id === params.turnId);
        return { data: (turn?.items ?? []).map(item => ({ turnId: turn.id, item: structuredClone(item) })), nextCursor: null };
      }
      if (method === 'thread/backgroundTerminals/list') {
        return { data: structuredClone(state.terminals[params.threadId] ?? []), nextCursor: null };
      }
      if (method === 'turn/interrupt') {
        assert.equal(params.threadId, 'child');
        assert.equal(params.turnId, 'child-turn');
        state.child.turns[0].status = 'interrupted';
        state.child.status = { type: 'idle' };
        const rootCall = state.root.turns[0].items.find(item => item.type === 'collabAgentToolCall');
        if (rootCall) {
          rootCall.status = 'completed';
          rootCall.agentsStates.worker.status = 'interrupted';
        }
        return {};
      }
      if (method === 'thread/backgroundTerminals/terminate') {
        const items = state.terminals[params.threadId] ?? [];
        const before = items.length;
        state.terminals[params.threadId] = items.filter(item => item.processId !== params.processId);
        return { terminated: before !== state.terminals[params.threadId].length };
      }
      throw new Error(`Unexpected method ${method}`);
    },
  };
  return client;
}

test('native inventory child identity and loaded root session agree through verified parent ownership', async () => {
  const client = fixture({ listedChildSessionId: 'child', childTurnStatus: 'inProgress' });
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true });
  assert.equal(evidence.settled, true);
  assert.equal(evidence.interrupted, true);
  assert.ok(evidence.actions.some(action => action.threadId === 'child'));
});

test('unrelated inventory session cannot use the native child representation exception', async () => {
  const client = fixture({ listedChildSessionId: 'unrelated-session', childTurnStatus: 'inProgress' });
  await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true }),
    error => error instanceof SettlementError && /ownership disagree/.test(error.message));
  assert.equal(client.calls.some(call => call.method === 'turn/interrupt'), false);
});

test('a child identity in both endpoints cannot bypass the root execution session check', async () => {
  const client = fixture({ listedChildSessionId: 'child', childSessionId: 'child', childTurnStatus: 'inProgress' });
  await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true }),
    error => error instanceof SettlementError && /outside the root session/.test(error.message));
  assert.equal(client.calls.some(call => call.method === 'turn/interrupt'), false);
});

test('settles terminal root and descendants without interrupting normal completion', async () => {
  const client = fixture();
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000 });

  assert.equal(evidence.settled, true);
  assert.equal(evidence.rootTerminalStatus, 'completed');
  assert.equal(evidence.rootInterruptedByHelper, false);
  assert.equal(evidence.interrupted, false);
  assert.equal(evidence.actions.length, 0);
  assert.equal(evidence.snapshots.at(-1).descendants[0].threadId, 'child');
});

test('ignores stale running collaboration snapshots when their completed child is verified idle', async () => {
  const staleSpawn = { id: 'root-agent-call', type: 'collabAgentToolCall', status: 'inProgress',
    receiverThreadIds: ['child'], agentsStates: { worker: { status: 'running' } } };
  const client = fixture({ rootItems: [staleSpawn] });
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true });

  assert.equal(evidence.settled, true);
  assert.equal(evidence.interrupted, false);
  assert.equal(evidence.actions.length, 0);
  assert.equal(evidence.snapshots.at(-1).blockers.length, 0);
});

test('stopActive does not report interruption when the verified workspace has no work to stop', async () => {
  const client = fixture();
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true });

  assert.equal(evidence.settled, true);
  assert.equal(evidence.forcedStopStarted, false);
  assert.equal(evidence.interrupted, false);
  assert.equal(evidence.actions.length, 0);
});

test('preserves a root turn that was already interrupted before descendant settlement', async () => {
  const client = fixture({ rootTurnStatus: 'interrupted' });
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000 });
  assert.equal(evidence.rootTerminalStatus, 'interrupted');
  assert.equal(evidence.rootInterruptedByHelper, false);
  assert.equal(evidence.interrupted, false);
});

test('enumerates both archive states, all source kinds and every cursor page', async () => {
  const client = fixture({ paginated: true });
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000 });
  const lists = client.calls.filter(call => call.method === 'thread/list');
  assert.deepEqual([...new Set(lists.map(call => call.params.archived))].sort(), [false, true]);
  assert.ok(lists.some(call => call.params.cursor === 'child-page-2'));
  assert.equal(evidence.settled, true);
});

test('fails closed on a repeated descendant pagination cursor before any mutation', async () => {
  const client = fixture({ childTurnStatus: 'inProgress', paginationLoop: true });
  await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true }),
    error => error instanceof SettlementError && /pagination cursor repeated/.test(error.message));
  assert.equal(client.calls.some(call => ['turn/interrupt', 'thread/backgroundTerminals/terminate'].includes(call.method)), false);
});

test('validates descendant parent and session ownership before interrupting it', async () => {
  const client = fixture({ childTurnStatus: 'inProgress', childParentThreadId: 'other-root', childSessionId: 'other-session' });
  await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true }),
    error => error instanceof SettlementError && /ownership disagree/.test(error.message));
  assert.equal(client.calls.some(call => call.method === 'turn/interrupt'), false);
  assert.equal(client.calls.some(call => call.method === 'thread/backgroundTerminals/terminate'), false);
});

test('fails closed on active descendants unless stop or a remaining work budget is explicit', async () => {
  const client = fixture({ childTurnStatus: 'inProgress' });
  await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 1000 }),
    error => error instanceof SettlementError && /no stop budget was authorized/.test(error.message));
  assert.equal(client.calls.some(call => call.method === 'turn/interrupt'), false);
});

test('explicit stop interrupts the child turn only and verifies terminal state on fresh reads', async () => {
  const rootCall = { id: 'root-agent-call', type: 'collabAgentToolCall', status: 'inProgress',
    receiverThreadIds: ['child'], agentsStates: { worker: { status: 'running' } } };
  const client = fixture({ childTurnStatus: 'inProgress', rootItems: [rootCall], rootItemsView: 'summary' });
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true });
  assert.equal(evidence.rootTerminalStatus, 'completed');
  assert.equal(evidence.rootInterruptedByHelper, false);
  assert.equal(evidence.interrupted, true);
  assert.deepEqual(evidence.actions.filter(action => action.kind === 'turn/interrupt').map(action => [action.threadId, action.turnId]),
    [['child', 'child-turn']]);
  assert.ok(client.calls.some(call => call.method === 'thread/items/list' && call.params.threadId === 'root'));
  assert.ok(client.calls.filter(call => call.method === 'thread/turns/list').length >= 4);
});

test('spends the remaining agent work budget before switching to child cleanup', async () => {
  const client = fixture({ childTurnStatus: 'inProgress' });
  const evidence = await settleAppServerWorkspace(client, 'root', {
    timeoutMs: 1000, maxAgentDurationMs: 20,
  });
  assert.equal(evidence.interrupted, true);
  assert.equal(evidence.rootTerminalStatus, 'completed');
  assert.ok(evidence.durationMs >= 15);
  assert.equal(client.calls.some(call => call.method === 'turn/interrupt'), true);
});

test('terminates only listed thread-owned background terminals then relists', async () => {
  const client = fixture({ terminals: { child: [{ processId: 'proc-child-1', itemId: 'cmd-item-1',
    command: 'sleep', cwd: 'C:\\eval' }] } });
  const evidence = await settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true });
  const terminate = client.calls.find(call => call.method === 'thread/backgroundTerminals/terminate');
  assert.deepEqual(terminate.params, { threadId: 'child', processId: 'proc-child-1' });
  const terminateIndex = client.calls.indexOf(terminate);
  assert.equal(client.calls.slice(terminateIndex + 1).some(call => call.method === 'thread/backgroundTerminals/list' && call.params.threadId === 'child'), true);
  assert.equal(evidence.interrupted, true);
  assert.equal(evidence.settled, true);
});

test('hard deadline bounds a hung injected RPC and returns unproven evidence', async () => {
  const client = { calls: [], request(method, params) {
    this.calls.push({ method, params });
    if (method === 'thread/list') return { data: [], nextCursor: null };
    return new Promise(() => {});
  } };
  await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 15 }),
    error => error instanceof SettlementError && /deadline expired during thread\/read/.test(error.message) &&
      error.evidence?.settled === false);
  assert.equal(client.calls.length, 3);
  assert.equal(client.calls[2].method, 'thread/read');
});

test('unloaded or errored child metadata cannot prove a live workspace boundary', async () => {
  for (const type of ['notLoaded', 'systemError']) {
    const client = fixture({ childStatus: { type } });
    await assert.rejects(settleAppServerWorkspace(client, 'root', { timeoutMs: 1000, stopActive: true }),
      /lacks live idle-state evidence/);
    assert.equal(client.calls.some(call => call.method === 'turn/interrupt'), false);
  }
});
