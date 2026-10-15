import assert from 'node:assert/strict';
import { EventEmitter, once } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import test from 'node:test';
import { AppServerTransport, JsonRpcError } from './app-server.mjs';

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.messages = [];
    this.signals = [];
    this.pid = 43210;
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.stdin = new Writable({
      write: (chunk, _encoding, callback) => {
        const text = chunk.toString('utf8');
        for (const line of text.split('\n').filter(Boolean)) {
          this.messages.push(JSON.parse(line));
          this.emit('outbound', this.messages.at(-1));
        }
        callback();
      },
    });
  }

  send(message) { this.stdout.write(`${JSON.stringify(message)}\n`); }
  sendBytes(bytes) { this.stdout.write(bytes); }
  finish(code = 0, signal = null) {
    if (this.finished) return;
    this.finished = true;
    this.stdout.end();
    this.emit('close', code, signal);
  }
  kill(signal) {
    this.signals.push(signal);
    queueMicrotask(() => this.finish(signal === 'SIGKILL' ? null : 0, signal));
    return true;
  }
}

function createTransport(options = {}) {
  const child = new FakeChild();
  const transport = new AppServerTransport(child, options);
  return { child, transport };
}

async function waitForMessages(child, count) {
  while (child.messages.length < count) await once(child, 'outbound');
}

test('initialize performs the generated-schema handshake then sends initialized notification', async () => {
  const { child, transport } = createTransport();
  const initializing = transport.initialize({
    clientInfo: { name: 'vally-executor-test', version: '0.1.0' },
    capabilities: { experimentalApi: true },
  });

  assert.deepEqual(child.messages[0], {
    id: 1,
    method: 'initialize',
    params: { clientInfo: { name: 'vally-executor-test', version: '0.1.0' }, capabilities: { experimentalApi: true } },
  });
  child.send({ id: 1, result: { userAgent: 'codex-test', codexHome: 'C:\\test', platformFamily: 'windows', platformOs: 'windows' } });
  const result = await initializing;

  assert.equal(result.userAgent, 'codex-test');
  assert.deepEqual(child.messages[1], { method: 'initialized' });
  await transport.shutdown();
});

test('request ids correlate concurrent replies and emit server notifications', async () => {
  const { child, transport } = createTransport();
  const first = transport.request('first/method', { value: 1 });
  const second = transport.request('second/method', { value: 2 });
  const notifications = [];
  transport.on('notification', notification => notifications.push(notification));

  child.send({ method: 'thread/status/changed', params: { threadId: 'thread-a' } });
  child.send({ id: child.messages[1].id, result: { which: 'second' } });
  child.send({ id: child.messages[0].id, result: { which: 'first' } });

  assert.deepEqual(await second, { which: 'second' });
  assert.deepEqual(await first, { which: 'first' });
  assert.deepEqual(notifications, [{ method: 'thread/status/changed', params: { threadId: 'thread-a' } }]);
  await transport.shutdown();
});

test('server-initiated requests require an explicit handler and return JSON-RPC errors', async () => {
  const { child, transport } = createTransport({ handlers: {
    'item/tool/call': async params => ({ echoed: params.arguments }),
    'test/fail': () => { throw Object.assign(new Error('denied'), { code: -32010, data: { reason: 'fixture' } }); },
  } });

  child.send({ id: 'server-1', method: 'item/tool/call', params: { arguments: { query: 'fixed' } } });
  child.send({ id: 'server-2', method: 'test/fail', params: {} });
  child.send({ id: 'server-3', method: 'not/registered', params: {} });
  await waitForMessages(child, 3);

  const byId = Object.fromEntries(child.messages.map(message => [message.id, message]));
  assert.deepEqual(byId['server-1'], { id: 'server-1', result: { echoed: { query: 'fixed' } } });
  assert.deepEqual(byId['server-2'], { id: 'server-2', error: { code: -32010, message: 'denied', data: { reason: 'fixture' } } });
  assert.deepEqual(byId['server-3'], { id: 'server-3', error: { code: -32601, message: 'Method not found: not/registered' } });
  await transport.shutdown();
});

test('streaming parser handles split UTF-8 frames and multiple messages in one chunk', async () => {
  const { child, transport } = createTransport();
  const reply = transport.request('echo');
  const notificationPromise = once(transport, 'notification');
  const body = Buffer.from(`${JSON.stringify({ id: 1, result: { label: 'café' } })}\n${JSON.stringify({ method: 'turn/started', params: { turnId: 'turn-1' } })}\n`);
  const splitAt = body.indexOf(Buffer.from('é')) + 1;
  child.sendBytes(body.subarray(0, splitAt));
  child.sendBytes(body.subarray(splitAt));

  assert.deepEqual(await reply, { label: 'café' });
  const notification = await notificationPromise;
  assert.deepEqual(notification[0], { method: 'turn/started', params: { turnId: 'turn-1' } });
  await transport.shutdown();
});

test('malformed and overlong frames fail the transport and reject pending requests', async t => {
  await t.test('malformed JSON', async () => {
    const { child, transport } = createTransport({ shutdownGraceMs: 0, forceKillGraceMs: 10 });
    const failure = once(transport, 'failure');
    const pending = transport.request('waiting');
    child.sendBytes(Buffer.from('{not-json}\n'));
    await assert.rejects(pending, /Invalid app-server JSONL frame/);
    assert.match((await failure)[0].message, /Invalid app-server JSONL frame/);
    await transport.shutdown();
  });

  await t.test('frame byte limit', async () => {
    const { child, transport } = createTransport({ maxFrameBytes: 8, shutdownGraceMs: 0, forceKillGraceMs: 10 });
    const failure = once(transport, 'failure');
    child.sendBytes(Buffer.from('123456789'));
    assert.match((await failure)[0].message, /exceeded 8 bytes/);
    await transport.shutdown();
  });
});

test('child exit rejects every pending request and emits terminal failure', async () => {
  const { child, transport } = createTransport();
  const failure = once(transport, 'failure');
  const first = transport.request('first');
  const second = transport.request('second');
  child.finish(7);

  await assert.rejects(first, /exited \(code 7/);
  await assert.rejects(second, /exited \(code 7/);
  assert.match((await failure)[0].message, /exited \(code 7/);
});

test('shutdown closes stdin and escalates signals for an owned child that remains open', async () => {
  const child = new FakeChild();
  const killed = [];
  const transport = new AppServerTransport(child, {
    ownsChild: true,
    shutdownGraceMs: 0,
    forceKillGraceMs: 20,
    killChild: (_child, signal) => {
      killed.push(signal);
      if (signal === 'SIGKILL') child.finish(null, signal);
    },
  });
  const pending = transport.request('waiting');
  const pendingRejected = assert.rejects(pending, /transport shut down/);

  const closed = await transport.shutdown();
  await pendingRejected;
  assert.deepEqual(killed, ['SIGTERM', 'SIGKILL']);
  assert.deepEqual(closed, { closed: true });
  assert.equal(transport.closeInfo.graceful, false);
  assert.equal(transport.closeInfo.signal, 'SIGKILL');
  assert.match(transport.closeInfo.error.message, /SIGKILL/);
});

test('nonzero exit after requested shutdown remains visible despite confirmed closure', async () => {
  const child = new FakeChild();
  const transport = new AppServerTransport(child, { ownsChild: true });
  child.stdin.on('finish', () => child.finish(7));
  assert.deepEqual(await transport.shutdown(), { closed: true });
  assert.equal(transport.closeInfo.expected, true);
  assert.equal(transport.closeInfo.graceful, false);
  assert.equal(transport.closeInfo.code, 7);
  assert.match(transport.closeInfo.error.message, /code 7/);
});

test('invalid transport setup is rejected before starting a child process', () => {
  let starts = 0;
  const spawnImpl = () => { starts += 1; return new FakeChild(); };
  for (const transportOptions of [null, { handlers: { 'item/tool/call': undefined } },
    { maxFrameBytes: 0 }, { shutdownGraceMs: -1 }, { killChild: false }]) {
    assert.throws(() => AppServerTransport.spawn('codex', [], { spawnImpl, transportOptions }));
  }
  assert.equal(starts, 0, 'rejected setup must not leak a newly launched native process');
});

test('JSON-RPC error responses reject only the matching request', async () => {
  const { child, transport } = createTransport();
  const first = transport.request('first');
  const second = transport.request('second');
  child.send({ id: child.messages[1].id, error: { code: -32001, message: 'request rejected', data: { retry: false } } });
  child.send({ id: child.messages[0].id, result: 'ok' });

  await assert.rejects(second, error => error instanceof JsonRpcError && error.code === -32001 && error.data.retry === false);
  assert.equal(await first, 'ok');
  await transport.shutdown();
});

test('spawn owns the child and fixes stdio, shell, and window options', async () => {
  const child = new FakeChild();
  let observed;
  const transport = AppServerTransport.spawn('codex', ['app-server'], {
    spawnImpl: (binary, args, options) => { observed = { binary, args, options }; return child; },
    spawnOptions: { cwd: 'C:\\fixture' },
    transportOptions: { shutdownGraceMs: 0, forceKillGraceMs: 20, killChild: (_child, signal) => child.kill(signal) },
  });
  assert.equal(observed.binary, 'codex');
  assert.deepEqual(observed.args, ['app-server']);
  assert.deepEqual(observed.options.stdio, ['pipe', 'pipe', 'pipe']);
  assert.equal(observed.options.shell, false);
  assert.equal(observed.options.windowsHide, true);
  assert.equal(transport.ownsChild, true);
  await transport.shutdown();
});
