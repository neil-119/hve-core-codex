import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { runAppServerTurn } from './turn.mjs';

class Client extends EventEmitter {
  calls = [];
  constructor(handle) { super(); this.handle = handle; }
  request(method, params) { this.calls.push({ method, params }); return this.handle(method, params, this); }
  terminal(status = 'completed', id = 'turn-1', threadId = 'thread-1') {
    this.emit('notification', { method: 'turn/completed', params: { threadId, turn: { id, status, items: [] } } });
  }
}
const params = { threadId: 'thread-1', input: [{ type: 'text', text: 'hello' }] };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

test('terminal notification may precede the turn/start response', async () => {
  const client = new Client((_method, _params, self) => { self.terminal(); return { turn: { id: 'turn-1' } }; });
  const result = await runAppServerTurn(client, params, { timeoutMs: 500 });
  assert.equal(result.turn.status, 'completed');
  assert.equal(result.interruptRequested, false);
  assert.equal(client.listenerCount('notification'), 0);
});

test('interrupt acknowledgment alone does not settle the turn', async () => {
  let acknowledgeInterrupt;
  const interrupted = new Promise(resolve => { acknowledgeInterrupt = resolve; });
  const client = new Client(method => {
    if (method === 'turn/start') return { turn: { id: 'turn-1' } };
    acknowledgeInterrupt(); return {};
  });
  let settled = false;
  const pending = runAppServerTurn(client, params, { timeoutMs: 500, maxAgentDurationMs: 10 })
    .then(value => { settled = true; return value; });
  await interrupted;
  await delay(10);
  assert.equal(settled, false);
  assert.deepEqual(client.calls[1], { method: 'turn/interrupt', params: { threadId: 'thread-1', turnId: 'turn-1' } });
  client.terminal('interrupted');
  const result = await pending;
  assert.equal(result.interruptRequested, true);
  assert.equal(result.turn.status, 'interrupted');
});

test('unrelated thread and turn completions cannot finish a configured turn', async () => {
  const client = new Client(() => ({ turn: { id: 'turn-1' } }));
  const pending = runAppServerTurn(client, params, { timeoutMs: 500 });
  await delay(5);
  client.terminal('completed', 'other-turn'); client.terminal('completed', 'turn-1', 'other-thread');
  client.terminal();
  assert.equal((await pending).turn.id, 'turn-1');
});

test('hard deadline rejects a hung start RPC without reporting a recoverable stop', async () => {
  const client = new Client(() => new Promise(() => {}));
  await assert.rejects(runAppServerTurn(client, params, { timeoutMs: 10 }), /settlement unproven/);
  assert.equal(client.listenerCount('notification'), 0);
});

test('working budget includes a delayed turn/start response', async () => {
  let respond, notifyInterrupted;
  const start = new Promise(resolve => { respond = resolve; });
  const interruptSeen = new Promise(resolve => { notifyInterrupted = resolve; });
  const client = new Client(method => {
    if (method === 'turn/start') return start;
    notifyInterrupted(); return {};
  });
  const pending = runAppServerTurn(client, params, { timeoutMs: 500, maxAgentDurationMs: 10 });
  await delay(25);
  assert.equal(client.calls.length, 1);
  respond({ turn: { id: 'turn-1' } });
  await interruptSeen;
  client.terminal('interrupted');
  assert.equal((await pending).interruptRequested, true);
});

test('transport failure and native failed status are rejected', async () => {
  for (const failure of ['transport', 'native']) {
    const client = new Client(() => ({ turn: { id: 'turn-1' } }));
    const pending = runAppServerTurn(client, params, { timeoutMs: 500 });
    const check = assert.rejects(pending, failure === 'transport' ? /connection lost/ : /turn failed/);
    await delay(5);
    if (failure === 'transport') client.emit('failure', new Error('connection lost')); else client.terminal('failed');
    await check;
  }
});

test('unrequested interruption is not treated as an agent time limit', async () => {
  const client = new Client((_method, _params, self) => { self.terminal('interrupted'); return { turn: { id: 'turn-1' } }; });
  await assert.rejects(runAppServerTurn(client, params, { timeoutMs: 500 }), /without an adapter stop/);
});

test('failed interrupt and missing terminal confirmation both reject', async () => {
  const rejected = new Client(method => {
    if (method === 'turn/start') return { turn: { id: 'turn-1' } };
    throw new Error('interrupt unsupported');
  });
  await assert.rejects(runAppServerTurn(rejected, params, { timeoutMs: 500, maxAgentDurationMs: 5 }), /interrupt unsupported/);
  const unconfirmed = new Client(method => method === 'turn/start' ? { turn: { id: 'turn-1' } } : {});
  await assert.rejects(runAppServerTurn(unconfirmed, params, { timeoutMs: 30, maxAgentDurationMs: 5 }), /settlement unproven/);
  assert.equal(unconfirmed.listenerCount('notification'), 0);
});
