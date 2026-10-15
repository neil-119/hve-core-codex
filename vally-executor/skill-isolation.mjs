import { AppServerTransport } from './app-server.mjs';
import { readFile, realpath } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';

const MAX_SKILLS = 512;
const MAX_MCP_SERVERS = 256;
const MAX_MCP_PAGES = 16;
const MCP_PAGE_SIZE = 64;
const MAX_OVERRIDE_CHARS = 24 * 1024;
const CLEANUP_TIMEOUT_MS = 2_500;
const MCP_SERVER_NAME = /^[A-Za-z0-9_-]{1,128}$/;
// Vally stages its selected skills itself. Account plugins, connected apps,
// and lifecycle hooks belong to separate native host acceptance checks.
// These are process-local overrides, not changes to the user's configuration.
export const VALLY_AMBIENT_CONFIG_ARGS = Object.freeze([
  '-c', 'features.apps=false', '-c', 'features.plugins=false', '-c', 'features.hooks=false',
]);

function pathKey(value) {
  const normalized = resolve(value).replaceAll('\\', '/').replace(/\/$/, '');
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

function isWithin(parent, candidate) {
  const rel = relative(parent, candidate);
  return rel === '' || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`));
}

function remaining(deadline, label) {
  const value = deadline - Date.now();
  if (value <= 0) throw new Error(`Skill isolation hard deadline exhausted during ${label}`);
  return value;
}

function withinDeadline(operation, deadline, label) {
  const timeoutMs = remaining(deadline, label);
  let timer;
  return Promise.race([
    // Check the remaining budget before creating a request. Creating it first
    // can leave an unobserved rejection when cleanup cancels an expired probe.
    Promise.resolve().then(operation),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Skill isolation hard deadline exhausted during ${label}`)), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function readExpectedSkills(workDir, skills) {
  if (!Array.isArray(skills)) throw new Error('Vally skills must be an array');
  if (skills.length > MAX_SKILLS) throw new Error(`Vally skill count exceeds ${MAX_SKILLS}`);
  const root = resolve(workDir);
  const skillsRoot = join(root, '.agents', 'skills');
  const realWorkDir = await realpath(root)
    .catch(() => { throw new Error('Vally workspace is unavailable for skill isolation'); });
  let realSkillsRoot;
  if (skills.length) {
    realSkillsRoot = await realpath(skillsRoot)
      .catch(() => { throw new Error('Vally did not stage its selected skills under .agents/skills'); });
    if (!isWithin(realWorkDir, realSkillsRoot))
      throw new Error('Vally skill discovery root resolves outside its workspace');
  }

  const expected = [];
  const seen = new Set();
  for (const skill of skills) {
    if (!skill || typeof skill.name !== 'string' || !/^[A-Za-z0-9_-]+$/.test(skill.name) ||
        typeof skill.rawContent !== 'string')
      throw new Error('Vally selected skill metadata is invalid');
    const path = join(skillsRoot, skill.name, 'SKILL.md');
    const key = pathKey(path);
    if (seen.has(key)) throw new Error(`Vally selected a duplicate skill path: ${skill.name}`);
    seen.add(key);
    const realSkillDir = await realpath(join(skillsRoot, skill.name))
      .catch(() => { throw new Error(`Vally skill ${skill.name} is not staged in Codex's .agents/skills discovery tree`); });
    const realSkillPath = await realpath(path)
      .catch(() => { throw new Error(`Vally skill ${skill.name} is missing SKILL.md`); });
    if (!isWithin(realSkillsRoot, realSkillDir) || !isWithin(realSkillDir, realSkillPath))
      throw new Error(`Vally skill ${skill.name} resolves outside its staged skill directory`);
    const content = await readFile(realSkillPath, 'utf8');
    if (content !== skill.rawContent) throw new Error(`Codex staged content disagrees with Vally skill ${skill.name}`);
    expected.push({ name: skill.name, path, key });
  }
  return expected;
}

function parseSkillList(response, cwd, label) {
  if (!response || !Array.isArray(response.data) || response.data.length !== 1)
    throw new Error(`${label}: skills/list must return exactly one workspace entry`);
  const entry = response.data[0];
  if (!entry || typeof entry.cwd !== 'string' || pathKey(entry.cwd) !== pathKey(cwd))
    throw new Error(`${label}: skills/list returned the wrong workspace`);
  if (!Array.isArray(entry.errors) || entry.errors.length)
    throw new Error(`${label}: skill discovery reported errors`);
  if (!Array.isArray(entry.skills) || entry.skills.length > MAX_SKILLS)
    throw new Error(`${label}: discovered skill list is invalid or exceeds ${MAX_SKILLS}`);
  const seen = new Set();
  return entry.skills.map(skill => {
    if (!skill || typeof skill.name !== 'string' || !skill.name || typeof skill.path !== 'string' ||
        !isAbsolute(skill.path) || typeof skill.enabled !== 'boolean' ||
        !['user', 'repo', 'system', 'admin'].includes(skill.scope))
      throw new Error(`${label}: discovered skill metadata is invalid`);
    const key = pathKey(skill.path);
    if (seen.has(key)) throw new Error(`${label}: duplicate discovered skill path: ${skill.path}`);
    seen.add(key);
    return { name: skill.name, path: resolve(skill.path), key, enabled: skill.enabled, scope: skill.scope };
  });
}

function parseMcpPage(response, label, remainingServerCapacity) {
  if (!response || typeof response !== 'object' || Array.isArray(response) || !Array.isArray(response.data))
    throw new Error(`${label}: mcpServerStatus/list must return a data array`);
  if (response.data.length > remainingServerCapacity)
    throw new Error(`${label}: MCP server catalog exceeds ${MAX_MCP_SERVERS}`);
  const servers = response.data.map(server => {
    if (!server || typeof server.name !== 'string' || !MCP_SERVER_NAME.test(server.name))
      throw new Error(`${label}: MCP server name is missing or invalid`);
    if (!server.tools || typeof server.tools !== 'object' || Array.isArray(server.tools))
      throw new Error(`${label}: MCP server ${server.name} tools must be a dictionary`);
    const toolsPrototype = Object.getPrototypeOf(server.tools);
    if (toolsPrototype !== Object.prototype && toolsPrototype !== null)
      throw new Error(`${label}: MCP server ${server.name} tools must be a dictionary`);
    return { name: server.name, toolCount: Object.keys(server.tools).length };
  });
  const nextCursor = response.nextCursor;
  if (nextCursor !== undefined && nextCursor !== null &&
      (typeof nextCursor !== 'string' || !nextCursor.length))
    throw new Error(`${label}: MCP catalog cursor is malformed`);
  return { servers, nextCursor: nextCursor ?? null };
}

async function listMcpInventory(client, deadline, label) {
  const servers = [];
  const seenNames = new Set();
  const seenCursors = new Set();
  let cursor;
  for (let page = 0; page < MAX_MCP_PAGES; page++) {
    const params = { limit: MCP_PAGE_SIZE, detail: 'toolsAndAuthOnly', ...(cursor ? { cursor } : {}) };
    const response = await withinDeadline(() => client.request('mcpServerStatus/list', params), deadline,
      `${label} mcpServerStatus/list page ${page + 1}`);
    const parsed = parseMcpPage(response, `${label} page ${page + 1}`, MAX_MCP_SERVERS - servers.length);
    for (const server of parsed.servers) {
      if (seenNames.has(server.name))
        throw new Error(`${label}: duplicate MCP server name: ${server.name}`);
      seenNames.add(server.name);
      servers.push(server);
      if (servers.length > MAX_MCP_SERVERS)
        throw new Error(`${label}: MCP server catalog exceeds ${MAX_MCP_SERVERS}`);
    }
    if (parsed.nextCursor === null) return servers;
    if (seenCursors.has(parsed.nextCursor))
      throw new Error(`${label}: MCP catalog repeated a pagination cursor`);
    seenCursors.add(parsed.nextCursor);
    if (page === MAX_MCP_PAGES - 1)
      throw new Error(`${label}: MCP catalog pagination exceeds ${MAX_MCP_PAGES} pages`);
    cursor = parsed.nextCursor;
  }
  throw new Error(`${label}: MCP catalog pagination exceeds ${MAX_MCP_PAGES} pages`);
}

function tomlString(value) {
  // JSON quoted strings use the same escapes needed by TOML basic strings.
  return JSON.stringify(value.replaceAll('\\', '/'));
}

function makeConfigArgs(skills, expectedKeys, mcpServers) {
  const ordered = [...skills].sort((a, b) => a.key.localeCompare(b.key));
  const array = `skills.config=[${ordered.map(skill =>
    `{path=${tomlString(skill.path)},enabled=${expectedKeys.has(skill.key)}}`).join(',')}]`;
  if (array.length > MAX_OVERRIDE_CHARS)
    throw new Error(`Skill isolation config exceeds ${MAX_OVERRIDE_CHARS} characters`);
  const mcpOverrides = mcpServers.flatMap(server => ['-c', `mcp_servers.${server.name}.enabled=false`]);
  return ['-c', array, '-c', 'project_doc_max_bytes=0', ...VALLY_AMBIENT_CONFIG_ARGS, ...mcpOverrides];
}

function defaultClientFactory(binary, configArgs, { cwd, env = {} }) {
  return AppServerTransport.spawn(binary, ['app-server', '--stdio', ...configArgs], {
    spawnOptions: { cwd, env: { ...process.env, ...env } },
    transportOptions: { shutdownGraceMs: 150, forceKillGraceMs: 1_000 },
  });
}

async function listWithClient(clientFactory, binary, configArgs, cwd, deadline, label, env) {
  let client;
  let result;
  let operationError;
  let cleanupError;
  try {
    client = clientFactory(binary, configArgs, { cwd, env });
    if (!client || typeof client.initialize !== 'function' || typeof client.request !== 'function' ||
        typeof client.shutdown !== 'function')
      throw new Error(`${label}: app-server probe client is incomplete`);
    await withinDeadline(() => client.initialize({
      clientInfo: { name: 'hve-vally-skill-isolation', version: '0.1.0' },
      capabilities: { experimentalApi: true },
    }), deadline, `${label} initialize`);
    const skills = await withinDeadline(() => client.request('skills/list', { cwds: [cwd], forceReload: true }), deadline,
      `${label} skills/list`);
    const mcpServers = await listMcpInventory(client, deadline, label);
    result = { skills, mcpServers };
  } catch (error) {
    operationError = error;
  } finally {
    if (client) {
      try {
        let timer;
        await Promise.race([
          client.shutdown(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Skill isolation app-server cleanup timed out')), CLEANUP_TIMEOUT_MS); }),
        ]).finally(() => clearTimeout(timer));
      } catch (error) { cleanupError = error; }
    }
  }
  if (operationError && cleanupError)
    throw new AggregateError([operationError, cleanupError], `${label} failed and its owned app-server cleanup was unconfirmed`);
  if (cleanupError) throw new Error(`${label} app-server cleanup failed: ${cleanupError.message}`);
  if (operationError) throw operationError;
  remaining(deadline, `${label} completion`);
  return result;
}

/**
 * Discover available Codex skills and configured MCP servers, apply a
 * process-local skill allowlist and MCP disable overrides, then verify both
 * native catalogs. This is inventory-level isolation evidence only; it does
 * not establish an exclusive runtime tool inventory.
 */
export async function resolveSkillIsolation({
  binary = 'codex', workDir, skills = [], timeoutMs, env = {}, clientFactory = defaultClientFactory,
} = {}) {
  if (typeof workDir !== 'string' || !workDir) throw new Error('Skill isolation requires a workspace directory');
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('Skill isolation requires a positive hard deadline');
  if (!env || typeof env !== 'object' || Array.isArray(env)) throw new Error('Skill isolation environment must be an object');
  const deadline = Date.now() + timeoutMs;
  const cwd = resolve(workDir);
  const expected = await readExpectedSkills(cwd, skills);
  const expectedKeys = new Set(expected.map(skill => skill.key));
  const beforeResponse = await listWithClient(clientFactory, binary, [...VALLY_AMBIENT_CONFIG_ARGS], cwd, deadline, 'Initial skill discovery', env);
  const before = parseSkillList(beforeResponse.skills, cwd, 'Initial skill discovery');
  const beforeMcp = beforeResponse.mcpServers;
  const beforeByPath = new Map(before.map(skill => [skill.key, skill]));
  const missingExpected = expected.filter(skill => !beforeByPath.has(skill.key));
  if (missingExpected.length)
    throw new Error(`Selected Vally skill paths were not discovered: ${missingExpected.map(skill => skill.name).join(', ')}`);
  const configArgs = makeConfigArgs(before, expectedKeys, beforeMcp);
  const verifiedResponse = await listWithClient(clientFactory, binary, configArgs, cwd, deadline, 'Skill isolation verification', env);
  const verified = parseSkillList(verifiedResponse.skills, cwd, 'Skill isolation verification');
  if (verified.length !== before.length) throw new Error('Skill catalog changed while applying the process-local isolation config');
  const verifiedByPath = new Map(verified.map(skill => [skill.key, skill]));
  for (const skill of before) {
    const actual = verifiedByPath.get(skill.key);
    if (!actual || actual.name !== skill.name || actual.scope !== skill.scope)
      throw new Error(`Skill catalog changed while applying the process-local isolation config: ${skill.path}`);
    const shouldEnable = expectedKeys.has(skill.key);
    if (actual.enabled !== shouldEnable)
      throw new Error(`Skill isolation mismatch for ${skill.path}: expected enabled=${shouldEnable}, found ${actual.enabled}`);
  }
  const enabledPaths = verified.filter(skill => skill.enabled).map(skill => skill.path);
  if (enabledPaths.length !== expected.length ||
      enabledPaths.some(path => !expectedKeys.has(pathKey(path))))
    throw new Error('Skill isolation did not enable exactly the selected Vally skill paths');
  const verifiedMcp = verifiedResponse.mcpServers;
  const beforeMcpNames = beforeMcp.map(server => server.name).sort();
  const verifiedMcpNames = verifiedMcp.map(server => server.name).sort();
  if (verifiedMcpNames.length !== beforeMcpNames.length ||
      verifiedMcpNames.some((name, index) => name !== beforeMcpNames[index]))
    throw new Error('MCP server catalog changed while applying the process-local isolation config');
  const remainingMcpTools = verifiedMcp.reduce((total, server) => total + server.toolCount, 0);
  if (remainingMcpTools !== 0)
    throw new Error(`MCP isolation left ${remainingMcpTools} advertised tool(s) enabled`);
  remaining(deadline, 'final verification');
  return {
    configArgs,
    expectedPaths: expected.map(skill => skill.path),
    enabledPaths,
    disabledSkills: verified.length - enabledPaths.length,
    mcpIsolation: {
      verified: true,
      disabledServerNames: beforeMcp.map(server => server.name),
      advertisedToolCount: remainingMcpTools,
    },
    exclusiveToolInventoryVerified: false,
    elapsedMs: timeoutMs - Math.max(0, deadline - Date.now()),
  };
}
