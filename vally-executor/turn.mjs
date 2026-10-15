// Control one Codex app-server turn. A terminal root turn alone does not prove
// delegated agents have stopped: the executor must establish that separately
// before notifying Vally that the workspace is stable.
export async function runAppServerTurn(client, params, {
  timeoutMs, maxAgentDurationMs, onNotification,
} = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('Invalid hard turn deadline');
  if (typeof params?.threadId !== 'string' || !params.threadId) throw new Error('Missing thread identity');
  const agentLimit = Number.isFinite(maxAgentDurationMs) && maxAgentDurationMs > 0
    ? maxAgentDurationMs : undefined;
  let turnId, interruptRequested = false, terminal, finished = false;
  let hardTimer, agentTimer;
  const earlyTerminals = new Map();
  let resolveCompletion, rejectCompletion;
  const completion = new Promise((resolve, reject) => { resolveCompletion = resolve; rejectCompletion = reject; });
  const finish = (error, value) => {
    if (finished) return;
    finished = true;
    clearTimeout(hardTimer); clearTimeout(agentTimer);
    client.off('notification', observe); client.off('failure', fail);
    if (error) rejectCompletion(error); else resolveCompletion(value);
  };
  const fail = error => finish(error instanceof Error ? error : new Error(String(error)));
  const acceptTerminal = value => {
    if (!['completed', 'interrupted', 'failed'].includes(value.status))
      return fail(new Error(`Invalid terminal turn status: ${value.status}`));
    terminal = value;
    if (value.status === 'failed') return fail(new Error(value.error?.message ?? 'Codex turn failed'));
    if (value.status === 'interrupted' && !interruptRequested)
      return fail(new Error('Codex turn interrupted without an adapter stop request'));
    finish(undefined, { turn: value, interruptRequested });
  };
  const observe = notification => {
    if (finished || notification.params?.threadId !== params.threadId) return;
    try { onNotification?.(notification); } catch (error) { return fail(error); }
    if (notification.method !== 'turn/completed') return;
    const value = notification.params.turn;
    if (!value || typeof value.id !== 'string' || !value.id) return fail(new Error('Missing terminal turn identity'));
    if (!turnId) {
      if (earlyTerminals.size >= 32) return fail(new Error('Too many unmatched terminal turns'));
      earlyTerminals.set(value.id, value);
    } else if (value.id === turnId) acceptTerminal(value);
  };
  client.on('notification', observe); client.on('failure', fail);
  const interrupt = () => {
    if (finished) return;
    interruptRequested = true;
    if (!turnId) return; // Start response is still pending; stop once identified.
    Promise.resolve().then(() => client.request('turn/interrupt', { threadId: params.threadId, turnId }))
      .catch(error => { if (!finished) fail(error); });
    // Only turn/completed settles completion, never the interrupt RPC ack.
  };
  hardTimer = setTimeout(() => {
    // Best-effort interruption is cleanup, not proof of a recoverable boundary.
    if (turnId && !terminal) {
      try { Promise.resolve(client.request('turn/interrupt', { threadId: params.threadId, turnId })).catch(() => {}); }
      catch { /* A disconnected transport cannot acknowledge cleanup. */ }
    }
    fail(new Error('Codex hard turn deadline exhausted; workspace settlement unproven'));
  }, timeoutMs);
  // Charge the whole turn/start request conservatively: the server can already
  // be working before its response reaches us. Session setup and host grading
  // happen outside this controller and do not consume its working budget.
  if (agentLimit !== undefined) agentTimer = setTimeout(interrupt, agentLimit);
  // Start without awaiting here: a hung start response must obey the hard limit.
  Promise.resolve().then(() => client.request('turn/start', params)).then(response => {
    if (finished) return;
    turnId = response?.turn?.id;
    if (typeof turnId !== 'string' || !turnId) return fail(new Error('Missing started turn identity'));
    if (earlyTerminals.has(turnId)) return acceptTerminal(earlyTerminals.get(turnId));
    if (interruptRequested) interrupt();
  }).catch(fail);
  return completion;
}
