import test from 'node:test';
import assert from 'node:assert/strict';
import { appServerEvent, AppServerUsage } from './app-server-events.mjs';
import { normalizeEvent } from './index.mjs';

test('native app-server command failure preserves command, output and failure grading', () => {
  const item = { id: 'cmd-1', type: 'commandExecution', command: 'python check.py',
    status: 'failed', exitCode: 2, aggregatedOutput: 'invalid input' };
  const started = normalizeEvent(appServerEvent({ method: 'item/started', params: { item } }), 0, 'gpt-6-luna');
  const completed = normalizeEvent(appServerEvent({ method: 'item/completed', params: { item } }), 0, 'gpt-6-luna');
  assert.equal(started[0].data.arguments.command, 'python check.py');
  assert.equal(completed[0].data.success, false);
  assert.equal(completed[0].data.result, 'invalid input');
  assert.equal(completed[0].data.exit_code, 2);
  assert.equal(completed[0].data.status, 'failed');
});

test('native app-server collaboration retains actual tool and receiver identity', () => {
  const item = { id: 'spawn-1', type: 'collabAgentToolCall', tool: 'spawnAgent',
    receiverThreadIds: ['child-1'], prompt: 'review', status: 'completed' };
  const events = normalizeEvent(appServerEvent({ method: 'item/started', params: { item } }), 1, 'gpt-6-luna');
  assert.equal(events[0].data.toolName, 'spawnAgent');
  assert.deepEqual(events[0].data.arguments.receiver_thread_ids, ['child-1']);
});

test('captured native subAgentActivity lifecycle retains its actual fields without inventing a collab call', () => {
  const started = {
    type: 'subAgentActivity', model: 'gpt-6-luna', reasoningEffort: 'max',
    id: 'call_201af2cc30984b9e8dc1c9ecdc33fdff', kind: 'started',
    agentThreadId: '01a12bb4-c9b2-7670-929d-4052c2961ea7',
    agentPath: '/root/binary_search_invariants',
  };
  const terminal = {
    type: 'subAgentActivity', model: null, reasoningEffort: null,
    id: 'subagent-completed-01a12bb4-c9f4-7f81-9400-6ca5468d17ee', kind: 'completed',
    agentThreadId: '01a12bb4-c9b2-7670-929d-4052c2961ea7',
    agentPath: '/root/binary_search_invariants',
  };
  for (const [method, item] of [
    ['item/started', started], ['item/completed', started],
    ['item/started', terminal], ['item/completed', terminal],
  ]) {
    const adapted = appServerEvent({ method, params: { item } });
    assert.equal(adapted.type, method === 'item/started' ? 'item.started' : 'item.completed');
    assert.deepEqual(adapted.item, { ...item, type: 'sub_agent_activity', native_type: 'subAgentActivity' });
    assert.equal(Object.hasOwn(adapted.item, 'tool'), false);
    assert.equal(Object.hasOwn(adapted.item, 'prompt'), false);
    assert.equal(Object.hasOwn(adapted.item, 'arguments'), false);
    const normalized = normalizeEvent(adapted, 0, 'gpt-6-luna');
    assert.equal(normalized[0].type, 'custom');
    assert.equal(normalized[0].data.source, 'codex-app-server');
    assert.equal(normalized[0].data.childActivity.childThreadId, item.agentThreadId);
    assert.equal(normalized[0].data.childActivity.nativeType, 'subAgentActivity');
    assert.equal(normalized[0].data.childActivity.kind, item.kind);
    assert.equal(Object.hasOwn(normalized[0].data.childActivity, 'model'), typeof item.model === 'string');
    assert.equal(normalized.some(event => event.type === 'tool_call'), false);
    assert.match(JSON.stringify(normalized[0]), new RegExp(item.agentThreadId));
  }
});

test('native app-server dynamic tool calls preserve exact name, namespace, arguments, and result status', () => {
  const started = { id: 'dynamic-1', type: 'dynamicToolCall', namespace: 'fixture', tool: 'lookup',
    arguments: { key: 'value' }, status: 'inProgress' };
  const completed = { ...started, status: 'failed', success: false, durationMs: 12,
    contentItems: [{ type: 'inputText', text: 'denied' }] };
  const call = normalizeEvent(appServerEvent({ method: 'item/started', params: { item: started } }), 2, 'gpt-6-luna')[0];
  const result = normalizeEvent(appServerEvent({ method: 'item/completed', params: { item: completed } }), 2, 'gpt-6-luna')[0];
  assert.equal(call.type, 'tool_call');
  assert.equal(call.data.toolName, 'lookup');
  assert.equal(call.data.namespace, 'fixture');
  assert.equal(call.data.toolCallId, 'dynamic-1');
  assert.deepEqual(call.data.arguments, { key: 'value' });
  assert.equal(result.type, 'tool_result');
  assert.equal(result.data.success, false);
  assert.equal(result.data.namespace, 'fixture');
  assert.equal(result.data.result.status, 'failed');
  assert.deepEqual(result.data.result.contentItems, [{ type: 'inputText', text: 'denied' }]);
});

test('native MCP calls use Vally tool aliases while retaining native server/tool names and raw errors', () => {
  const started = { id: 'mcp-call-1', type: 'mcpToolCall', server: 'local-server', tool: 'lookup_item',
    arguments: { key: 'value' }, status: 'inProgress' };
  const failed = { ...started, status: 'failed', error: { message: 'native MCP failure' } };
  const call = normalizeEvent(appServerEvent({ method: 'item/started', params: { item: started } }),
    0, 'gpt-6-luna')[0];
  const result = normalizeEvent(appServerEvent({ method: 'item/completed', params: { item: failed } }),
    0, 'gpt-6-luna')[0];

  assert.equal(call.data.toolName, 'local-server-lookup_item');
  assert.deepEqual(call.data.nativeMcp, { server: 'local-server', tool: 'lookup_item' });
  assert.deepEqual(call.data.arguments, { key: 'value' });
  assert.equal(result.data.toolName, 'local-server-lookup_item');
  assert.equal(result.data.success, false);
  assert.deepEqual(result.data.nativeMcp, { server: 'local-server', tool: 'lookup_item' });
  assert.deepEqual(result.data.error, failed.error);
  assert.deepEqual(result.data.result, failed.error);
});

test('native app-server webSearch outputs map to the measured web_search tool', () => {
  const started = { id: 'web-1', type: 'webSearch', query: 'fixture query',
    action: { type: 'search', query: 'fixture query' } };
  const completed = { ...started, results: [{ title: 'fixture result' }] };
  const call = normalizeEvent(appServerEvent({ method: 'item/started', params: { item: started } }), 0, 'gpt-6-luna')[0];
  const result = normalizeEvent(appServerEvent({ method: 'item/completed', params: { item: completed } }), 0, 'gpt-6-luna')[0];
  assert.equal(call.data.toolName, 'web_search');
  assert.equal(call.data.arguments.query, 'fixture query');
  assert.equal(result.data.toolCallId, 'web-1');
  assert.equal(result.data.success, true);
  assert.equal(result.data.result.results[0].title, 'fixture result');
});

test('unknown events are left to authoritative raw logs rather than invented tool calls', () => {
  assert.equal(appServerEvent({ method: 'item/completed', params: { item: { type: 'futureTool' } } }), undefined);
  assert.equal(appServerEvent({ method: 'item/agentMessage/delta', params: { delta: 'partial' } }), undefined);
  assert.equal(appServerEvent({ method: 'turn/completed', params: { turn: { status: 'failed', error: { message: 'failed' } } } }).type, 'turn.failed');
});

test('retrying transport errors remain visible instead of appearing error-free', () => {
  const raw = appServerEvent({ method: 'error', params: { willRetry: true,
    error: { message: 'Reconnecting', additionalDetails: 'UnknownIssuer' } } });
  const event = normalizeEvent(raw, 0, 'gpt-6-luna')[0];
  assert.equal(event.type, 'error');
  assert.equal(event.data.willRetry, true);
  assert.match(event.data.message, /UnknownIssuer/);
});

test('cumulative usage snapshots count each thread delta once across turns', () => {
  const usage = new AppServerUsage();
  const notification = (threadId, inputTokens, outputTokens, cachedInputTokens) => ({
    method: 'thread/tokenUsage/updated', params: { threadId, tokenUsage: {
      total: { inputTokens, outputTokens, cachedInputTokens },
    } },
  });
  assert.equal(usage.consume(notification('root', 100, 20, 10), 0, 'gpt-6-luna')[0].data.inputTokens, 100);
  assert.deepEqual(usage.consume(notification('root', 100, 20, 10), 0, 'gpt-6-luna'), []);
  assert.deepEqual(usage.consume(notification('root', 150, 28, 15), 1, 'gpt-6-luna')[0].data,
    { inputTokens: 50, outputTokens: 8, cacheReadTokens: 5, model: 'gpt-6-luna' });
  assert.equal(usage.consume(notification('child', 7, 3, 0), 1, 'gpt-6-luna')[0].data.inputTokens, 7);
  assert.throws(() => usage.consume(notification('root', 149, 28, 15), 1, 'gpt-6-luna'), /decreased/);
});
