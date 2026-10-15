import { spawn as spawnProcess } from 'node:child_process';
import { EventEmitter } from 'node:events';

const DEFAULT_MAX_FRAME_BYTES = 32 * 1024 * 1024;

function validateTransportOptions(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options))
    throw new Error('App-server transport options must be an object');
  const { handlers = {}, maxFrameBytes = DEFAULT_MAX_FRAME_BYTES,
    shutdownGraceMs = 500, forceKillGraceMs = 1000, killChild } = options;
  if (!Number.isSafeInteger(maxFrameBytes) || maxFrameBytes < 1)
    throw new Error('maxFrameBytes must be a positive safe integer');
  if (!Number.isFinite(shutdownGraceMs) || shutdownGraceMs < 0 ||
      !Number.isFinite(forceKillGraceMs) || forceKillGraceMs < 0)
    throw new Error('Shutdown grace periods must be nonnegative numbers');
  if (!handlers || typeof handlers !== 'object' || Array.isArray(handlers))
    throw new Error('Server request handlers must be an object');
  for (const [method, handler] of Object.entries(handlers))
    if (!method || typeof handler !== 'function')
      throw new Error('Each server request handler must be a function keyed by method');
  if (killChild !== undefined && typeof killChild !== 'function')
    throw new Error('killChild must be a function');
}

export class JsonRpcError extends Error {
  constructor(error) {
    super(error.message);
    this.name = 'JsonRpcError';
    this.code = error.code;
    if (Object.hasOwn(error, 'data')) this.data = error.data;
  }
}

// Codex app-server uses newline-delimited JSON-RPC messages over stdio. This
// class owns framing and correlation only; callers choose the methods/params
// and explicitly provide handlers for server-initiated requests.
export class AppServerTransport extends EventEmitter {
  static spawn(binary, args = [], { spawnImpl = spawnProcess, spawnOptions = {}, transportOptions = {} } = {}) {
    if (typeof binary !== 'string' || !binary) throw new Error('App-server binary must be a nonempty executable path');
    if (!Array.isArray(args) || args.some(argument => typeof argument !== 'string')) throw new Error('App-server args must be an array of strings');
    validateTransportOptions(transportOptions);
    const child = spawnImpl(binary, args, {
      ...spawnOptions,
      stdio: ['pipe', 'pipe', 'pipe'],
      shell: false,
      windowsHide: true,
      detached: process.platform !== 'win32',
    });
    return new AppServerTransport(child, { ...transportOptions, ownsChild: true });
  }

  constructor(child, {
    handlers = {},
    ownsChild = false,
    maxFrameBytes = DEFAULT_MAX_FRAME_BYTES,
    shutdownGraceMs = 500,
    forceKillGraceMs = 1000,
    killChild,
  } = {}) {
    super();
    if (!child?.stdin?.write || !child?.stdout?.on || !child?.stderr?.on || !child?.on)
      throw new Error('App-server child must expose stdin, stdout, stderr, and process events');
    if (!Number.isSafeInteger(maxFrameBytes) || maxFrameBytes < 1) throw new Error('maxFrameBytes must be a positive safe integer');
    if (!Number.isFinite(shutdownGraceMs) || shutdownGraceMs < 0 || !Number.isFinite(forceKillGraceMs) || forceKillGraceMs < 0)
      throw new Error('Shutdown grace periods must be nonnegative numbers');
    if (!handlers || typeof handlers !== 'object' || Array.isArray(handlers)) throw new Error('Server request handlers must be an object');
    for (const [method, handler] of Object.entries(handlers)) {
      if (!method || typeof handler !== 'function') throw new Error('Each server request handler must be a function keyed by method');
    }

    this.child = child;
    this.ownsChild = ownsChild;
    this.handlers = new Map(Object.entries(handlers));
    this.maxFrameBytes = maxFrameBytes;
    this.shutdownGraceMs = shutdownGraceMs;
    this.forceKillGraceMs = forceKillGraceMs;
    this.killChild = killChild;
    this.pending = new Map();
    this.nextRequestId = 1;
    this.pendingFrameChunks = [];
    this.pendingFrameBytes = 0;
    this.closed = false;
    this.closeInfo = null;
    this.shutdownRequested = false;
    this.failure = null;
    this.failureEmitted = false;
    this.shutdownPromise = null;
    this.initialized = false;
    this.initializing = false;
    this.stderrTail = '';
    this.closePromise = new Promise(resolve => { this.resolveClose = resolve; });

    child.stdout.on('data', chunk => this.#onStdoutData(chunk));
    child.stdout.on('end', () => this.#onStdoutEnd());
    child.stdout.on('error', error => this.#fail(error));
    child.stderr.on('data', chunk => this.#recordStderr(chunk));
    child.stderr.on('error', error => this.emit('stderrError', error));
    child.stdin.on('error', error => {
      if (!this.shutdownRequested) this.#fail(error);
    });
    child.on('error', error => this.#finish(error, null, null));
    child.on('close', (code, signal) => this.#finish(null, code, signal));
  }

  async initialize({ clientInfo, capabilities } = {}) {
    if (this.initialized || this.initializing) throw new Error('App-server transport has already initialized');
    if (!clientInfo || typeof clientInfo !== 'object' || Array.isArray(clientInfo) ||
        typeof clientInfo.name !== 'string' || typeof clientInfo.version !== 'string')
      throw new Error('initialize requires clientInfo.name and clientInfo.version');
    if (capabilities !== undefined && (!capabilities || typeof capabilities !== 'object' || Array.isArray(capabilities)))
      throw new Error('initialize capabilities must be an object');

    this.initializing = true;
    try {
      const params = { clientInfo };
      if (capabilities !== undefined) params.capabilities = capabilities;
      const response = await this.request('initialize', params);
      this.notify('initialized');
      this.initialized = true;
      return response;
    } finally {
      this.initializing = false;
    }
  }

  request(method, params) {
    this.#assertWritable();
    if (typeof method !== 'string' || !method) throw new Error('JSON-RPC method must be a nonempty string');
    const id = this.nextRequestId++;
    const message = { id, method };
    if (params !== undefined) message.params = params;

    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try {
        this.#write(message);
      } catch (error) {
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  notify(method, params) {
    this.#assertWritable();
    if (typeof method !== 'string' || !method) throw new Error('JSON-RPC method must be a nonempty string');
    const message = { method };
    if (params !== undefined) message.params = params;
    this.#write(message);
  }

  #assertWritable() {
    if (this.failure) throw this.failure;
    if (this.closed || this.shutdownRequested) throw new Error('App-server transport is closed');
  }

  #write(message) {
    let serialized;
    try { serialized = JSON.stringify(message); }
    catch (error) { throw new Error(`Could not serialize app-server message: ${error.message}`); }
    if (typeof serialized !== 'string') throw new Error('App-server message did not serialize to JSON');
    const line = `${serialized}\n`;
    if (Buffer.byteLength(line) > this.maxFrameBytes) throw new Error('App-server outbound frame exceeds the configured byte limit');
    this.child.stdin.write(line, error => {
      if (error && !this.shutdownRequested) this.#fail(error);
    });
  }

  #onStdoutData(chunk) {
    if (this.closed || this.failure) return;
    try {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      let offset = 0;
      while (offset < bytes.length) {
        const newline = bytes.indexOf(0x0a, offset);
        const end = newline === -1 ? bytes.length : newline;
        this.#appendFrame(bytes.subarray(offset, end));
        offset = newline === -1 ? bytes.length : newline + 1;
        if (newline !== -1) this.#consumeFrame();
      }
    } catch (error) {
      this.#fail(error);
    }
  }

  #appendFrame(piece) {
    if (this.pendingFrameBytes + piece.length > this.maxFrameBytes)
      throw new Error(`App-server frame exceeded ${this.maxFrameBytes} bytes`);
    if (piece.length) {
      this.pendingFrameChunks.push(Buffer.from(piece));
      this.pendingFrameBytes += piece.length;
    }
  }

  #onStdoutEnd() {
    if (this.closed || this.failure || this.pendingFrameBytes === 0) return;
    try { this.#consumeFrame(); }
    catch (error) { this.#fail(error); }
  }

  #consumeFrame() {
    let frame = this.pendingFrameChunks.length === 1
      ? this.pendingFrameChunks[0]
      : Buffer.concat(this.pendingFrameChunks, this.pendingFrameBytes);
    this.pendingFrameChunks = [];
    this.pendingFrameBytes = 0;
    if (frame.at(-1) === 0x0d) frame = frame.subarray(0, -1);
    if (!frame.length) return;

    let message;
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(frame);
      this.emit('rawFrame', text);
      message = JSON.parse(text);
    } catch (error) {
      throw new Error(`Invalid app-server JSONL frame: ${error.message}`);
    }
    if (!message || typeof message !== 'object' || Array.isArray(message))
      throw new Error('Invalid app-server JSON-RPC message: expected an object');
    this.emit('rawMessage', message);
    this.#dispatch(message);
  }

  #dispatch(message) {
    const hasId = Object.hasOwn(message, 'id');
    if (typeof message.method === 'string' && message.method) {
      if (hasId) {
        if (!this.#validId(message.id)) throw new Error('Invalid app-server request id');
        void this.#handleServerRequest(message);
        return;
      }
      this.emit('notification', { method: message.method, params: message.params });
      return;
    }

    if (hasId && (Object.hasOwn(message, 'result') || Object.hasOwn(message, 'error'))) {
      if (!this.#validId(message.id)) throw new Error('Invalid app-server response id');
      if (Object.hasOwn(message, 'result') === Object.hasOwn(message, 'error'))
        throw new Error('Invalid app-server response: expected exactly one of result or error');
      const pending = this.pending.get(message.id);
      if (!pending) {
        this.emit('orphanResponse', message);
        return;
      }
      if (Object.hasOwn(message, 'error')) {
        const error = message.error;
        if (!error || typeof error !== 'object' || !Number.isInteger(error.code) || typeof error.message !== 'string')
          throw new Error('Invalid app-server JSON-RPC error response');
        this.pending.delete(message.id);
        pending.reject(new JsonRpcError(error));
      } else {
        this.pending.delete(message.id);
        pending.resolve(message.result);
      }
      return;
    }

    throw new Error('Invalid app-server JSON-RPC message shape');
  }

  #validId(id) {
    return typeof id === 'string' || (typeof id === 'number' && Number.isSafeInteger(id));
  }

  async #handleServerRequest(message) {
    const handler = this.handlers.get(message.method);
    if (!handler) {
      this.#writeServerResponse({ id: message.id, error: { code: -32601, message: `Method not found: ${message.method}` } });
      return;
    }
    try {
      this.emit('serverRequest', { method: message.method, id: message.id, params: message.params });
      const result = await handler(message.params, message);
      this.#writeServerResponse({ id: message.id, result: result === undefined ? null : result });
    } catch (error) {
      const code = Number.isInteger(error?.code) ? error.code : -32603;
      const rpcError = { code, message: String(error?.message ?? error) };
      if (error && Object.hasOwn(error, 'data')) rpcError.data = error.data;
      this.#writeServerResponse({ id: message.id, error: rpcError });
    }
  }

  #writeServerResponse(response) {
    if (this.closed || this.shutdownRequested) return;
    try { this.#write(response); }
    catch (error) { this.#fail(error); }
  }

  #recordStderr(chunk) {
    const text = Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk);
    this.stderrTail = (this.stderrTail + text).slice(-8192);
  }

  #rejectPending(error) {
    for (const { reject } of this.pending.values()) reject(error);
    this.pending.clear();
  }

  #emitFailure(error) {
    if (this.failureEmitted) return;
    this.failureEmitted = true;
    this.emit('failure', error);
  }

  #fail(error) {
    if (this.failure || this.closed || this.shutdownRequested) return;
    this.failure = error instanceof Error ? error : new Error(String(error));
    this.#rejectPending(this.failure);
    this.#emitFailure(this.failure);
    void this.shutdown().catch(shutdownError => this.emit('shutdownError', shutdownError));
  }

  #finish(error, code, signal) {
    if (this.closed) return;
    this.closed = true;
    const exitError = error ?? new Error(`App-server child exited (code ${code ?? 'unknown'}, signal ${signal ?? 'none'})${this.stderrTail ? `: ${this.stderrTail}` : ''}`);
    this.#rejectPending(this.failure ?? exitError);
    if (error && !this.failure) this.failure = error;
    if (!this.shutdownRequested) this.#emitFailure(this.failure ?? exitError);
    const graceful = this.shutdownRequested && !this.failure && !error && code === 0 && !signal;
    const info = Object.freeze({ code, signal, expected: this.shutdownRequested, graceful,
      error: this.failure ?? (graceful ? null : exitError) });
    this.closeInfo = info;
    this.resolveClose(info);
    this.emit('close', info);
  }

  async #waitForClose(milliseconds) {
    if (this.closed) return true;
    let timer;
    const timeout = new Promise(resolve => { timer = setTimeout(() => resolve(false), milliseconds); });
    const closed = this.closePromise.then(() => true);
    const result = await Promise.race([closed, timeout]);
    clearTimeout(timer);
    return result;
  }

  async #sendSignal(signal) {
    if (this.killChild) return this.killChild(this.child, signal);
    if (this.child.pid && process.platform !== 'win32' && this.child.spawnargs && this.child.spawnargs.length && this.child.spawnfile) {
      // spawn() below uses a new process group on Unix so owned descendants can
      // be stopped with the app-server process.
      try { process.kill(-this.child.pid, signal); return true; }
      catch { /* Fall back to the direct child if it was not a process group. */ }
    }
    return this.child.kill?.(signal) ?? false;
  }

  shutdown({ graceMs = this.shutdownGraceMs, forceKillGraceMs = this.forceKillGraceMs } = {}) {
    if (this.shutdownPromise) return this.shutdownPromise;
    if (!Number.isFinite(graceMs) || graceMs < 0 || !Number.isFinite(forceKillGraceMs) || forceKillGraceMs < 0)
      return Promise.reject(new Error('Shutdown grace periods must be nonnegative numbers'));
    this.shutdownRequested = true;
    this.#rejectPending(new Error('App-server transport shut down'));
    this.shutdownPromise = (async () => {
      try { if (!this.child.stdin.destroyed && !this.child.stdin.writableEnded) this.child.stdin.end(); } catch {}
      if (this.closed) return { closed: true };
      if (!this.ownsChild) return { closed: false, owned: false };
      if (await this.#waitForClose(graceMs)) return { closed: true };

      await this.#sendSignal('SIGTERM');
      if (await this.#waitForClose(graceMs)) return { closed: true };

      await this.#sendSignal('SIGKILL');
      if (await this.#waitForClose(forceKillGraceMs)) return { closed: true };
      throw new Error(`Owned app-server child ${this.child.pid ?? '(unknown pid)'} did not exit after termination signals`);
    })();
    return this.shutdownPromise;
  }
}
