import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { resolve } from 'node:path';
import test from 'node:test';
import { z } from 'zod';
import { CodexJudgeClient } from './judge.mjs';

function fixture(actions, identity = {}) {
  let transport;
  const client = new CodexJudgeClient({
    skillIsolationResolver: async () => ({ configArgs: [], disabledSkills: 0 }),
    appServerFactory: (_binary, { handlers }) => {
      transport = new EventEmitter();
      transport.calls = [];
      transport.initialize = async () => {};
      transport.shutdown = async () => { transport.closed = true; transport.emit('close'); return { closed: true }; };
      transport.request = async (method, params) => {
        transport.calls.push({ method, params });
        if (method === 'thread/start') return { thread: { id: 'thread' }, model: 'gpt-6-luna', reasoningEffort: 'max', ...identity };
        if (method === 'turn/interrupt') return {};
        const id = `turn-${transport.calls.filter(c => c.method === 'turn/start').length}`;
        transport.emit('notification', { method: 'turn/started', params: { threadId: 'thread', turn: { id } } });
        await actions.shift()?.({ transport, handlers, id });
        return { turn: { id, status: 'inProgress' } };
      };
      return transport;
    },
  });
  return { client, transport: () => transport, options: { model: 'gpt-6-luna', systemMessage: 'Original rubric', userMessage: 'Original evidence',
    timeoutMs: 1000, maxReminders: 1, tool: { name: 'grade', description: 'Original verdict', parameters: z.object({ passed: z.boolean() }) } } };
}
const complete = ({ transport, id }) => transport.emit('notification', { method: 'turn/completed', params: { threadId: 'thread', turn: { id, status: 'completed' } } });
const verdict = async (context, args, extra = {}) => context.handlers['item/tool/call']({ threadId: 'thread', turnId: context.id, callId: `${context.id}-call`, tool: 'grade', arguments: args, ...extra });

test('valid native verdict preserves original prompts and actual Luna Max identity', async () => {
  const f = fixture([async context => { assert.equal((await verdict(context, { passed: true })).success, true); complete(context); }]);
  const result = await f.client.judge(f.options);
  assert.deepEqual(result.args, { passed: true });
  assert.equal(result.codexCompatibility.toolIsolation, 'unverified');
  assert.equal(result.effectiveReasoningEffort, 'max');
  assert.equal(f.transport().calls[0].params.baseInstructions, 'Original rubric');
  assert.equal(f.transport().calls[1].params.input[0].text, 'Original evidence');
  assert.equal(f.transport().closed, true);
});
test('invalid schema gets a bounded reminder in the same thread', async () => {
  const f = fixture([async c => { assert.equal((await verdict(c, { passed: 'yes' })).success, false); complete(c); },
    async c => { await verdict(c, { passed: false }); complete(c); }]);
  const result = await f.client.judge(f.options);
  assert.equal(result.remindersUsed, 1);
  assert.deepEqual(result.args, { passed: false });
  assert.match(f.transport().calls.filter(c => c.method === 'turn/start')[1].params.input[0].text, /invalid arguments/);
});
test('completed text without a verdict cannot become a grade', async () => {
  const f = fixture([complete, complete]);
  await assert.rejects(f.client.judge(f.options), /did not invoke grade/);
  assert.equal(f.transport().closed, true);
});
test('foreign tool identity invalidates a completed verdict turn', async () => {
  const f = fixture([async c => { await verdict(c, { passed: true }, { threadId: 'foreign' }); complete(c); }]);
  await assert.rejects(f.client.judge(f.options), /uncorrelated identity/);
});
test('model substitution is rejected before a model turn', async () => {
  const f = fixture([], { model: 'another-model' });
  await assert.rejects(f.client.judge(f.options), /effective identity/);
  assert.equal(f.transport().calls.some(c => c.method === 'turn/start'), false);
});
test('unexpected process exit fails promptly and closes only owned transport', async () => {
  const f = fixture([({ transport }) => transport.emit('close')]);
  await assert.rejects(f.client.judge(f.options), /transport closed/);
  assert.equal(f.transport().closed, true);
});
test('a second valid verdict is rejected rather than replacing the first', async () => {
  const f = fixture([async c => {
    await verdict(c, { passed: true });
    const second = await verdict(c, { passed: false }, { callId: 'second-verdict' });
    assert.equal(second.success, false);
    complete(c);
  }]);
  await assert.rejects(f.client.judge(f.options), /more than one valid verdict/);
});
test('missing terminal notification times out, interrupts and cleans up', async () => {
  const f = fixture([async c => { await verdict(c, { passed: true }); }]);
  await assert.rejects(f.client.judge({ ...f.options, timeoutMs: 30 }), /Judge turn timed out/);
  assert.equal(f.transport().calls.some(c => c.method === 'turn/interrupt'), true);
  assert.equal(f.transport().closed, true);
});
test('a reroute after turn start fails without waiting for a terminal notification', async () => {
  const f = fixture([({ transport }) => {
    setTimeout(() => transport.emit('notification', { method: 'model/rerouted', params: { threadId: 'thread' } }), 5);
  }]);
  const started = Date.now();
  await assert.rejects(f.client.judge(f.options), /model was rerouted/);
  assert.ok(Date.now() - started < 500);
  assert.equal(f.transport().closed, true);
});
test('a throwing cleanup observer cannot prevent listener and ownership cleanup', async () => {
  let observed = false;
  const f = fixture([async c => {
    await verdict(c, { passed: true });
    complete(c);
    c.transport.shutdown = async () => { throw new Error('shutdown failure'); };
  }]);
  await assert.rejects(f.client.judge({ ...f.options, workspace: {
    workingDirectory: resolve(process.cwd()), evidenceFiles: [],
    onCleanupError() { observed = true; throw new Error('observer failure'); },
  } }), /shutdown failure/);
  assert.equal(observed, true);
  assert.equal(f.client.clients.size, 0);
  assert.equal(f.transport().listenerCount('notification'), 0);
});

test('medium effort is accepted and remains explicit for judge setup', () => {
  const client = new CodexJudgeClient({ reasoningEffort: 'medium' });
  assert.equal(client.reasoningEffort, 'medium');
  assert.throws(() => new CodexJudgeClient({ reasoningEffort: 'invalid' }), /supported reasoning effort/);
});
