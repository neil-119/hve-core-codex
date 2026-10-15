// Translate observed app-server notifications to the CLI event vocabulary.
// Keep original notifications in the session log; this is an adapter view.
export function appServerEvent(notification) {
  const { method, params = {} } = notification;
  if (method === 'error') return { type: 'error',
    message: [params.error?.message, params.error?.additionalDetails].filter(Boolean).join(': '),
    willRetry: params.willRetry };
  if (method === 'turn/started') return { type: 'turn.started' };
  if (method === 'turn/completed') {
    if (params.turn?.status === 'failed') return { type: 'turn.failed', error: params.turn.error };
    return { type: 'turn.completed', status: params.turn?.status };
  }
  if (!['item/started', 'item/completed'].includes(method)) return undefined;
  const source = params.item;
  if (!source) return undefined;
  const type = method === 'item/started' ? 'item.started' : 'item.completed';
  const item = { ...source };
  switch (source.type) {
    case 'agentMessage': item.type = 'agent_message'; break;
    case 'reasoning': item.text = [...(source.summary ?? []), ...(source.content ?? [])].join('\n'); break;
    case 'commandExecution':
      item.type = 'command_execution'; item.exit_code = source.exitCode;
      item.aggregated_output = source.aggregatedOutput;
      break;
    case 'fileChange': item.type = 'file_change'; break;
    case 'mcpToolCall': item.type = 'mcp_tool_call'; break;
    case 'dynamicToolCall':
      item.type = 'dynamic_tool_call';
      item.durationMs = source.durationMs;
      item.contentItems = source.contentItems;
      break;
    case 'webSearch': item.type = 'web_search'; break;
    case 'collabAgentToolCall':
      item.type = 'collab_tool_call'; item.receiver_thread_ids = source.receiverThreadIds;
      break;
    case 'subAgentActivity':
      // This native activity item proves child lifecycle, but it does not carry
      // the collab tool name or prompt/arguments. Keep it distinct so the
      // downstream adapter can count/grade only fields actually present.
      item.type = 'sub_agent_activity'; item.native_type = source.type;
      break;
    default: return undefined;
  }
  return { type, item };
}

// Native totals are cumulative across turns. Summing snapshots or `last`
// would double-count retries/responses. This tracks each thread independently.
export class AppServerUsage {
  totals = new Map();
  consume(notification, turn, model) {
    if (notification.method !== 'thread/tokenUsage/updated') return [];
    const { threadId, tokenUsage } = notification.params ?? {};
    if (typeof threadId !== 'string' || !threadId) throw new Error('Missing usage thread identity');
    const total = tokenUsage?.total;
    const fields = ['inputTokens', 'outputTokens', 'cachedInputTokens'];
    if (!total || fields.some(key => !Number.isSafeInteger(total[key]) || total[key] < 0))
      throw new Error('Invalid cumulative Codex token usage');
    const previous = this.totals.get(threadId) ?? Object.fromEntries(fields.map(key => [key, 0]));
    if (fields.some(key => total[key] < previous[key])) throw new Error('Cumulative Codex usage decreased');
    this.totals.set(threadId, Object.fromEntries(fields.map(key => [key, total[key]])));
    const data = { inputTokens: total.inputTokens - previous.inputTokens,
      outputTokens: total.outputTokens - previous.outputTokens,
      cacheReadTokens: total.cachedInputTokens - previous.cachedInputTokens, model };
    return Object.values(data).some(value => typeof value === 'number' && value > 0)
      ? [{ type: 'token_usage', turn, data }] : [];
  }
}
