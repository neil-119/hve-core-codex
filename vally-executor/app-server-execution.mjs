import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { appendFileSync } from 'node:fs';
import { join } from 'node:path';
import { computeMetrics } from '@microsoft/vally';
import { runAppServerTurn } from './turn.mjs';
import { appServerEvent, AppServerUsage } from './app-server-events.mjs';

const MAX_CHILD_USAGE_EVENTS = 8192;
const MAX_CHILD_USAGE_THREADS = 1024;
const MAX_CHILD_ITEM_EVENTS = 4096;
const MAX_CHILD_ITEM_BYTES = 8 * 1024 * 1024;
const MAX_CHILD_ITEM_THREADS = 1024;
const USAGE_FIELDS = ['inputTokens', 'outputTokens', 'cachedInputTokens'];
const TERMINAL_TURNS = new Set(['completed', 'interrupted', 'failed']);
const CHILD_ITEM_PLACEHOLDER = Symbol('owned-child-item-placeholder');
const MCP_SERVER_PAGE_SIZE = 64;
const MAX_MCP_SERVER_PAGES = 16;
const MAX_MCP_SERVERS = MCP_SERVER_PAGE_SIZE * MAX_MCP_SERVER_PAGES;

function boundedText(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 256 ? value : null;
}

function isPlainRecord(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function nativeMcpThreadConfig(requestedMcpServers, disabledMcpServerNames) {
  if (!isPlainRecord(requestedMcpServers) || Object.keys(requestedMcpServers).length === 0 ||
      Object.keys(requestedMcpServers).length > MAX_MCP_SERVERS ||
      Object.keys(requestedMcpServers).some(name => !/^[A-Za-z0-9_-]{1,128}$/.test(name)) ||
      Object.values(requestedMcpServers).some(server => !isPlainRecord(server)))
    throw new Error('Native eval-level MCP verification requires a bounded requested server map');
  if (!Array.isArray(disabledMcpServerNames) || disabledMcpServerNames.length > 256 ||
      disabledMcpServerNames.some(name => typeof name !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(name)) ||
      new Set(disabledMcpServerNames).size !== disabledMcpServerNames.length)
    throw new Error('Native eval-level MCP verification requires bounded ambient isolation names');
  const threadConfig = Object.fromEntries(disabledMcpServerNames.map(name => [name, { enabled: false }]));
  for (const [name, server] of Object.entries(requestedMcpServers))
    threadConfig[name] = { ...server, enabled: true };
  if (Object.keys(threadConfig).length > MAX_MCP_SERVERS)
    throw new Error('Native eval-level MCP thread config exceeds its server limit');
  return threadConfig;
}

async function verifyNativeMcpCatalog(client, threadId, requestedMcpServers, disabledMcpServerNames, timeoutMs) {
  if (!isPlainRecord(requestedMcpServers) || Object.keys(requestedMcpServers).length === 0 ||
      Object.keys(requestedMcpServers).length > MAX_MCP_SERVERS)
    throw new Error('Native eval-level MCP verification requires a bounded nonempty requested server map');
  const expectedNames = Object.keys(requestedMcpServers).sort();
  const expectedNameSet = new Set(expectedNames);
  const disabledNameSet = new Set(disabledMcpServerNames.filter(name => !expectedNameSet.has(name)));
  const deadline = performance.now() + timeoutMs;
  while (true) {
    let starting = false;
    const catalog = new Map();
    const seenCursors = new Set();
    let cursor;
    for (let page = 0; page < MAX_MCP_SERVER_PAGES; page++) {
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw new Error('Native eval-level MCP catalog verification deadline exhausted');
      const response = await withDeadline(client.request('mcpServerStatus/list', {
        threadId, limit: MCP_SERVER_PAGE_SIZE, detail: 'toolsAndAuthOnly', ...(cursor ? { cursor } : {}),
      }), remaining);
      if (!response || !Array.isArray(response.data) || response.data.length > MAX_MCP_SERVERS - catalog.size)
        throw new Error('Native per-thread MCP catalog response is malformed or exceeds its server limit');
      for (const server of response.data) {
        if (!isPlainRecord(server) || typeof server.name !== 'string' ||
            !/^[A-Za-z0-9_-]{1,128}$/.test(server.name) || catalog.has(server.name))
          throw new Error('Native per-thread MCP catalog has a missing, invalid, or duplicate server name');
        if (!expectedNameSet.has(server.name) && !disabledNameSet.has(server.name))
          throw new Error(`Native per-thread MCP catalog contains unknown server '${server.name}'`);
        if (!isPlainRecord(server.tools) || server.toolsError !== undefined && server.toolsError !== null)
          throw new Error(`Native MCP server '${server.name}' has an invalid or failed tool catalog`);
        const toolNames = Object.keys(server.tools).sort();
        if (toolNames.some(name => !name || !isPlainRecord(server.tools[name])))
          throw new Error(`Native MCP server '${server.name}' returned malformed tool metadata`);
        if (expectedNameSet.has(server.name)) {
          if (server.runtimeStatus === 'starting') starting = true;
          else if (server.runtimeStatus !== 'connected')
            throw new Error(`Native MCP server '${server.name}' is not connected in the requested thread`);
          catalog.set(server.name, toolNames);
        } else {
          if (server.runtimeStatus !== 'disabled' || toolNames.length !== 0)
            throw new Error(`Ambient MCP server '${server.name}' reappeared instead of remaining disabled with no tools`);
          catalog.set(server.name, []);
        }
      }
      const nextCursor = response.nextCursor;
      if (nextCursor === undefined || nextCursor === null) break;
      if (typeof nextCursor !== 'string' || !nextCursor || seenCursors.has(nextCursor))
        throw new Error('Native per-thread MCP catalog has a malformed or repeated pagination cursor');
      seenCursors.add(nextCursor);
      if (page === MAX_MCP_SERVER_PAGES - 1)
        throw new Error(`Native per-thread MCP catalog exceeds ${MAX_MCP_SERVER_PAGES} pages`);
      cursor = nextCursor;
    }
    const actualNames = [...catalog.keys()].filter(name => expectedNameSet.has(name)).sort();
    if (actualNames.length !== expectedNames.length || actualNames.some((name, index) => name !== expectedNames[index]))
      throw new Error('Native per-thread MCP server catalog does not exactly match the requested Vally server map');
    if (starting) {
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw new Error('Native eval-level MCP catalog verification deadline exhausted');
      await new Promise(resolve => setTimeout(resolve, Math.min(250, remaining)));
      continue;
    }
    return { verified: true, threadId, servers: expectedNames.map(name => ({ name,
      nativeToolNames: catalog.get(name) })) };
  }
}

function snapshotOwnership(snapshot, expectedRootId) {
  const root = snapshot?.root;
  if (!root || root.threadId !== expectedRootId || typeof root.sessionId !== 'string' || !root.sessionId ||
      root.status !== 'idle' || !TERMINAL_TURNS.has(root.latestTurnStatus) || !Array.isArray(snapshot.descendants))
    return null;
  const children = new Map();
  for (const value of snapshot.descendants) {
    if (!value || typeof value.threadId !== 'string' || !value.threadId || value.threadId === expectedRootId ||
        typeof value.parentThreadId !== 'string' || !value.parentThreadId || value.sessionId !== root.sessionId ||
        value.status !== 'idle' || !Array.isArray(value.turns) ||
        value.turns.some(turn => !turn || !TERMINAL_TURNS.has(turn.status)) || children.has(value.threadId))
      return null;
    children.set(value.threadId, {
      threadId: value.threadId,
      parentThreadId: value.parentThreadId,
      sessionId: value.sessionId,
      model: boundedText(value.model),
      reasoningEffort: boundedText(value.reasoningEffort),
      agentRole: boundedText(value.agentRole),
    });
  }
  for (const child of children.values()) {
    const visited = new Set([child.threadId]);
    let parentId = child.parentThreadId;
    while (parentId !== expectedRootId) {
      if (visited.has(parentId)) return null;
      visited.add(parentId);
      const parent = children.get(parentId);
      if (!parent || parent.sessionId !== root.sessionId) return null;
      parentId = parent.parentThreadId;
    }
  }
  return { sessionId: root.sessionId, root, children };
}

// A successful return from the default settlement implementation is only
// useful for delegated accounting when its final two stable snapshots prove
// the exact root session and every descendant parent chain.
function verifiedSettlementOwnership(settlement, expectedRootId, expectedRootSessionId) {
  if (settlement?.protocol !== 'codex-app-server-v2' || settlement.settled !== true ||
      settlement.rootThreadId !== expectedRootId || !TERMINAL_TURNS.has(settlement.rootTerminalStatus) ||
      typeof expectedRootSessionId !== 'string' || !expectedRootSessionId ||
      !Array.isArray(settlement.snapshots) || settlement.snapshots.length < 2)
    return { verified: false, reason: 'settlement-proof-missing-or-incomplete' };
  const previous = snapshotOwnership(settlement.snapshots.at(-2), expectedRootId);
  const latest = snapshotOwnership(settlement.snapshots.at(-1), expectedRootId);
  if (!previous || !latest || previous.sessionId !== expectedRootSessionId || latest.sessionId !== expectedRootSessionId ||
      previous.sessionId !== latest.sessionId ||
      latest.root.latestTurnStatus !== settlement.rootTerminalStatus)
    return { verified: false, reason: 'settlement-snapshots-do-not-prove-root-ownership' };
  if (previous.children.size !== latest.children.size ||
      [...previous.children.values()].some(child => {
        const next = latest.children.get(child.threadId);
        return !next || next.parentThreadId !== child.parentThreadId || next.sessionId !== child.sessionId;
      }))
    return { verified: false, reason: 'settlement-snapshots-have-changing-descendant-ownership' };
  for (const child of latest.children.values()) {
    const earlier = previous.children.get(child.threadId);
    child.metadataStable = earlier.model === child.model && earlier.reasoningEffort === child.reasoningEffort &&
      earlier.agentRole === child.agentRole;
  }
  return { verified: true, sessionId: latest.sessionId, children: latest.children };
}

function captureChildUsage(notification, turn, state) {
  const threadId = notification.params?.threadId;
  if (typeof threadId !== 'string' || !threadId || threadId.length > 256) {
    state.malformedNotifications = Math.min(Number.MAX_SAFE_INTEGER, state.malformedNotifications + 1);
    return;
  }
  if (!state.threadIds.has(threadId)) {
    if (state.threadIds.size >= MAX_CHILD_USAGE_THREADS) {
      state.overflow = true;
      state.droppedEvents = Math.min(Number.MAX_SAFE_INTEGER, state.droppedEvents + 1);
      return;
    }
    state.threadIds.add(threadId);
  }
  if (state.events.length >= MAX_CHILD_USAGE_EVENTS) {
    state.overflow = true;
    state.droppedEvents = Math.min(Number.MAX_SAFE_INTEGER, state.droppedEvents + 1);
    return;
  }
  const total = notification.params?.tokenUsage?.total;
  const valid = Boolean(total && typeof total === 'object' &&
    USAGE_FIELDS.every(field => Number.isSafeInteger(total[field]) && total[field] >= 0));
  state.events.push({
    threadId,
    turn,
    valid,
    ...(valid ? { total: {
      inputTokens: total.inputTokens,
      outputTokens: total.outputTokens,
      cachedInputTokens: total.cachedInputTokens,
    } } : {}),
    processed: false,
  });
}

function markChildItemIncomplete(state, threadId, count = 1) {
  if (!threadId) return;
  state.incompleteItemsByThread.set(threadId,
    (state.incompleteItemsByThread.get(threadId) ?? 0) + count);
}

function captureChildItem(notification, turn, state, events) {
  state.observedItemNotificationCount += 1;
  const params = notification?.params;
  const threadId = boundedText(params?.threadId);
  if (!threadId) {
    state.malformedNotificationCount += 1;
    state.unknownItemCount += 1;
    state.unattributedIncompleteItemCount += 1;
    return;
  }
  if (!state.threadIds.has(threadId)) {
    if (state.threadIds.size >= MAX_CHILD_ITEM_THREADS) {
      state.overflow = true;
      state.overflowReasons.add('thread-count');
      state.droppedItemCount += 1;
      state.unattributedIncompleteItemCount += 1;
      return;
    }
    state.threadIds.add(threadId);
  }

  const item = params?.item;
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    state.malformedNotificationCount += 1;
    state.unknownItemCount += 1;
    if (threadId) state.unknownThreadIds.add(threadId);
    markChildItemIncomplete(state, threadId);
    return;
  }
  let serialized;
  try { serialized = JSON.stringify(item); }
  catch {
    state.malformedNotificationCount += 1;
    state.unknownItemCount += 1;
    if (threadId) state.unknownThreadIds.add(threadId);
    markChildItemIncomplete(state, threadId);
    return;
  }
  if (typeof serialized !== 'string') {
    state.malformedNotificationCount += 1;
    state.unknownItemCount += 1;
    if (threadId) state.unknownThreadIds.add(threadId);
    markChildItemIncomplete(state, threadId);
    return;
  }

  const itemBytes = Buffer.byteLength(serialized, 'utf8');
  let raw;
  try { raw = appServerEvent(notification); }
  catch {
    state.malformedNotificationCount += 1;
    state.unknownItemCount += 1;
    if (threadId) state.unknownThreadIds.add(threadId);
    markChildItemIncomplete(state, threadId);
    return;
  }
  if (!raw?.item) {
    state.unknownItemCount += 1;
    if (threadId) state.unknownThreadIds.add(threadId);
    markChildItemIncomplete(state, threadId);
    return;
  }
  if (state.bufferedItemCount >= MAX_CHILD_ITEM_EVENTS ||
      itemBytes > MAX_CHILD_ITEM_BYTES - state.bufferedItemBytes) {
    state.overflow = true;
    state.overflowReasons.add(state.bufferedItemCount >= MAX_CHILD_ITEM_EVENTS ? 'item-count' : 'item-bytes');
    state.droppedItemCount += 1;
    markChildItemIncomplete(state, threadId);
    return;
  }
  state.bufferedItemCount += 1;
  state.bufferedItemBytes += itemBytes;
  events.push({ [CHILD_ITEM_PLACEHOLDER]: true, raw, threadId, turn, itemBytes });
}

function resolveOwnedChildItems(events, children, state, normalizeEvent) {
  for (let index = 0; index < events.length;) {
    const placeholder = events[index];
    if (!placeholder?.[CHILD_ITEM_PLACEHOLDER]) {
      index += 1;
      continue;
    }
    const child = children.get(placeholder.threadId);
    if (!child?.metadataStable || !child.model) {
      index += 1;
      continue;
    }
    const normalized = normalizeEvent(placeholder.raw, placeholder.turn, child.model);
    if (!Array.isArray(normalized) || normalized.some(event => !event || typeof event !== 'object'))
      throw new Error('Child event normalizer returned invalid events');
    const attributed = normalized.map(event => ({ ...event, agentId: child.threadId,
      ...(child.agentRole ? { agentRole: child.agentRole } : {}) }));
    events.splice(index, 1, ...attributed);
    state.resolvedItemNotificationCount += 1;
    state.emittedNormalizedEventCount += attributed.length;
    index += attributed.length;
  }
}

function finalizeChildItems(events, state, ownedChildThreadIds, lastOwnershipProof, settlementProofFailures) {
  const finalized = [];
  const pendingCounts = new Map(state.incompleteItemsByThread);
  for (const value of events) {
    if (!value?.[CHILD_ITEM_PLACEHOLDER]) {
      finalized.push(value);
      continue;
    }
    const childId = value.threadId;
    pendingCounts.set(childId, (pendingCounts.get(childId) ?? 0) + 1);
  }

  const canClassifyForeign = Boolean(lastOwnershipProof?.verified) && settlementProofFailures === 0;
  const unresolvedOwnedThreadIds = new Set();
  const unresolvedThreadIds = new Set();
  const excludedForeignThreadIds = new Set();
  let unresolvedOwnedItemCount = 0;
  let unresolvedItemCount = 0;
  let excludedForeignItemCount = 0;
  for (const [threadId, count] of pendingCounts) {
    if (ownedChildThreadIds.has(threadId)) {
      unresolvedOwnedThreadIds.add(threadId);
      unresolvedOwnedItemCount += count;
      unresolvedItemCount += count;
    } else if (canClassifyForeign) {
      excludedForeignThreadIds.add(threadId);
      excludedForeignItemCount += count;
    } else {
      unresolvedThreadIds.add(threadId);
      unresolvedItemCount += count;
    }
  }
  const outputEvents = finalized;
  return {
    events: outputEvents,
    diagnostic: {
      observedItemNotificationCount: state.observedItemNotificationCount,
      bufferedItemCount: state.bufferedItemCount,
      bufferedItemBytes: state.bufferedItemBytes,
      resolvedItemNotificationCount: state.resolvedItemNotificationCount,
      emittedNormalizedEventCount: state.emittedNormalizedEventCount,
      unresolvedItemCount: unresolvedItemCount + state.unattributedIncompleteItemCount,
      unresolvedOwnedItemCount,
      unresolvedOwnedThreadIds: [...unresolvedOwnedThreadIds].sort(),
      unresolvedThreadIds: [...unresolvedThreadIds].sort(),
      excludedForeignItemCount,
      excludedForeignThreadIds: [...excludedForeignThreadIds].sort(),
      unknownItemCount: state.unknownItemCount,
      unknownThreadIds: [...state.unknownThreadIds].sort(),
      malformedNotificationCount: state.malformedNotificationCount,
      droppedItemCount: state.droppedItemCount,
      unattributedIncompleteItemCount: state.unattributedIncompleteItemCount,
      overflow: state.overflow,
      overflowReasons: [...state.overflowReasons].sort(),
      completeDelegatedTrajectoryVerified: false,
    },
  };
}

// Dependencies remain explicit while native settlement is being validated.
// Vally remains responsible for trials, fixtures, grading and result storage.
export async function executeAppServer(stimulus, options, config, {
  client, settleWorkspace, normalizeEvent, requestedMcpServers, disabledMcpServerNames = [],
}) {
  if (options.sessionID) throw new Error('App-server acceptance currently requires a fresh thread');
  if (requestedMcpServers !== undefined && !isPlainRecord(requestedMcpServers))
    throw new Error('Native eval-level MCP requested server map must be an object');
  const hasMcpServers = isPlainRecord(requestedMcpServers) && Object.keys(requestedMcpServers).length > 0;
  const threadMcpServers = hasMcpServers ? nativeMcpThreadConfig(requestedMcpServers, disabledMcpServerNames) : undefined;
  const model = options.model ?? 'gpt-6-luna';
  const effort = options.reasoningEffort ?? config.reasoningEffort;
  const startedAt = new Date();
  if (!Number.isFinite(options.timeout) || options.timeout <= 0) throw new Error('Invalid execution deadline');
  let remainingHard = options.timeout;
  let remainingWork = options.maxAgentDurationMs;
  const events = [], usage = new AppServerUsage();
  const settlements = [];
  const childItemBuffer = { threadIds: new Set(), incompleteItemsByThread: new Map(), unknownThreadIds: new Set(),
    observedItemNotificationCount: 0, bufferedItemCount: 0, bufferedItemBytes: 0,
    resolvedItemNotificationCount: 0, emittedNormalizedEventCount: 0,
    unknownItemCount: 0, malformedNotificationCount: 0, droppedItemCount: 0,
    unattributedIncompleteItemCount: 0,
    overflow: false, overflowReasons: new Set() };
  const childUsageBuffer = { events: [], threadIds: new Set(), overflow: false,
    droppedEvents: 0, malformedNotifications: 0 };
  const ownedChildThreadIds = new Set();
  const childMetadata = new Map();
  const childUsageObserved = new Set();
  const invalidChildUsage = new Set();
  const unstableChildMetadata = new Set();
  const excludedForeignThreadIds = new Set();
  let lastOwnershipProof = null;
  let settlementProofFailures = 0;
  let verifiedRootSessionId = null;
  let output = '', sessionID, rootSessionID, endReason = 'completed';
  let primaryError;
  const consume = notification => {
    // Caller owns authoritative raw logging for all threads on this transport.
    const notificationThreadId = notification.params?.threadId;
    if (notificationThreadId !== sessionID) {
      if (notification.method === 'thread/tokenUsage/updated')
        captureChildUsage(notification, currentTurn, childUsageBuffer);
      else if (notification.method === 'item/started' || notification.method === 'item/completed')
        captureChildItem(notification, currentTurn, childItemBuffer, events);
      return;
    }
    const raw = appServerEvent(notification);
    if (raw) raw.receivedAt = new Date().toISOString();
    if (raw) {
      const normalized = normalizeEvent(raw, currentTurn, model);
      events.push(...normalized);
    }
    events.push(...usage.consume(notification, currentTurn, model));
    if (notification.method === 'item/completed' && notification.params.item?.type === 'agentMessage')
      output = notification.params.item.text;
  };
  let currentTurn = 0;
  const observeRaw = message => options.onRawEvent?.(message);
  const logFrame = frame => {
    if (options.sessionLog) appendFileSync(join(options.sessionLog.rootDir, 'events.jsonl'), frame + '\n');
  };
  try {
    if (options.sessionLog) {
      await mkdir(options.sessionLog.rootDir, { recursive: true });
      await writeFile(join(options.sessionLog.rootDir, 'events.jsonl'), '');
    }
    client.on('rawFrame', logFrame); client.on('rawMessage', observeRaw);
    const setupBegan = Date.now();
    if (typeof client.initialize === 'function' && !client.initialized) {
      await withDeadline(client.initialize({ clientInfo: { name: 'hve-vally-codex', version: '0.1.0' },
        capabilities: { experimentalApi: true } }), remainingHard);
      remainingHard -= Date.now() - setupBegan;
    }
    const threadSetupBegan = Date.now();
    const response = await withDeadline(client.request('thread/start', { model, cwd: options.workDir,
      sandbox: config.sandbox, config: { model_reasoning_effort: effort,
        'agents.default_subagent_model': model, 'agents.default_subagent_reasoning_effort': effort,
        ...(hasMcpServers ? { mcp_servers: threadMcpServers } : {}) },
      }), remainingHard);
    remainingHard -= Date.now() - threadSetupBegan;
    sessionID = response?.thread?.id;
    if (typeof sessionID !== 'string' || !sessionID) throw new Error('Missing app-server thread identity');
    rootSessionID = response?.thread?.sessionId;
    if (typeof rootSessionID !== 'string' || !rootSessionID)
      throw new Error('Missing app-server root session identity');
    if (response.model !== model || response.reasoningEffort !== effort)
      throw new Error('App-server effective model or reasoning effort disagrees with requested settings');
    let nativeMcpCatalog;
    if (hasMcpServers) {
      const mcpSetupBegan = Date.now();
      nativeMcpCatalog = await verifyNativeMcpCatalog(client, sessionID, requestedMcpServers,
        disabledMcpServerNames, remainingHard);
      remainingHard -= Date.now() - mcpSetupBegan;
    }
    client.on('notification', consume);
    const turns = stimulus.turns ?? [{ prompt: stimulus.prompt }];
    for (currentTurn = 0; currentTurn < turns.length; currentTurn++) {
      const prompt = typeof turns[currentTurn] === 'string' ? turns[currentTurn] : turns[currentTurn].prompt;
      if (typeof prompt !== 'string') throw new Error('Missing configured turn prompt');
      events.push({ type: 'user_message', timestamp: new Date(), turn: currentTurn, data: { content: prompt } });
      const began = Date.now();
      const result = await runAppServerTurn(client, { threadId: sessionID,
        input: [{ type: 'text', text: prompt }], model, effort },
      { timeoutMs: remainingHard, maxAgentDurationMs: remainingWork });
      const workingElapsed = Date.now() - began;
      remainingHard -= workingElapsed;
      if (remainingWork !== undefined) remainingWork -= workingElapsed;
      const reachedLimit = result.interruptRequested || remainingWork !== undefined && remainingWork <= 0;
      const settlementBegan = Date.now();
      const settlement = await settleWorkspace(client, sessionID, { timeoutMs: remainingHard,
        stopActive: reachedLimit, maxAgentDurationMs: reachedLimit ? undefined : remainingWork });
      settlements.push(settlement);
      if (options.sessionLog) await writeFile(join(options.sessionLog.rootDir, `settlement-turn-${currentTurn}.json`),
        JSON.stringify(settlement, null, 2));
      const ownership = verifiedSettlementOwnership(settlement, sessionID, rootSessionID);
      if (!ownership.verified) {
        settlementProofFailures += 1;
      } else {
        if (verifiedRootSessionId && verifiedRootSessionId !== ownership.sessionId)
          throw new Error('Verified root session identity changed between settlements');
        verifiedRootSessionId = ownership.sessionId;
        lastOwnershipProof = ownership;
        for (const child of ownership.children.values()) {
          ownedChildThreadIds.add(child.threadId);
          if (!child.metadataStable) unstableChildMetadata.add(child.threadId);
          else unstableChildMetadata.delete(child.threadId);
          const previous = childMetadata.get(child.threadId);
          if (previous && (previous.parentThreadId !== child.parentThreadId || previous.sessionId !== child.sessionId ||
              previous.model && child.model && previous.model !== child.model ||
              previous.agentRole && child.agentRole && previous.agentRole !== child.agentRole ||
              previous.reasoningEffort && child.reasoningEffort && previous.reasoningEffort !== child.reasoningEffort))
            throw new Error(`Verified metadata changed for descendant thread ${child.threadId}`);
          childMetadata.set(child.threadId, {
            threadId: child.threadId,
            parentThreadId: child.parentThreadId,
            sessionId: child.sessionId,
            model: child.model ?? previous?.model ?? null,
            reasoningEffort: child.reasoningEffort ?? previous?.reasoningEffort ?? null,
            agentRole: child.agentRole ?? previous?.agentRole ?? null,
          });
        }
        resolveOwnedChildItems(events, ownership.children, childItemBuffer, normalizeEvent);
        for (const update of childUsageBuffer.events) {
          if (update.processed || !ownership.children.has(update.threadId)) continue;
          const child = ownership.children.get(update.threadId);
          if (!update.valid) {
            invalidChildUsage.add(update.threadId);
            update.processed = true;
            continue;
          }
          if (!child.metadataStable || !child.model) continue;
          const usageEvents = usage.consume({ method: 'thread/tokenUsage/updated', params: {
            threadId: update.threadId, tokenUsage: { total: update.total },
          } }, update.turn, child.model);
          events.push(...usageEvents.map(event => ({ ...event, agentId: child.threadId,
            ...(child.agentRole ? { agentRole: child.agentRole } : {}) })));
          childUsageObserved.add(update.threadId);
          update.processed = true;
        }
      }
      const settlementElapsed = Date.now() - settlementBegan;
      remainingHard -= settlementElapsed;
      if (remainingWork !== undefined) remainingWork -= settlementElapsed;
      const interrupted = reachedLimit || settlement.interrupted || remainingWork !== undefined && remainingWork <= 0;
      if (interrupted) endReason = 'agent_timeout';
      await options.onTurnComplete?.({ turn: currentTurn,
        status: interrupted ? 'interrupted' : 'completed',
        final: interrupted || currentTurn === turns.length - 1 });
      // Host grading time is outside both budgets. A recoverable stop returns
      // partial work and never starts another configured turn.
      if (interrupted) break;
    }
    const incompleteChildThreadIds = new Set(invalidChildUsage);
    for (const threadId of ownedChildThreadIds) {
      const child = childMetadata.get(threadId);
      if (!child?.model || !childUsageObserved.has(threadId) ||
          unstableChildMetadata.has(threadId) || childUsageBuffer.overflow)
        incompleteChildThreadIds.add(threadId);
    }
    if (lastOwnershipProof?.verified && settlementProofFailures === 0) {
      for (const threadId of childUsageBuffer.threadIds) {
        if (!ownedChildThreadIds.has(threadId)) excludedForeignThreadIds.add(threadId);
      }
    } else {
      for (const threadId of childUsageBuffer.threadIds) incompleteChildThreadIds.add(threadId);
    }
    const delegatedUsageVerified = settlements.length > 0 && settlementProofFailures === 0 &&
      Boolean(lastOwnershipProof?.verified) && !childUsageBuffer.overflow &&
      childUsageBuffer.malformedNotifications === 0 && incompleteChildThreadIds.size === 0;
    const usageScope = !lastOwnershipProof?.verified || settlementProofFailures > 0
      ? childUsageObserved.size > 0
        ? 'root-plus-partial-verified-descendants'
        : 'root-only-unverified-settlement'
      : ownedChildThreadIds.size === 0
        ? 'root-only-no-descendants'
        : delegatedUsageVerified
          ? 'root-plus-verified-descendants'
          : childUsageObserved.size > 0
            ? 'root-plus-partial-verified-descendants'
            : 'root-only-incomplete-delegated-usage';
    const finalizedChildItems = finalizeChildItems(events, childItemBuffer, ownedChildThreadIds,
      lastOwnershipProof, settlementProofFailures);
    return { id: randomUUID(), stimulus, events: finalizedChildItems.events, output, workDir: options.workDir, endReason,
      metadata: { startedAt, completedAt: new Date(), model, executor: 'codex', sessionID,
        ...(nativeMcpCatalog ? { nativeMcpCatalog } : {}),
        usageScope, delegatedUsageVerified,
        delegatedEvents: finalizedChildItems.diagnostic,
        delegatedUsage: {
          scope: usageScope,
          verified: delegatedUsageVerified,
          rootSessionId: verifiedRootSessionId,
          childThreadIds: [...ownedChildThreadIds].sort(),
          childAgents: [...childMetadata.values()].sort((a, b) => a.threadId.localeCompare(b.threadId)).map(child => ({
            threadId: child.threadId, parentThreadId: child.parentThreadId, model: child.model,
            reasoningEffort: child.reasoningEffort, agentRole: child.agentRole,
          })),
          meteredChildThreadIds: [...childUsageObserved].sort(),
          incompleteChildThreadIds: [...incompleteChildThreadIds].sort(),
          excludedForeignThreadIds: [...excludedForeignThreadIds].sort(),
          bufferedEventCount: childUsageBuffer.events.length,
          bufferOverflow: childUsageBuffer.overflow,
          droppedEventCount: childUsageBuffer.droppedEvents,
          malformedNotifications: childUsageBuffer.malformedNotifications,
          unverifiedSettlementCount: settlementProofFailures,
        },
        settlementEvidence: settlements, usageReported: usage.totals.size > 0 },
      metrics: { ...computeMetrics(finalizedChildItems.events), wallTimeMs: Date.now() - startedAt.getTime() } };
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    const cleanupErrors = [];
    try { await client.shutdown(); }
    catch (error) { cleanupErrors.push(error); }
    client.off('notification', consume);
    client.off('rawFrame', logFrame); client.off('rawMessage', observeRaw);
    const distinctCleanupErrors = cleanupErrors.filter(error => error !== primaryError && error?.cause !== primaryError);
    if (distinctCleanupErrors.length) {
      if (primaryError)
        throw new AggregateError([primaryError, ...distinctCleanupErrors], 'App-server execution and cleanup failed', { cause: primaryError });
      if (distinctCleanupErrors.length === 1) throw distinctCleanupErrors[0];
      throw new AggregateError(distinctCleanupErrors, 'App-server cleanup failed');
    }
  }
}

function withDeadline(promise, timeoutMs) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('Execution deadline exhausted');
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('App-server setup deadline exhausted')), timeoutMs);
  })]).finally(() => clearTimeout(timer));
}
