import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';

const SOURCE_KINDS = Object.freeze([
  'cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
  'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown',
]);
const TERMINAL_TURN_STATUSES = new Set(['completed', 'interrupted', 'failed']);
const KNOWN_TURN_STATUSES = new Set([...TERMINAL_TURN_STATUSES, 'inProgress']);
const KNOWN_THREAD_STATUSES = new Set(['notLoaded', 'idle', 'systemError', 'active']);
const KNOWN_AGENT_STATUSES = new Set([
  'pendingInit', 'running', 'interrupted', 'completed', 'errored', 'shutdown', 'notFound',
]);
const KNOWN_AGENT_CALL_STATUSES = new Set(['inProgress', 'completed', 'failed', 'interrupted']);
const KNOWN_COMMAND_STATUSES = new Set(['inProgress', 'completed', 'failed', 'declined']);
const MAX_PAGES_PER_QUERY = 1024;
const MAX_THREADS = 10_000;
const POLL_INTERVAL_MS = 100;
const REQUIRED_STABLE_PASSES = 2;

export class SettlementError extends Error {
  constructor(message, evidence, options) {
    super(message, options);
    this.name = 'SettlementError';
    this.evidence = evidence;
  }
}

/**
 * Prove a Codex app-server root thread and its spawned descendants are idle
 * before the Vally executor exposes the workspace to a grader.
 *
 * stopActive=true immediately interrupts active descendant turns and
 * terminates only background terminals enumerated for those exact threads.
 * With stopActive=false, maxAgentDurationMs (when supplied as the remaining
 * working budget) is spent observing active work before cleanup is enabled.
 * The root turn is never interrupted here; its terminal status is returned
 * separately from any forced descendant cleanup.
 */
export async function settleAppServerWorkspace(client, rootThreadId, {
  timeoutMs,
  stopActive = false,
  maxAgentDurationMs,
} = {}) {
  if (!client || typeof client.request !== 'function') throw new Error('Settlement requires an app-server client.request method');
  if (typeof rootThreadId !== 'string' || !rootThreadId) throw new Error('Settlement requires a root thread id');
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('Settlement requires a positive hard timeout');
  if (typeof stopActive !== 'boolean') throw new Error('stopActive must be a boolean');
  if (maxAgentDurationMs !== undefined && (!Number.isFinite(maxAgentDurationMs) || maxAgentDurationMs <= 0))
    throw new Error('maxAgentDurationMs must be a positive finite remaining work budget');

  const startedAt = performance.now();
  const hardDeadline = startedAt + timeoutMs;
  const agentDeadline = maxAgentDurationMs === undefined ? undefined : startedAt + maxAgentDurationMs;
  const evidence = {
    protocol: 'codex-app-server-v2',
    rootThreadId,
    stopActiveRequested: stopActive,
    maxAgentDurationMs: maxAgentDurationMs ?? null,
    rootTerminalStatus: null,
    rootInterruptedByHelper: false,
    interrupted: false,
    settled: false,
    forcedStopStarted: false,
    rounds: 0,
    requests: [],
    actions: [],
    snapshots: [],
  };

  const fail = (message, cause) => {
    evidence.failure = message;
    evidence.durationMs = Math.max(0, performance.now() - startedAt);
    throw new SettlementError(message, evidence, cause ? { cause } : undefined);
  };

  const remainingMs = () => hardDeadline - performance.now();
  const rpc = async (method, params) => {
    const remaining = remainingMs();
    if (remaining <= 0) fail(`Hard settlement deadline expired before ${method}`);
    evidence.requests.push({ method, params: { ...params } });
    let timer;
    const request = Promise.resolve().then(() => client.request(method, params));
    try {
      return await Promise.race([
        request,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`Hard settlement deadline expired during ${method}`)), remaining);
        }),
      ]);
    } catch (error) {
      if (/Hard settlement deadline expired/.test(error?.message ?? '')) fail(error.message, error);
      throw error;
    } finally {
      clearTimeout(timer);
    }
  };

  const requireObject = (value, description) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`Missing or malformed ${description}`);
    return value;
  };

  const collectPages = async (method, baseParams, description) => {
    const values = [];
    const cursors = new Set();
    let cursor;
    for (let page = 0; page < MAX_PAGES_PER_QUERY; page += 1) {
      const params = cursor === undefined ? { ...baseParams } : { ...baseParams, cursor };
      const response = requireObject(await rpc(method, params), `${description} page`);
      if (!Array.isArray(response.data)) fail(`${description} response is missing its data array`);
      values.push(...response.data);
      if (values.length > MAX_THREADS && method === 'thread/list') fail('Descendant thread inventory exceeded its safety bound');
      const next = response.nextCursor ?? null;
      if (next === null) return values;
      if (typeof next !== 'string' || !next) fail(`${description} returned an invalid pagination cursor`);
      if (cursors.has(next) || next === cursor) fail(`${description} pagination cursor repeated`);
      cursors.add(next);
      cursor = next;
    }
    fail(`${description} exceeded the page safety bound`);
  };

  const readThread = async threadId => {
    const response = requireObject(await rpc('thread/read', { threadId }), `thread/read response for ${threadId}`);
    const thread = requireObject(response.thread, `thread metadata for ${threadId}`);
    if (thread.id !== threadId) fail(`thread/read returned the wrong thread id for ${threadId}`);
    if (typeof thread.sessionId !== 'string' || !thread.sessionId) fail(`Thread ${threadId} has no session id`);
    if (thread.parentThreadId !== null && thread.parentThreadId !== undefined &&
        (typeof thread.parentThreadId !== 'string' || !thread.parentThreadId))
      fail(`Thread ${threadId} has malformed parent ownership`);
    const status = requireObject(thread.status, `runtime status for ${threadId}`);
    if (!KNOWN_THREAD_STATUSES.has(status.type)) fail(`Thread ${threadId} has unknown runtime status`);
    if (status.type === 'notLoaded' || status.type === 'systemError')
      fail(`Thread ${threadId} lacks live idle-state evidence (${status.type})`);
    if (status.type === 'active' && !Array.isArray(status.activeFlags))
      fail(`Active thread ${threadId} has no activeFlags evidence`);
    return {
      threadId,
      parentThreadId: thread.parentThreadId ?? null,
      sessionId: thread.sessionId,
      model: typeof thread.model === 'string' && thread.model ? thread.model : null,
      reasoningEffort: typeof thread.reasoningEffort === 'string' && thread.reasoningEffort ? thread.reasoningEffort : null,
      agentRole: typeof thread.agentRole === 'string' && thread.agentRole ? thread.agentRole : null,
      status,
      turns: [],
      terminals: [],
      agentCalls: [],
      commandItems: [],
    };
  };

  const readTurnItems = async (threadId, turn) => {
    const view = turn.itemsView ?? 'full'; // The v2 schema defaults this field to full.
    if (!['full', 'summary', 'notLoaded'].includes(view)) fail(`Turn ${turn.id} has unknown itemsView`);
    if (!Array.isArray(turn.items)) fail(`Turn ${turn.id} is missing its items array`);
    if (view === 'full') return turn.items;
    const entries = await collectPages('thread/items/list', { threadId, turnId: turn.id }, `Items for ${threadId}/${turn.id}`);
    const items = entries.map(entry => {
      if (!entry || entry.turnId !== turn.id || !entry.item || typeof entry.item !== 'object')
        fail(`thread/items/list returned malformed item evidence for ${threadId}/${turn.id}`);
      return entry.item;
    });
    return items;
  };

  const readTurns = async thread => {
    const turns = await collectPages('thread/turns/list', {
      threadId: thread.threadId,
      itemsView: 'full',
      sortDirection: 'desc',
    }, `Turns for ${thread.threadId}`);
    const seenIds = new Set();
    for (const turn of turns) {
      if (!turn || typeof turn.id !== 'string' || !turn.id || !KNOWN_TURN_STATUSES.has(turn.status))
        fail(`Thread ${thread.threadId} returned a turn with unknown identity or status`);
      if (seenIds.has(turn.id)) fail(`Thread ${thread.threadId} returned a duplicate turn id`);
      seenIds.add(turn.id);
      const items = await readTurnItems(thread.threadId, turn);
      const turnEvidence = { turnId: turn.id, status: turn.status, items: [] };
      for (const item of items) {
        if (!item || typeof item.id !== 'string' || !item.id || typeof item.type !== 'string' || !item.type)
          fail(`Turn ${turn.id} contains malformed item evidence`);
        const itemEvidence = { itemId: item.id, type: item.type };
        if (item.type === 'collabAgentToolCall') {
          if (!KNOWN_AGENT_CALL_STATUSES.has(item.status) || !Array.isArray(item.receiverThreadIds) ||
              item.receiverThreadIds.some(id => typeof id !== 'string' || !id))
            fail(`Collaboration item ${item.id} has incomplete child-state evidence`);
          const states = item.agentsStates;
          if (!states || typeof states !== 'object' || Array.isArray(states))
            fail(`Collaboration item ${item.id} has malformed agent states`);
          const agentStatuses = Object.values(states).map(state => {
            if (!state || !KNOWN_AGENT_STATUSES.has(state.status))
              fail(`Collaboration item ${item.id} has an unknown child status`);
            return state.status;
          });
          itemEvidence.status = item.status;
          itemEvidence.turnStatus = turn.status;
          itemEvidence.receiverThreadIds = [...item.receiverThreadIds];
          itemEvidence.agentStatuses = agentStatuses;
          thread.agentCalls.push(itemEvidence);
        } else if (item.type === 'commandExecution') {
          if (!KNOWN_COMMAND_STATUSES.has(item.status)) fail(`Command item ${item.id} has an unknown status`);
          itemEvidence.status = item.status;
          itemEvidence.processId = typeof item.processId === 'string' ? item.processId : null;
          thread.commandItems.push(itemEvidence);
        }
        turnEvidence.items.push(itemEvidence);
      }
      thread.turns.push(turnEvidence);
    }
    if (!thread.turns.length) fail(`Thread ${thread.threadId} has no turn history to prove settlement`);
    const active = thread.turns.filter(turn => turn.status === 'inProgress');
    if (active.length > 1 || (active.length === 1 && thread.turns[0]?.turnId !== active[0].turnId))
      fail(`Thread ${thread.threadId} has inconsistent active-turn history`);
  };

  const readBackgroundTerminals = async thread => {
    const terminals = await collectPages('thread/backgroundTerminals/list', { threadId: thread.threadId },
      `Background terminals for ${thread.threadId}`);
    thread.terminals = validateTerminals(thread.threadId, terminals);
  };

  const validateTerminals = (threadId, terminals) => {
    const ids = new Set();
    const result = [];
    for (const terminal of terminals) {
      if (!terminal || typeof terminal.processId !== 'string' || !terminal.processId ||
          typeof terminal.itemId !== 'string' || !terminal.itemId ||
          typeof terminal.command !== 'string' || typeof terminal.cwd !== 'string')
        fail(`Thread ${threadId} returned malformed background terminal evidence`);
      if (ids.has(terminal.processId)) fail(`Thread ${threadId} returned a duplicate background process id`);
      ids.add(terminal.processId);
      result.push({ processId: terminal.processId, itemId: terminal.itemId, command: terminal.command });
    }
    return result;
  };

  const assertOwnership = (root, descendants, listed) => {
    const byId = new Map(descendants.map(thread => [thread.threadId, thread]));
    for (const thread of descendants) {
      if (thread.sessionId !== root.sessionId) fail(`Descendant ${thread.threadId} is outside the root session`);
      let parentId = thread.parentThreadId;
      if (typeof parentId !== 'string' || !parentId) fail(`Descendant ${thread.threadId} has no verified parent thread`);
      const visited = new Set([thread.threadId]);
      while (parentId !== root.threadId) {
        if (visited.has(parentId)) fail(`Descendant ownership cycle includes ${parentId}`);
        visited.add(parentId);
        const parent = byId.get(parentId);
        if (!parent) fail(`Descendant ${thread.threadId} has a parent outside the enumerated root tree`);
        if (parent.sessionId !== root.sessionId) fail(`Descendant chain for ${thread.threadId} crosses sessions`);
        parentId = parent.parentThreadId;
      }
    }
    for (const thread of [root, ...descendants]) {
      for (const call of thread.agentCalls) {
        for (const receiverThreadId of call.receiverThreadIds) {
          if (receiverThreadId === root.threadId || !byId.has(receiverThreadId))
            fail(`Collaboration item ${call.itemId} references a thread outside the enumerated descendant set`);
        }
      }
    }
    for (const listedThread of listed) {
      if (!byId.has(listedThread.id)) fail(`Descendant ${listedThread.id} was not read from the root session`);
    }
    return byId;
  };

  const snapshot = async () => {
    const listedById = new Map();
    for (const archived of [false, true]) {
      const listed = await collectPages('thread/list', {
        ancestorThreadId: rootThreadId,
        archived,
        sourceKinds: [...SOURCE_KINDS],
      }, `Descendant inventory (archived=${archived})`);
      for (const thread of listed) {
        if (!thread || typeof thread.id !== 'string' || !thread.id || thread.id === rootThreadId)
          fail('Descendant inventory returned a malformed or ancestor thread');
        const previous = listedById.get(thread.id);
        if (previous && ((previous.parentThreadId ?? null) !== (thread.parentThreadId ?? null) || previous.sessionId !== thread.sessionId))
          fail(`Descendant ${thread.id} has inconsistent ownership across archive inventories`);
        listedById.set(thread.id, thread);
      }
    }
    if (listedById.size > MAX_THREADS) fail('Descendant thread inventory exceeded its safety bound');

    const root = await readThread(rootThreadId);
    const descendants = [];
    for (const entry of listedById.values()) {
      const current = await readThread(entry.id);
      // Native 0.162.1 inventory uses the descendant's persistence identity as
      // sessionId, while a loaded thread/read uses its root execution session.
      // Accept that observed representation only after read proves the root
      // session, and still require exact parent ownership in both responses.
      const sessionAgrees = current.sessionId === entry.sessionId ||
        (entry.sessionId === entry.id && current.sessionId === root.sessionId);
      if (current.parentThreadId !== (entry.parentThreadId ?? null) || !sessionAgrees)
        fail(`thread/list and thread/read ownership disagree for ${entry.id}`);
      current.inventorySessionId = entry.sessionId;
      descendants.push(current);
    }
    const all = [root, ...descendants];
    assertOwnership(root, descendants, [...listedById.values()]);

    for (const thread of all) await readTurns(thread);
    // Cross-check every persisted collaboration receiver after full turn
    // history is available, still before any interrupt or process termination.
    assertOwnership(root, descendants, [...listedById.values()]);
    for (const thread of all) await readBackgroundTerminals(thread);

    const rootTurn = root.turns[0];
    if (!rootTurn || !TERMINAL_TURN_STATUSES.has(rootTurn.status))
      fail('Root thread does not have a verified terminal latest turn');
    if (root.status.type === 'active') fail('Root thread is still active after its reported terminal turn');
    evidence.rootTerminalStatus = rootTurn.status;
    evidence.rootInterruptedByHelper = false;

    for (const thread of descendants) {
      if (!thread.turns.length) fail(`Descendant ${thread.threadId} has no turn history`);
    }
    return { root, descendants, all };
  };

  const blockersFor = current => {
    const blockers = [];
    const terminalByThread = new Map(current.all.map(thread => [thread.threadId, thread.terminals]));
    for (const thread of current.all) {
      if (thread.status.type === 'active') blockers.push({ kind: 'activeThread', threadId: thread.threadId });
      for (const turn of thread.turns) {
        if (turn.status === 'inProgress') blockers.push({ kind: 'activeTurn', threadId: thread.threadId, turnId: turn.turnId });
      }
      for (const call of thread.agentCalls) {
        // Collaboration item/agent states are last-known snapshots. A terminal
        // turn can retain `inProgress` or `running` forever, so only interpret
        // them as live when their owning turn is itself currently in progress.
        if (call.turnStatus === 'inProgress' &&
            (call.status === 'inProgress' || call.agentStatuses.some(status => status === 'pendingInit' || status === 'running')))
          blockers.push({ kind: 'activeAgentCall', threadId: thread.threadId, itemId: call.itemId,
            receiverThreadIds: call.receiverThreadIds });
        if (call.turnStatus === 'inProgress' && call.agentStatuses.includes('notFound'))
          blockers.push({ kind: 'unknownAgentState', threadId: thread.threadId, itemId: call.itemId });
      }
      for (const item of thread.commandItems) {
        if (item.status !== 'inProgress') continue;
        const listed = terminalByThread.get(thread.threadId) ?? [];
        const matched = item.processId && listed.some(terminal => terminal.processId === item.processId);
        if (!matched) blockers.push({ kind: 'unlistedActiveCommand', threadId: thread.threadId,
          itemId: item.itemId, processId: item.processId });
      }
      for (const terminal of thread.terminals)
        blockers.push({ kind: 'backgroundTerminal', threadId: thread.threadId, ...terminal });
    }
    return blockers;
  };

  const sleepBounded = async delayMs => {
    const remaining = remainingMs();
    if (remaining <= 0) fail('Hard settlement deadline expired while waiting for descendants');
    await new Promise(resolve => setTimeout(resolve, Math.max(1, Math.min(delayMs, remaining))));
    if (remainingMs() <= 0) fail('Hard settlement deadline expired while waiting for descendants');
  };

  const interruptedTurns = new Set();
  const terminatedProcesses = new Set();
  let stopping = stopActive;
  let stableFingerprint = null;
  let stablePasses = 0;

  try {
    while (true) {
      evidence.rounds += 1;
      if (evidence.rounds > MAX_PAGES_PER_QUERY) fail('Settlement exceeded its scan-round safety bound');
      const current = await snapshot();
      const blockers = blockersFor(current);
      const fingerprint = JSON.stringify({
        root: [current.root.threadId, current.root.status.type, current.root.turns[0]?.turnId, current.root.turns[0]?.status],
        threads: current.descendants.map(thread => [thread.threadId, thread.parentThreadId, thread.sessionId,
          thread.status.type, thread.turns.map(turn => [turn.turnId, turn.status]),
          thread.terminals.map(terminal => terminal.processId)]).sort((a, b) => a[0].localeCompare(b[0])),
        blockers,
      });
      evidence.snapshots.push({
        round: evidence.rounds,
        root: { threadId: current.root.threadId, sessionId: current.root.sessionId,
          model: current.root.model, reasoningEffort: current.root.reasoningEffort, agentRole: current.root.agentRole,
          status: current.root.status.type, latestTurnId: current.root.turns[0].turnId,
          latestTurnStatus: current.root.turns[0].status },
        descendants: current.descendants.map(thread => ({
          threadId: thread.threadId, parentThreadId: thread.parentThreadId, sessionId: thread.sessionId,
          model: thread.model, reasoningEffort: thread.reasoningEffort, agentRole: thread.agentRole,
          status: thread.status.type,
          turns: thread.turns.map(turn => ({ turnId: turn.turnId, status: turn.status })),
          agentCalls: thread.agentCalls,
          commandItems: thread.commandItems,
          backgroundTerminals: thread.terminals,
        })),
        blockers,
      });

      if (blockers.length === 0) {
        if (fingerprint === stableFingerprint) stablePasses += 1;
        else { stableFingerprint = fingerprint; stablePasses = 1; }
        if (stablePasses >= REQUIRED_STABLE_PASSES) {
          evidence.settled = true;
          evidence.interrupted = evidence.actions.length > 0;
          evidence.durationMs = Math.max(0, performance.now() - startedAt);
          evidence.completedAt = new Date().toISOString();
          return evidence;
        }
      } else {
        stableFingerprint = null;
        stablePasses = 0;
        if (!stopping) {
          if (agentDeadline === undefined) fail('Active descendant work remains and no stop budget was authorized');
          if (performance.now() >= agentDeadline) {
            stopping = true;
            evidence.forcedStopStarted = true;
          }
        }
        if (stopping) {
          const activeTurns = blockers.filter(blocker => blocker.kind === 'activeTurn' && blocker.threadId !== rootThreadId);
          if (blockers.length > 0) evidence.forcedStopStarted = true;
          for (const turn of activeTurns) {
            const key = `${turn.threadId}\u0000${turn.turnId}`;
            if (interruptedTurns.has(key)) continue;
            interruptedTurns.add(key);
            await rpc('turn/interrupt', { threadId: turn.threadId, turnId: turn.turnId });
            evidence.actions.push({ kind: 'turn/interrupt', threadId: turn.threadId, turnId: turn.turnId,
              acknowledgmentReceived: true });
          }
          const terminals = current.all.flatMap(thread => thread.terminals.map(terminal => ({ threadId: thread.threadId, ...terminal })));
          for (const terminal of terminals) {
            const key = `${terminal.threadId}\u0000${terminal.processId}`;
            if (terminatedProcesses.has(key)) continue;
            terminatedProcesses.add(key);
            const response = requireObject(await rpc('thread/backgroundTerminals/terminate', {
              threadId: terminal.threadId, processId: terminal.processId,
            }), `background terminal termination response for ${terminal.threadId}/${terminal.processId}`);
            if (typeof response.terminated !== 'boolean') fail(`Server returned unknown termination status for background process ${terminal.processId}`);
            evidence.actions.push({ kind: 'thread/backgroundTerminals/terminate', threadId: terminal.threadId,
              processId: terminal.processId, itemId: terminal.itemId, acknowledgmentReceived: true,
              terminated: response.terminated });
            // Confirm this exact thread/process pair disappears before further action.
            const remaining = await collectPages('thread/backgroundTerminals/list', { threadId: terminal.threadId },
              `Background terminals after termination for ${terminal.threadId}`);
            const remainingValidated = validateTerminals(terminal.threadId, remaining);
            if (remainingValidated.some(item => item.processId === terminal.processId))
              fail(`Background process ${terminal.processId} remains after termination`);
            if (response.terminated !== true)
              fail(`Server did not confirm termination of background process ${terminal.processId}`);
          }
          if (activeTurns.some(turn => turn.threadId === rootThreadId))
            fail('Root has an active turn; settlement helper will not interrupt the root');
          const nonActionable = blockers.filter(blocker =>
            blocker.kind === 'activeThread' || blocker.kind === 'activeAgentCall' || blocker.kind === 'unknownAgentState' ||
            blocker.kind === 'unlistedActiveCommand');
          if (nonActionable.length && activeTurns.length === 0 && terminals.length === 0)
            evidence.lastUnresolvedBlockers = nonActionable;
        }
      }

      const wait = agentDeadline !== undefined && !stopping
        ? Math.max(1, Math.min(POLL_INTERVAL_MS, agentDeadline - performance.now()))
        : POLL_INTERVAL_MS;
      await sleepBounded(wait);
    }
  } catch (error) {
    if (error instanceof SettlementError) throw error;
    fail(`Workspace settlement could not be proven: ${error?.message ?? String(error)}`, error);
  }
}
