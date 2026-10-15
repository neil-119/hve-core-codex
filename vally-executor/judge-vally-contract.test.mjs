import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createDefaultGraderRegistry, gradeTrajectory } from '@microsoft/vally';
import { CodexJudgeClient, JUDGE_COMPATIBILITY } from './judge.mjs';

function validGrade() {
  return {
    rubric_scores: [
      { criterion: 'Correctness', score: 1, reasoning: 'The response satisfies the task.' },
      { criterion: 'Safety', score: 1, reasoning: 'The response avoids unsafe behavior.' },
    ],
    overall_score: 1,
    overall_reasoning: 'Both criteria pass.',
  };
}

class MockAppServer extends EventEmitter {
  constructor({ handlers, steps, calls, index }) {
    super();
    this.handlers = handlers;
    this.steps = [...steps];
    this.calls = calls;
    this.index = index;
    this.threadId = `mock-thread-${index}`;
    this.turnNumber = 0;
    this.closed = false;
    this.threadParams = undefined;
  }

  async initialize() {}

  async request(method, params) {
    this.calls.push({ session: this.index, method, params });
    if (method === 'thread/start') {
      this.threadParams = params;
      return { thread: { id: this.threadId }, model: 'gpt-6-luna', reasoningEffort: 'max' };
    }
    if (method === 'turn/start') {
      assert.equal(params.threadId, this.threadId);
      assert.equal(params.model, 'gpt-6-luna');
      assert.equal(params.effort, 'max');
      const turnId = `mock-turn-${this.index}-${++this.turnNumber}`;
      const step = this.steps.shift() ?? { call: false };
      setImmediate(() => { void this.completeTurn(turnId, step); });
      return { turn: { id: turnId, status: 'inProgress' } };
    }
    if (method === 'turn/interrupt') return { turn: { id: params.turnId, status: 'interrupted' } };
    throw new Error(`Unexpected app-server request: ${method}`);
  }

  async completeTurn(turnId, step) {
    try {
      this.emit('notification', { method: 'turn/started', params: {
        threadId: this.threadId, turn: { id: turnId, status: 'inProgress' },
      } });
      for (const toolName of step.evidenceTools ?? []) {
        const tool = this.threadParams.dynamicTools.find(candidate => candidate.name === toolName);
        assert.ok(tool, `Vally evidence tool ${toolName} is included in thread/start`);
        const path = tool.inputSchema.properties.path?.enum?.[0] ??
          tool.inputSchema.properties.paths?.items?.enum?.[0];
        assert.ok(path, `${toolName} is scoped to at least one Vally evidence file`);
        const arguments_ = toolName === 'view'
          ? { path, view_range: [1, -1] }
          : { pattern: 'parser', paths: [path] };
        const response = await this.handlers['item/tool/call']({
          threadId: this.threadId,
          turnId,
          callId: `mock-evidence-${this.index}-${this.turnNumber}-${toolName}`,
          namespace: 'functions',
          tool: toolName,
          arguments: arguments_,
        });
        this.calls.push({ session: this.index, method: `${toolName}-result`, response });
      }
      if (step.call) {
        const response = await this.handlers['item/tool/call']({
          threadId: this.threadId,
          turnId,
          callId: `mock-call-${this.index}-${this.turnNumber}`,
          namespace: 'functions',
          tool: step.tool ?? 'submit_grade',
          arguments: step.arguments,
        });
        this.calls.push({ session: this.index, method: 'tool-result', response });
      }
      const n = this.turnNumber;
      this.emit('notification', { method: 'thread/tokenUsage/updated', params: {
        threadId: this.threadId,
        tokenUsage: { total: { inputTokens: 40 * n, outputTokens: 10 * n,
          cachedInputTokens: 0, reasoningOutputTokens: 0 } },
      } });
      this.emit('notification', { method: 'turn/completed', params: {
        threadId: this.threadId, turn: { id: turnId, status: 'completed' },
      } });
    } catch (error) {
      this.calls.push({ session: this.index, method: 'mock-error', error: String(error) });
      this.emit('notification', { method: 'turn/completed', params: {
        threadId: this.threadId, turn: { id: turnId, status: 'failed', error: String(error) },
      } });
    }
  }

  async shutdown() {
    this.closed = true;
    return { closed: true };
  }
}

function mockJudgeClient(scripts) {
  const calls = [];
  const servers = [];
  const client = new CodexJudgeClient({
    binary: 'mock-codex',
    auditDir: undefined,
    skillIsolationResolver: async () => ({ configArgs: [], disabledSkills: 0, mcpIsolation: { verified: true } }),
    appServerFactory: (_binary, { handlers }) => {
      const server = new MockAppServer({ handlers, steps: scripts.shift() ?? [], calls, index: servers.length + 1 });
      servers.push(server);
      return server;
    },
  });
  return { client, calls, servers };
}

test('Vally prompt and panel graders use their original rubric prompt, schema, and verdict scoring through app-server', async () => {
  const invalidGrade = { rubric_scores: [], overall_score: 1, overall_reasoning: 'Invalid empty rubric.' };
  const { client, calls, servers } = mockJudgeClient([
    [
      { call: true, evidenceTools: ['view', 'grep'], arguments: invalidGrade },
      { call: true, arguments: validGrade() },
    ],
    [{ call: true, evidenceTools: ['view', 'grep'], arguments: validGrade() }],
  ]);
  const repoCache = resolve(dirname(fileURLToPath(import.meta.url)), '..', '.cache');
  await mkdir(repoCache, { recursive: true });
  const workDir = await mkdtemp(join(repoCache, 'hve-vally-judge-contract-'));
  const priorTempEnvironment = Object.fromEntries(['TEMP', 'TMP', 'TMPDIR']
    .map(name => [name, process.env[name]]));
  for (const name of Object.keys(priorTempEnvironment)) process.env[name] = repoCache;
  const stimulus = {
    name: 'judge-contract',
    prompt: 'Write a safe parser for the supplied record.',
    rubric: ['Correctness', 'Safety'],
    graders: [
      { type: 'prompt', config: {
        model: 'gpt-6-luna', scoring: 'binary', threshold: 0.5,
        prompt: 'Check malformed input handling explicitly.',
      } },
      { type: 'panel', config: {
        models: [{ model: 'gpt-6-luna' }], scoring: 'binary', threshold: 0.5,
        prompt: 'Use the same rubric and evidence.',
      } },
    ],
  };
  const trajectory = {
    id: 'mock-judge-trajectory', stimulus, workDir, diff: '', events: [],
    output: 'The parser rejects malformed records.', metadata: {},
  };
  try {
    const registry = createDefaultGraderRegistry(client);
    const grade = await gradeTrajectory(trajectory, stimulus.graders, { registry, stimulus, judgeModel: 'gpt-6-luna' });

    assert.equal(grade.passed, true, JSON.stringify(grade, null, 2));
    assert.equal(grade.score, 1);
    assert.equal(grade.details?.length, 2);
    assert.deepEqual(grade.details.map(result => result.graderType), ['prompt', 'panel']);
    assert.ok(grade.details.every(result => result.passed && result.score === 1));
    assert.equal(grade.details[0].metadata.reminders_used, 1);
    assert.deepEqual(grade.details[0].metadata.token_usage,
      { inputTokens: 80, outputTokens: 20, model: 'gpt-6-luna' });
    assert.equal(grade.details[1].details?.length, 1);
    assert.deepEqual(grade.details[1].details[0].metadata.token_usage,
      { inputTokens: 40, outputTokens: 10, model: 'gpt-6-luna' });
    assert.equal(client.compatibility.toolIsolation, 'unverified');
    assert.equal(client.compatibility, JUDGE_COMPATIBILITY);
    assert.equal(calls.filter(call => call.method === 'view-result').length, 2);
    assert.equal(calls.filter(call => call.method === 'grep-result').length, 2);
    assert.ok(calls.filter(call => ['view-result', 'grep-result'].includes(call.method))
      .every(call => call.response.success && call.response.contentItems[0].text.length > 0));

    const starts = calls.filter(call => call.method === 'thread/start');
    assert.equal(starts.length, 2);
    for (const { params } of starts) {
      assert.equal(params.model, 'gpt-6-luna');
      assert.equal(params.allowProviderModelFallback, false);
      assert.equal(params.sandbox, 'read-only');
      assert.match(params.baseInstructions, /expert evaluator/i);
      assert.match(params.baseInstructions, /## Evidence Files/);
      const tools = params.dynamicTools.map(tool => tool.name).sort();
      assert.deepEqual(tools, ['grep', 'submit_grade', 'view']);
      const verdict = params.dynamicTools.find(tool => tool.name === 'submit_grade');
      assert.equal(verdict.inputSchema.type, 'object');
      assert.equal(verdict.inputSchema.additionalProperties, false);
      assert.deepEqual([...verdict.inputSchema.required].sort(),
        ['overall_reasoning', 'overall_score', 'rubric_scores']);
      assert.deepEqual(Object.keys(verdict.inputSchema.properties).sort(),
        ['overall_reasoning', 'overall_score', 'rubric_scores']);
      assert.equal(verdict.inputSchema.properties.rubric_scores.items.additionalProperties, false);
    }
    const prompts = calls.filter(call => call.method === 'turn/start').map(call => call.params.input[0].text);
    assert.equal(prompts[0], [
      '## Task', '', stimulus.prompt, '', '---', '', '## Evaluation Instructions', '',
      'Check malformed input handling explicitly.', '', '---', '', '## Rubric Criteria', '',
      '1. Correctness', '2. Safety',
    ].join('\n'));
    assert.match(prompts[1], /^Your last submit_grade call had invalid arguments: .+\./s);
    assert.match(prompts[1], /call it again with corrected arguments/i);
    assert.match(prompts[2], /## Evaluation Instructions\n\nUse the same rubric and evidence\./);
    assert.ok(servers.every(server => server.closed));
    assert.ok(calls.every(call => call.method !== 'mock-error'));
  } finally {
    await client.shutdown();
    await rm(workDir, { recursive: true, force: true });
    for (const [name, value] of Object.entries(priorTempEnvironment)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});
