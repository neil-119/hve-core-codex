import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile, readFile, realpath } from 'node:fs/promises';
import { appendFileSync, readFileSync } from 'node:fs';
import { isAbsolute, join, resolve as resolvePath } from 'node:path';
import { computeMetrics } from '@microsoft/vally';
import { AppServerTransport } from './app-server.mjs';
import { executeAppServer } from './app-server-execution.mjs';
import { settleAppServerWorkspace } from './settlement.mjs';
import { resolveSkillIsolation as defaultSkillIsolationResolver, VALLY_AMBIENT_CONFIG_ARGS } from './skill-isolation.mjs';
import { classifyNativeCommandExecution } from './shell-normalization.mjs';
import { translateMcpServers, vallyMcpToolName } from './mcp-config-translator.mjs';

const MAX_EVAL_MCP_SERVERS = 256;
const HVE_CONTEXT = JSON.parse(readFileSync(new URL('./navigation.json', import.meta.url), 'utf8'));
const NAVIGATION_GUIDANCE = HVE_CONTEXT.guidance;
const RESPONSE_STYLE = HVE_CONTEXT.responseStyle;
if (typeof RESPONSE_STYLE !== 'string' || !RESPONSE_STYLE.trim()) throw new Error('Missing HVE response style');

// An executor plugin only: Vally owns fixtures, trials, grading and reports.
export function registerExecutors(registry) {
  registry.register(new CodexExecutor());
}

export function parseConfig(config = {}) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Codex config must be an object');
  const allowed = new Set(['binary', 'sandbox', 'reasoningEffort']);
  for (const key of Object.keys(config)) if (!allowed.has(key)) throw new Error(`Unsupported Codex executor config: ${key}`);
  if (config.binary !== undefined && (typeof config.binary !== 'string' || !config.binary)) throw new Error('binary must be a nonempty executable path');
  if (config.sandbox !== undefined && !['read-only', 'workspace-write'].includes(config.sandbox)) throw new Error('sandbox must be read-only or workspace-write');
  if (config.reasoningEffort !== undefined && !['low', 'medium', 'high', 'xhigh', 'max'].includes(config.reasoningEffort)) throw new Error('Unsupported reasoningEffort');
  return { binary: 'codex', sandbox: 'workspace-write', reasoningEffort: 'max', ...config };
}

export function buildAppServerArgs(skillIsolationArgs = []) {
  if (!Array.isArray(skillIsolationArgs) || skillIsolationArgs.some(argument => typeof argument !== 'string'))
    throw new Error('Skill isolation app-server arguments must be an array of strings');
  return ['app-server', '--stdio', ...skillIsolationArgs];
}

function isolationPathKey(value) {
  const normalized = resolvePath(value).replaceAll('\\', '/').replace(/\/$/, '');
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function verifiedIsolationSummary(isolation, selectedPaths) {
  const mcp = isolation?.mcpIsolation;
  if (!mcp || mcp.verified !== true || mcp.advertisedToolCount !== 0 ||
      !Array.isArray(mcp.disabledServerNames) || mcp.disabledServerNames.length > 256 ||
      mcp.disabledServerNames.some(name => typeof name !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(name)) ||
      new Set(mcp.disabledServerNames).size !== mcp.disabledServerNames.length)
    throw new Error('MCP isolation resolver returned incomplete catalog verification evidence');
  const ambientArgs = [...VALLY_AMBIENT_CONFIG_ARGS,
    ...mcp.disabledServerNames.flatMap(name => ['-c', `mcp_servers.${name}.enabled=false`])];
  if (!isolation || !Array.isArray(isolation.configArgs) ||
      isolation.configArgs.length !== 4 + ambientArgs.length || isolation.configArgs[0] !== '-c' ||
      !isolation.configArgs[1]?.startsWith('skills.config=[') || !isolation.configArgs[1].endsWith(']') ||
      isolation.configArgs[2] !== '-c' || isolation.configArgs[3] !== 'project_doc_max_bytes=0' ||
      ambientArgs.some((argument, index) => isolation.configArgs[index + 4] !== argument) ||
      isolation.configArgs.some(argument => typeof argument !== 'string'))
    throw new Error('Skill isolation resolver returned invalid process-local config arguments');
  if (!Array.isArray(isolation.expectedPaths) || !Array.isArray(isolation.enabledPaths) ||
      [...isolation.expectedPaths, ...isolation.enabledPaths].some(path => typeof path !== 'string' || !isAbsolute(path)) ||
      !Number.isSafeInteger(isolation.disabledSkills) || isolation.disabledSkills < 0 ||
      !Number.isFinite(isolation.elapsedMs) || isolation.elapsedMs < 0)
    throw new Error('Skill isolation resolver returned incomplete verification evidence');
  const expected = isolation.expectedPaths.map(isolationPathKey);
  const enabled = isolation.enabledPaths.map(isolationPathKey);
  const selected = selectedPaths.map(isolationPathKey);
  if (new Set(expected).size !== expected.length || new Set(enabled).size !== enabled.length ||
      new Set(selected).size !== selected.length || expected.length !== enabled.length ||
      expected.length !== selected.length || expected.some(path => !enabled.includes(path) || !selected.includes(path)))
    throw new Error('Skill isolation resolver did not verify exactly the selected skills as enabled');
  return {
    verified: true,
    expectedPaths: [...isolation.expectedPaths],
    enabledPaths: [...isolation.enabledPaths],
    disabledSkills: isolation.disabledSkills,
    elapsedMs: isolation.elapsedMs,
    mcpIsolation: { verified: true, disabledServerCount: mcp.disabledServerNames.length,
      advertisedToolCount: 0, exclusiveToolInventoryVerified: false },
  };
}

export function normalizeEvent(raw, turn, model, { platform = process.platform } = {}) {
  const base = { turn, ...(raw.receivedAt ? { timestamp: new Date(raw.receivedAt) } : {}) };
  if (raw.type === 'turn.started') return [{ ...base, type: 'turn_start', data: { turnId: String(turn) } }];
  if (raw.type === 'turn.completed') {
    const result = [{ ...base, type: 'turn_end', data: { turnId: String(turn) } }];
    if (raw.usage) result.push({ ...base, type: 'token_usage', data: {
      inputTokens: raw.usage.input_tokens, outputTokens: raw.usage.output_tokens,
      cacheReadTokens: raw.usage.cached_input_tokens ?? 0, model,
    } });
    return result;
  }
  if (raw.type === 'error' || raw.type === 'turn.failed') return [{ ...base, type: 'error', data: {
    message: raw.message ?? raw.error?.message ?? 'Codex turn failed',
    ...(raw.willRetry !== undefined ? { willRetry: raw.willRetry } : {}),
  } }];
  const item = raw.item;
  if (!item) return [];
  if (raw.type === 'item.completed' && item.type === 'agent_message') return [{ ...base, type: 'assistant_message', data: { content: item.text } }];
  if (raw.type === 'item.completed' && item.type === 'reasoning') return [{ ...base, type: 'reasoning', data: { content: item.text } }];
  if (item.type === 'sub_agent_activity') return [{ ...base, type: 'custom', data: {
    source: 'codex-app-server', event: raw,
    childActivity: {
      nativeType: item.native_type, itemId: item.id, kind: item.kind,
      childThreadId: item.agentThreadId, agentPath: item.agentPath,
      ...(typeof item.model === 'string' ? { model: item.model } : {}),
      ...(typeof item.reasoningEffort === 'string' ? { reasoningEffort: item.reasoningEffort } : {}),
    },
  } }];
  if (item.type === 'web_search') {
    if (raw.type === 'item.started') return [{ ...base, type: 'tool_call', data: {
      toolName: 'web_search', toolCallId: item.id,
      arguments: { ...(item.query !== undefined ? { query: item.query } : {}), ...(item.action ? { action: item.action } : {}) },
    } }];
    if (raw.type === 'item.completed') return [{ ...base, type: 'tool_result', data: {
      toolName: 'web_search', toolCallId: item.id,
      ...(Array.isArray(item.results) ? { success: true } : {}),
      result: { ...(item.query !== undefined ? { query: item.query } : {}),
        ...(item.action ? { action: item.action } : {}), ...(item.results !== undefined ? { results: item.results } : {}) },
    } }];
  }
  if (item.type === 'dynamic_tool_call') {
    if (raw.type === 'item.started') return [{ ...base, type: 'tool_call', data: {
      toolName: item.tool, toolCallId: item.id, arguments: item.arguments,
      ...(item.namespace ? { namespace: item.namespace } : {}),
    } }];
    if (raw.type === 'item.completed') return [{ ...base, type: 'tool_result', data: {
      toolName: item.tool, toolCallId: item.id,
      ...(typeof item.success === 'boolean' ? { success: item.success } : item.status === 'failed' ? { success: false } : {}),
      result: { status: item.status, ...(item.durationMs !== undefined ? { durationMs: item.durationMs } : {}),
        ...(item.contentItems !== undefined ? { contentItems: item.contentItems } : {}) },
      ...(item.namespace ? { namespace: item.namespace } : {}),
    } }];
  }
  if (item.type === 'command_execution') {
    const shell = classifyNativeCommandExecution(item, { platform });
    const verifiedShell = shell.status === 'verified';
    const toolName = verifiedShell ? shell.logical.toolName : 'exec_command';
    const arguments_ = verifiedShell ? shell.logical.arguments : { command: item.command, cmd: item.command };
    if (raw.type === 'item.started') return [{ ...base, type: 'tool_call', data: {
      toolName, toolCallId: item.id, arguments: arguments_, shellNormalization: shell,
    } }];
    if (raw.type === 'item.completed') return [{ ...base, type: 'tool_result', data: {
      toolName, toolCallId: item.id, success: item.exit_code === 0, shellNormalization: shell,
      result: typeof item.aggregated_output === 'string' ? item.aggregated_output : '',
      ...(item.exit_code !== undefined ? { exit_code: item.exit_code } : {}),
      ...(item.status !== undefined ? { status: item.status } : {}),
    } }];
  }
  if (item.type === 'mcp_tool_call') {
    const toolName = vallyMcpToolName(item.server, item.tool);
    const nativeMcp = { server: item.server, tool: item.tool };
    if (raw.type === 'item.started') return [{ ...base, type: 'tool_call', data: {
      toolName, toolCallId: item.id, arguments: item.arguments, nativeMcp,
    } }];
    if (raw.type === 'item.completed') return [{ ...base, type: 'tool_result', data: {
      toolName, toolCallId: item.id, success: item.status === 'completed', result: item.result ?? item.error,
      ...(item.error !== undefined ? { error: item.error } : {}), nativeMcp,
    } }];
  }
  if (item.type === 'collab_tool_call' || item.type === 'file_change') {
    const toolName = item.type === 'file_change' ? 'apply_patch' : item.tool;
    if (raw.type === 'item.started') return [{ ...base, type: 'tool_call', data: {
      toolName, toolCallId: item.id, arguments: item.type === 'file_change' ? { changes: item.changes } : {
        prompt: item.prompt, receiver_thread_ids: item.receiver_thread_ids,
      },
    } }];
    if (raw.type === 'item.completed') return [{ ...base, type: 'tool_result', data: {
      toolName, toolCallId: item.id, success: item.status === 'completed', result: item,
    } }];
  }
  // Preserve other native observations without inventing activation/tool evidence.
  return [{ ...base, type: 'custom', data: { source: 'codex-cli', event: raw } }];
}

export class CodexExecutor {
  name = 'codex';
  supportsPreparedWorkspace = true;
  supportsMultiTurn = true;
  supportsTurnCompletion = true;
  supportsEnvVars = true;
  supportsSimulation = false;
  supportsEvalMcp = false;
  children = new Set();
  appServers = new Set();
  constructor({ appServerFactory, settleWorkspace, binary, reasoningEffort, navigationContext = false,
    enableEvalMcp = false,
    skillIsolationResolver = defaultSkillIsolationResolver } = {}) {
    parseConfig({ ...(binary !== undefined ? { binary } : {}),
      ...(reasoningEffort !== undefined ? { reasoningEffort } : {}) });
    if (typeof skillIsolationResolver !== 'function') throw new Error('skillIsolationResolver must be a function');
    if (typeof enableEvalMcp !== 'boolean') throw new Error('enableEvalMcp must be a boolean');
    this.binary = binary;
    if (typeof navigationContext !== 'boolean') throw new Error('navigationContext must be boolean');
    this.navigationContext = navigationContext;
    this.reasoningEffort = reasoningEffort;
    this.supportsEvalMcp = enableEvalMcp;
    this.skillIsolationResolver = skillIsolationResolver;
    this.appServerFactory = appServerFactory ?? ((binary, options) => AppServerTransport.spawn(binary,
      buildAppServerArgs(options.skillIsolationArgs ?? []), { spawnOptions: { cwd: options.workDir, env: { ...process.env, ...options.env } },
        transportOptions: { handlers: {
          'item/commandExecution/requestApproval': () => ({ decision: 'decline' }),
          'item/fileChange/requestApproval': () => ({ decision: 'decline' }),
          'item/permissions/requestApproval': () => ({ permissions: {}, scope: 'turn' }),
        } } }));
    this.settleWorkspace = settleWorkspace ?? settleAppServerWorkspace;
  }
  validateConfig(config) { parseConfig(config); }

  async execute(stimulus, options) {
    if (stimulus?.simulation !== undefined && stimulus.simulation !== null)
      throw new Error('Codex executor does not support Vally simulation');
    const config = parseConfig(options.executorConfig);
    if (this.binary !== undefined) config.binary = this.binary;
    if (this.reasoningEffort !== undefined) options = { ...options, reasoningEffort: this.reasoningEffort };
    const translatedMcp = translateMcpServers(options.mcpServers);
    const requestedMcpServers = translatedMcp.config?.mcp_servers;
    const inputMcpServerNames = options.mcpServers === undefined ? [] : Object.keys(options.mcpServers);
    const hasMcpServers = inputMcpServerNames.length > 0;
    if (inputMcpServerNames.length > MAX_EVAL_MCP_SERVERS ||
        inputMcpServerNames.some(name => !/^[A-Za-z0-9_-]{1,128}$/.test(name)))
      throw new Error('Vally MCP server map has an invalid name or exceeds the server limit');
    if (hasMcpServers && !this.supportsEvalMcp)
      throw new Error('Codex executor eval-level MCP support is disabled pending explicit opt-in');
    if (stimulus.attachments?.length) throw new Error('Codex executor does not yet support attachments');
    if (!Number.isFinite(options.timeout) || options.timeout <= 0) throw new Error('Invalid execution deadline');
    const hardDeadline = Date.now() + options.timeout;
    const isolation = await this.skillIsolationResolver({ binary: config.binary, workDir: options.workDir,
      skills: options.skills ?? [], timeoutMs: options.timeout, env: options.env ?? {} });
    const remainingHard = hardDeadline - Date.now();
    if (remainingHard <= 0) throw new Error('Codex execution hard deadline exhausted during skill isolation');
    const selectedSkillPaths = (options.skills ?? []).map(skill => {
      if (!skill || typeof skill.name !== 'string' || !/^[A-Za-z0-9_-]+$/.test(skill.name))
        throw new Error('Vally selected skill metadata is invalid');
      return resolvePath(join(options.workDir, '.agents', 'skills', skill.name, 'SKILL.md'));
    });
    const isolationEvidence = verifiedIsolationSummary(isolation, selectedSkillPaths);
    const contextPath = join(options.workDir, '.github', 'copilot-instructions.md');
    let context;
    try {
      const canonical = await realpath(contextPath);
      const root = await realpath(options.workDir);
      if (!canonical.startsWith(root + (process.platform === 'win32' ? '\\' : '/'))) throw new Error('Fixture instruction escapes workspace');
      context = await readFile(canonical, 'utf8');
      if (Buffer.byteLength(context) > 128 * 1024) throw new Error('Fixture instruction exceeds 128 KiB');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const developerContext = [RESPONSE_STYLE, this.navigationContext ? NAVIGATION_GUIDANCE : undefined, context].filter(value => value !== undefined).join('\n\n');
    const contextArgs = developerContext ? ['-c', `developer_instructions=${JSON.stringify(developerContext)}`] : [];
    options = { ...options, timeout: remainingHard, skillIsolationArgs: [...isolation.configArgs, ...contextArgs] };
    const model = options.model ?? 'gpt-6-luna';
    const skillsLoaded = (options.skills ?? []).map(skill => skill.name);
    if (options.sessionLog) {
      await mkdir(options.sessionLog.rootDir, { recursive: true });
      await writeFile(join(options.sessionLog.rootDir, 'events.jsonl'), '');
    }
    if (hasMcpServers || Number.isFinite(options.maxAgentDurationMs) && options.maxAgentDurationMs > 0) {
      let client;
      try {
        client = this.appServerFactory(config.binary, options);
        this.appServers.add(client);
        const trajectory = await executeAppServer(stimulus, options, config, {
          client, settleWorkspace: this.settleWorkspace, normalizeEvent, requestedMcpServers,
          disabledMcpServerNames: [...isolation.mcpIsolation.disabledServerNames],
        });
        trajectory.metadata.responseStyleVersion = HVE_CONTEXT.responseStyleVersion;
        trajectory.metadata.navigationContext = this.navigationContext;
        trajectory.metadata.fixtureInstructionsDelivered = context !== undefined;
        trajectory.metadata.skillsLoaded = skillsLoaded;
        trajectory.metadata.skillIsolation = isolationEvidence;
        return trajectory;
      } finally {
        if (client) this.appServers.delete(client);
      }
    }
    const startedAt = new Date();
    const events = [];
    const rawEvents = [];
    let sessionID = options.sessionID;
    let output = '';
    let remaining = options.timeout;
    const turns = stimulus.turns ?? [{ prompt: stimulus.prompt }];
    for (let turn = 0; turn < turns.length; turn++) {
      const prompt = typeof turns[turn] === 'string' ? turns[turn] : turns[turn].prompt;
      if (typeof prompt !== 'string') throw new Error('Missing configured turn prompt');
      events.push({ type: 'user_message', timestamp: new Date(), turn, data: { content: prompt } });
      const args = ['exec', '--json', '--ignore-user-config', '--skip-git-repo-check', '-C', options.workDir, '-s', config.sandbox, '-m', model,
        '-c', `model_reasoning_effort=${JSON.stringify(options.reasoningEffort ?? config.reasoningEffort)}`,
        '-c', `agents.default_subagent_model=${JSON.stringify(model)}`,
        '-c', `agents.default_subagent_reasoning_effort=${JSON.stringify(options.reasoningEffort ?? config.reasoningEffort)}`,
        ...options.skillIsolationArgs];
      if (sessionID) args.push('resume', sessionID);
      args.push('-');
      const began = Date.now();
      const records = await this.run(config.binary, args, prompt, options, remaining);
      remaining -= Date.now() - began;
      let completed = false;
      const threadEvents = records.filter(raw => raw.type === 'thread.started');
      if (threadEvents.length !== 1 || typeof threadEvents[0].thread_id !== 'string' || !threadEvents[0].thread_id)
        throw new Error('Codex must report exactly one nonempty thread identity');
      if (sessionID && threadEvents[0].thread_id !== sessionID) throw new Error('Codex resumed a different thread');
      for (const raw of records) {
        rawEvents.push(raw);
        if (raw.type === 'thread.started') sessionID = raw.thread_id;
        if (raw.type === 'turn.completed') completed = true;
        events.push(...normalizeEvent(raw, turn, model));
        if (raw.type === 'item.completed' && raw.item?.type === 'agent_message') output = raw.item.text;
      }
      if (!completed || events.some(e => e.turn === turn && e.type === 'error')) throw new Error('Codex did not complete the configured turn successfully');
      if (!sessionID) throw new Error('Codex did not report a session ID for resume');
      await options.onTurnComplete?.({ turn, status: 'completed', final: turn === turns.length - 1 });
    }
    return { id: randomUUID(), stimulus, events, output, workDir: options.workDir, endReason: 'completed',
      metadata: { responseStyleVersion: HVE_CONTEXT.responseStyleVersion, startedAt, completedAt: new Date(), model, executor: this.name, sessionID, skillsLoaded, fixtureInstructionsDelivered: context !== undefined, navigationContext: this.navigationContext,
        skillIsolation: isolationEvidence },
      metrics: { ...computeMetrics(events), wallTimeMs: Date.now() - startedAt.getTime() } };
  }

  run(binary, args, prompt, options, timeout) {
    if (!Number.isFinite(timeout) || timeout <= 0) throw new Error('Codex execution deadline exhausted');
    return new Promise((resolve, reject) => {
      const child = spawn(binary, args, { cwd: options.workDir, env: { ...process.env, ...options.env }, shell: false, windowsHide: true, detached: process.platform !== 'win32' });
      this.children.add(child);
      let pending = '', stderr = '', error, bytes = 0, settled = false;
      const records = [];
      const stop = err => {
        error ??= err;
        void this.terminate(child).then(() => {
          if (!settled) { settled = true; clearTimeout(timer); reject(error); }
        });
      };
      const timer = setTimeout(() => stop(new Error('Codex execution timed out')), timeout);
      const consume = line => {
        if (options.sessionLog) appendFileSync(join(options.sessionLog.rootDir, 'events.jsonl'), line + '\n');
        if (!line.trim()) return;
        const raw = { ...JSON.parse(line), receivedAt: new Date().toISOString() };
        records.push(raw);
        options.onRawEvent?.(raw);
      };
      child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
      child.stdout.on('data', data => {
        if (error || settled) return;
        bytes += Buffer.byteLength(data);
        if (bytes > 32 * 1024 * 1024) return stop(new Error('Codex JSONL exceeded 32 MiB'));
        pending += data;
        try {
          let newline;
          while ((newline = pending.indexOf('\n')) !== -1) {
            const line = pending.slice(0, newline).replace(/\r$/, ''); pending = pending.slice(newline + 1); consume(line);
          }
        } catch (err) { stop(new Error(`Invalid Codex JSONL or diagnostic callback: ${err.message}`)); }
      });
      child.stderr.on('data', data => { stderr = (stderr + data).slice(-8192); });
      child.on('error', err => { error = err; });
      child.on('close', code => {
        clearTimeout(timer); this.children.delete(child);
        if (settled) return;
        settled = true;
        if (error) return reject(error);
        try { if (pending) consume(pending); }
        catch (err) { return reject(new Error(`Invalid Codex JSONL: ${err.message}`)); }
        if (code !== 0) return reject(new Error(`Codex exited ${code}: ${stderr}`));
        resolve(records);
      });
      child.stdin.on('error', err => stop(err));
      child.stdin.end(prompt);
    });
  }
  async terminate(child) {
    if (child.exitCode !== null || child.signalCode !== null) return;
    if (process.platform === 'win32' && child.pid) {
      const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      killer.on('error', () => child.kill());
      const timer = setTimeout(() => killer.kill(), 1500); timer.unref();
      killer.once('close', () => clearTimeout(timer));
    } else if (child.pid) {
      try { process.kill(-child.pid, 'SIGTERM'); } catch { child.kill(); }
    }
    await new Promise(resolve => {
      let forcedTimer;
      const timer = setTimeout(() => {
        if (process.platform !== 'win32' && child.pid) { try { process.kill(-child.pid, 'SIGKILL'); } catch {} }
        child.kill();
        forcedTimer = setTimeout(resolve, 500);
      }, 2000);
      child.once('close', () => { clearTimeout(timer); clearTimeout(forcedTimer); resolve(); });
    });
  }
  async shutdown() { await Promise.all([
    ...[...this.children].map(child => this.terminate(child)),
    ...[...this.appServers].map(client => client.shutdown()),
  ]); }
}
